## 项目概述

**智能教务文档填写系统** — 基于 LLM + LangGraph 的多 Agent 协作式 Word 文档自动填写系统。用户上传教务模板，通过对话或上传知识文件即可自动填充字段，保留原始格式并生成可下载的 docx 文件。

**当前架构**：StateGraph 3 节点流水线（知识提取 Agent → 填充 Agent → 生成 Agent），每个 Agent 独立配置 system_prompt 和工具集，输出带标签（`[知识提取Agent]`/`[填充Agent]`/`[生成Agent]`）。

## 技术栈

- **后端**：Python 3.12 / FastAPI / LangChain 1.0 / LangGraph 1.0
- **前端**：原生 HTML + CSS + JavaScript（单文件 SPA，位于 `web/index.html`）
- **模型**：豆包 Seed（通过 OpenAI 兼容接口）
- **文档处理**：python-docx / OpenXML
- **包管理**：uv（Python），禁止使用 pip
- **存储**：S3 兼容对象存储 / 内存 Checkpointer

## 目录结构

```
projects/                     # 技术项目根目录
├── config/
│   └── agent_llm_config.json # Agent 模型配置 + 系统提示词 + 工具注册
├── docs/
│   └── ARCHITECTURE.md       # 项目架构文档
├── scripts/                  # 启动/构建脚本
│   ├── coze-preview-build.sh # 预览构建（uv sync）
│   ├── coze-preview-run.sh   # 预览运行（端口 5000）
│   ├── setup.sh              # 部署构建（依赖安装）
│   ├── http_run.sh           # 部署运行（端口 5000）
│   └── local_run.sh          # 本地开发运行
├── assets/                   # 模板文件 + 静态资源
├── src/
│   ├── main.py               # Web 服务入口（FastAPI）
│   ├── agents/agent.py       # Agent 主逻辑（StateGraph 3 节点流水线）
│   ├── tools/                # 工具模块（模板解析、文档生成、校验等）
│   ├── storage/              # 存储层（记忆持久化）
│   └── utils/                # 工具函数
├── web/
│   └── index.html            # 前端 SPA（单文件，~99KB）
├── pyproject.toml            # 项目配置 + 依赖声明
├── requirements.txt          # 冻结依赖清单
├── MIGRATION_GUIDE.md        # 迁移指南（给新 AI 助手）
├── local_debug.py            # 本地命令行调试入口
└── test_simple.py            # 简单测试脚本
```

## 关键入口 / 核心模块

- **服务入口**：`src/main.py`，通过 `python src/main.py -m http -p <port>` 启动
- **Agent 逻辑**：`src/agents/agent.py`，LangGraph StateGraph 3 节点流水线（知识提取→填充→生成）
- **Agent 配置**：`config/agent_llm_config.json`，含 `knowledge_sp`/`filling_sp`/`generation_sp` 三个独立 system_prompt
- **模板解析引擎**：`src/tools/template_analyzer.py`
- **文档生成引擎**：`src/tools/edu_report_tool.py`
- **API 接口**：`/run`（同步）、`/stream_run`（SSE 流式）、`/upload`、`/prefill`、`/template-preview`、`/download-docx` 等

## 运行与预览

- **预览**（dev）：`bash scripts/coze-preview-run.sh`，绑定 `0.0.0.0:5000`
- **部署**（deploy）：`bash scripts/http_run.sh -p 5000`，绑定 `0.0.0.0:5000`
- **依赖安装**：`uv sync`
- 前端挂载路径：`/web/`

## 根 .coze 与子项目 .coze 映射

- **工作区根**：`/workspace/projects/`
- **技术项目根**：`/workspace/projects/projects/`
- **根 .coze**：`/workspace/projects/.coze`（平台读取的唯一入口，[dev]/[deploy] 通过 `projects/scripts/` 指向子项目脚本）
- **子项目 .coze**：`/workspace/projects/projects/.coze`
- 两文件 `project_type` 均为 `"web"`，`preview_enable` 均为 `"enabled"`

## 已知问题与注意事项

1. **cozeloop 兼容层**：`cozeloop` v0.1.x 使用了 LangChain 旧版本导入路径（`langchain.callbacks.base`、`langchain.schema`），已在系统 `dist-packages` 添加兼容 shim。如果迁移到新版本 cozeloop，需移除这些 shim。
2. **无用系统依赖已移除**：`pycairo==1.29.0`、`dbus-python==1.3.2`、`PyGObject==3.48.2` 在源码中未被引用，已从 `pyproject.toml` 和 `requirements.txt` 中移除。
3. **前端挂载**：`main.py` 中前端静态文件通过双路径候选挂载（优先项目根下的 `web/`，回退到 `COZE_WORKSPACE_PATH/web`）。
4. **预览脚本已更新 lock 文件**：`coze-preview-build.sh` 使用 `uv sync`（非 `--frozen`）以允许 lock 文件更新。
5. **模板路径解析**：`edu_report_tool.py` 中 `generate_from_template` 和 `analyze_uploaded_template` 已修复为使用 `_resolve_template_path`，支持从 `assets/` 子目录查找模板文件。
6. **MultiAgentState**：`agent.py` 中 `MultiAgentState` 需包含 `remaining_steps` 字段以满足 LangGraph 1.0 的 `create_react_agent` 要求。
7. **Agent 中间件**：使用 `AgentMiddleware` 子类时需同时实现 `wrap_tool_call`（同步）和 `awrap_tool_call`（异步）方法。

## 源码仓库

- **GitHub**：`https://github.com/CurtainXxxx/doc-auto-fillter`
- **当前分支**：`feature/v2-fill-enhance-v2`
- **最新提交**：`90259b7` — fix: 打印CSS补全

## 下一步开发计划

1. ~~**多 Agent 架构**~~ ✅ 已完成：StateGraph 3 节点流水线（知识提取 → 填充 → 生成），见 `src/agents/agent.py`
2. **通用模板引擎重构**（`docs/refactor_plan.md`）：field_id 为主、label 为兼容、事实提取分离、生成后校验

## 多 Agent 架构详情

```
START → knowledge_extraction（知识提取Agent）→ filling（填充Agent）→ generation（生成Agent）→ END
```

| Agent | 工具数 | 核心职责 |
|---|---|---|
| 知识提取 Agent | 5 | 旧报告提取、知识文件解析、事实提取、预填、数据准备清单 |
| 填充 Agent | 8 | 模板分析、表单初始化、字段更新、AI预填、状态查询 |
| 生成 Agent | 4 | 表单文档生成、内置模板生成、上传模板生成、模板分析 |

每个 Agent 输出带标签（`[知识提取Agent]`/`[填充Agent]`/`[生成Agent]`），评审可直观看到多 Agent 协作过程。

### 测试结果（2026-06-05）

使用 `教材建设申报_【简单】.txt` 知识文件 + `教材建设申报书.docx` 模板完整测试通过：

| 阶段 | 状态 | 详情 |
|------|------|------|
| 知识提取 Agent | ✅ | 提取 15 条事实（申报人、教材、项目描述等） |
| 填充 Agent | ✅ | 识别 29 字段，14 条自动匹配（48.3%），剩余由 AI 合理补全 |
| 生成 Agent | ✅ | 100% 填写率，7 项校验全通过，生成 docx 并上传对象存储 |

## 用户偏好与长期约束

- Python 3.12，使用 `uv` 管理依赖
- 预览端口固定 5000，监听 `0.0.0.0`
- 禁止使用 9000 端口
- Node.js 项目仅允许 `pnpm`