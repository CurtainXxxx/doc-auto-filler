"""
LangGraph Checkpointer 统一管理模块。

核心职责：
1. 为 LangGraph 提供跨对话的消息持久化能力（PostgreSQL）
2. 数据库不可用时的自动降级（MemorySaver，重启后丢失）
3. 单例模式保证全局只维护一个连接池

策略：PostgresSaver ≥ AsyncPostgresSaver > MemorySaver（兜底）
- db_url 为空或连接失败 → 静默降级为 MemorySaver（不阻塞启动）
- 连接池初始化失败 → 降级为 MemorySaver
"""

import psycopg
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver
from typing import Optional, Union
import logging
import time

logger = logging.getLogger(__name__)

# 数据库连接超时时间（秒），每次尝试 15 秒，共尝试 2 次
DB_CONNECTION_TIMEOUT = 15
DB_MAX_RETRIES = 2


class MemoryManager:
    """Checkpointer 管理器（单例）。

    按优先级尝试以下策略：
    1. AsyncPostgresSaver + 连接池（推荐，生产环境）
    2. MemorySaver（兜底，数据不跨重启持久化）

    所有 get_checkpointer() 调用者无需关心底层实现，
    由本类统一处理连接、重试和降级。
    """

    _instance: Optional['MemoryManager'] = None
    _checkpointer: Optional[Union[AsyncPostgresSaver, MemorySaver]] = None
    _pool: Optional[AsyncConnectionPool] = None
    _setup_done: bool = False

    def __new__(cls):
        """单例保证：整个进程内只维护一个 MemoryManager 实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _connect_with_retry(self, db_url: str) -> Optional[psycopg.Connection]:
        """带重试的数据库同步连接，用于 schema 初始化。

        Args:
            db_url: PostgreSQL 连接字符串

        Returns:
            成功返回 psycopg.Connection，失败返回 None
        """
        last_error = None
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                logger.info(f"Attempting database connection (attempt {attempt}/{DB_MAX_RETRIES})")
                conn = psycopg.connect(db_url, autocommit=True, connect_timeout=DB_CONNECTION_TIMEOUT)
                logger.info(f"Database connection established on attempt {attempt}")
                return conn
            except Exception as e:
                last_error = e
                logger.warning(f"Database connection attempt {attempt} failed: {e}")
                if attempt < DB_MAX_RETRIES:
                    time.sleep(1)
        logger.error(f"All {DB_MAX_RETRIES} database connection attempts failed, last error: {last_error}")
        return None

    def _setup_schema_and_tables(self, db_url: str) -> bool:
        """创建 memory schema 和 LangGraph checkpoint 表。

        只执行一次（_setup_done 标记），后续调用直接返回 True。
        使用 PostgresSaver.setup() 创建 checkpoints / checkpoint_blobs / writes 三张表。

        Args:
            db_url: PostgreSQL 连接字符串

        Returns:
            bool: schema 创建成功或已存在
        """
        if self._setup_done:
            return True

        conn = self._connect_with_retry(db_url)
        if conn is None:
            return False

        try:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS memory")
            conn.execute("SET search_path TO memory")
            PostgresSaver(conn).setup()
            self._setup_done = True
            logger.info("Memory schema and tables created")
            return True
        except Exception as e:
            logger.warning(f"Failed to setup schema/tables: {e}")
            return False
        finally:
            conn.close()

    def _get_db_url_safe(self) -> Optional[str]:
        """从环境变量安全获取数据库连接字符串。

        优先从 PGDATABASE_URL 环境变量读取；
        失败时尝试从 coze_workload_identity 获取项目级配置。

        Returns:
            str | None: db_url 或 None（获取失败/为空）
        """
        try:
            from storage.database.db import get_db_url
            db_url = get_db_url()
            if db_url and db_url.strip():
                return db_url
            logger.warning("db_url is empty, will fallback to MemorySaver")
            return None
        except Exception as e:
            logger.warning(f"Failed to get db_url: {e}, will fallback to MemorySaver")
            return None

    def _create_fallback_checkpointer(self) -> MemorySaver:
        """创建内存兜底 checkpointer（数据重启即失）。

        当 db_url 不可用或 PostgreSQL 连接失败时调用，
        确保系统不因数据库问题而无法启动。
        """
        self._checkpointer = MemorySaver()
        logger.warning("Using MemorySaver as fallback checkpointer (data will not persist across restarts)")
        return self._checkpointer

    def get_checkpointer(self) -> BaseCheckpointSaver:
        """获取 checkpointer（按优先级尝试，自动降级）。

        优先级：
        1. 已缓存的 checkpointer → 直接返回
        2. PostgreSQL（AsyncPostgresSaver + 连接池）→ 连接状态持久化
        3. MemorySaver（兜底）→ 无持久化

        Returns:
            BaseCheckpointSaver: 可用的 checkpointer 实例
        """
        if self._checkpointer is not None:
            return self._checkpointer

        # 1. 尝试获取 db_url
        db_url = self._get_db_url_safe()
        if not db_url:
            return self._create_fallback_checkpointer()

        # 2. 尝试连接数据库并创建 schema/表（带重试）
        if not self._setup_schema_and_tables(db_url):
            return self._create_fallback_checkpointer()

        # 3. 连接字符串加上 search_path 让连接指向 memory schema
        if "?" in db_url:
            db_url = f"{db_url}&options=-csearch_path%3Dmemory"
        else:
            db_url = f"{db_url}?options=-csearch_path%3Dmemory"

        # 4. 尝试创建连接池和 checkpointer
        try:
            self._pool = AsyncConnectionPool(
                conninfo=db_url,
                timeout=DB_CONNECTION_TIMEOUT,
                min_size=1,
                max_idle=300,
                check=AsyncConnectionPool.check_connection,
            )
            self._checkpointer = AsyncPostgresSaver(self._pool)
            logger.info("AsyncPostgresSaver initialized successfully")
        except Exception as e:
            logger.warning(f"Failed to create AsyncPostgresSaver: {e}, will fallback to MemorySaver")
            return self._create_fallback_checkpointer()

        return self._checkpointer


_memory_manager: Optional[MemoryManager] = None


def get_memory_saver() -> BaseCheckpointSaver:
    """获取 checkpointer，优先使用 PostgresSaver，db_url 不可用或连接失败时退化为 MemorySaver"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager.get_checkpointer()