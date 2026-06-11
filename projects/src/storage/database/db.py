"""
SQLAlchemy 数据库引擎管理模块。

职责：
1. 从环境变量或 Coze Workload Identity 获取 PGDATABASE_URL
2. 创建带重试机制的 SQLAlchemy 引擎
3. 提供 sessionmaker / session 工厂方法

注意：本模块用于可直接使用 SQLAlchemy 的场景；
LangGraph Checkpointer 使用独立的 psycopg 连接（见 memory_saver.py）。
"""

import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
import logging
logger = logging.getLogger(__name__)

# 引擎连接验证最大重试时间（秒）
MAX_RETRY_TIME = 20

# 优先从 .env 加载环境变量（仅当文件存在时）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def get_db_url() -> str:
    """获取 PostgreSQL 数据库连接字符串。

    优先级：
    1. PGDATABASE_URL 环境变量（部署时由平台注入）
    2. coze_workload_identity（项目级配置，开发环境）

    Returns:
        str: 数据库连接 URL

    Raises:
        Exception: 两种来源均无法获取时抛出
    """
    url = os.getenv("PGDATABASE_URL") or ""
    if url is not None and url != "":
        return url
    from coze_workload_identity import Client
    try:
        client = Client()
        env_vars = client.get_project_env_vars()
        client.close()
        for env_var in env_vars:
            if env_var.key == "PGDATABASE_URL":
                url = env_var.value.replace("'", "'\\''")
                return url
    except Exception as e:
        logger.error(f"Error loading PGDATABASE_URL: {e}")
        raise e
    finally:
        if url is None or url == "":
            logger.error("PGDATABASE_URL is not set")
    return url


# 全局单例缓存
_engine = None
_SessionLocal = None


def _create_engine_with_retry():
    """创建 SQLAlchemy 引擎，带连接验证重试。

    配置说明：
    - pool_size=100, max_overflow=100：高并发场景避免频繁创建连接
    - pool_pre_ping=True：每次从池取连接时发 SELECT 1 验证有效性
    - pool_recycle=1800：30 分钟回收连接，防 Proxy 断开

    Returns:
        Engine: 已验证可用的 SQLAlchemy 引擎

    Raises:
        OperationalError: MAX_RETRY_TIME 秒内无法建立连接时抛出
    """
    url = get_db_url()
    if url is None or url == "":
        logger.error("PGDATABASE_URL is not set")
        raise ValueError("PGDATABASE_URL is not set")
    size = 100
    overflow = 100
    recycle = 1800
    timeout = 30
    engine = create_engine(
        url,
        pool_size=size,
        max_overflow=overflow,
        pool_pre_ping=True,
        pool_recycle=recycle,
        pool_timeout=timeout,
    )
    # 用 SELECT 1 验证连接是否可用，失败则重试直到超时
    start_time = time.time()
    last_error = None
    while time.time() - start_time < MAX_RETRY_TIME:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except OperationalError as e:
            last_error = e
            elapsed = time.time() - start_time
            logger.warning(f"Database connection failed, retrying... (elapsed: {elapsed:.1f}s)")
            time.sleep(min(1, MAX_RETRY_TIME - elapsed))
    logger.error(f"Database connection failed after {MAX_RETRY_TIME}s: {last_error}")
    raise last_error  # pyright: ignore [reportGeneralTypeIssues]


def get_engine():
    """获取全局 SQLAlchemy 引擎（懒加载单例）。"""
    global _engine
    if _engine is None:
        _engine = _create_engine_with_retry()
    return _engine


def get_sessionmaker():
    """获取全局 sessionmaker（懒加载单例）。"""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_session():
    """获取一个新的数据库会话。"""
    return get_sessionmaker()()


__all__ = [
    "get_db_url",
    "get_engine",
    "get_sessionmaker",
    "get_session",
]
