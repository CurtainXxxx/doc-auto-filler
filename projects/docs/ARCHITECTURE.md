# 格智 FormWise 智能填表系统 — 架构文档

## 1. 系统概述

基于 LLM + LangGraph 的多 Agent 协作式 Word 文档自动填写系统。用户上传教务模板，通过对话或上传知识文件即可自动填充字段，保留原始格式并生成可下载的 docx 文件。

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    用户界面（SPA）                           │
│                  web/index.html (2942行)                    │
│          聊天区 + 模板预览 + 字段编辑 + 文档下载             │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼──────────────────────────────────┐
│                  Web 服务层（FastAPI）                       │
│                  src/main.py (1081行)                       │
│    /upload /upload-template /prefill /run /stream_run       │
│    /cofill-generate /v1/chat/completions /download-docx     │
│    /template-preview ...                                    │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│              Agent 编排层（LangGraph StateGraph）            │
│                  src/agents/agent.py (451行)                │
│                                                             │
│   START → Router → (条件路由) → Agent节点 → END             │
│                                                             │
│   ┌────────────────┐  ┌──────────────┐  ┌──────────────┐   │
│   │ 知识提取 Agent │  │  填充 Agent  │  │  生成 Agent  │   │
│   │   (5个工具)    │→ │   (8个工具)  │→ │   (3个工具)  │   │
│   └────────────────┘  └──────────────┘  └──────────────┘   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   工具层（@tool 函数）                        │
│                                                             │
│  edu_report_tool.py (599行)      filling/ 子模块 (1911行)    │
│  template_analyzer.py (1416行)   prefill_tool.py (1034行)   │
│  knowledge_tool.py (595行)       old_report_extractor.py    │
│  docx_validator.py (739行)       docx_preview.py (487行)    │
│  docx_upload.py (255行)          error_handler.py (186行)   │
│                                                             │
│  工具总数：16 个 @tool（3 个 Agent 按需分配）                │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    存储层                                     │
│  memory/memory_saver.py (194行)  — 对话历史持久化            │
│  database/db.py (144行)          — SQLite 关系型存储         │
│  docx_upload.py                  — S3 对象存储              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 多 Agent 协作流水线

### 3.1 核心流程

```
用户输入 → Router → knowledge_extraction → Router → filling → Router → generation → END
```

Router 机制：根据对话中是否已有 `[FACTS]`、`[FIELDS]`、`[生成完成]` 标记，跳过已完成的阶段，只调用需要的 Agent。

### 3.2 三个 Agent 分工

| Agent | 工具数 | 核心职责 |
|-------|--------|----------|
| **知识提取 Agent** | 5 | 旧报告提取、知识文件解析、事实提取、预填、数据准备清单 |
| **填充 Agent** | 8 | 模板分析、表单初始化、字段更新、AI 预填、状态查询 |
| **生成 Agent** | 3 | 表单文档生成、内置模板生成、上传模板生成 |

### 3.3 阶段检测

每个 Agent 的 system_prompt 包含阶段完成标记指令（`[知识提取完成]` / `[填充完成]` / `[生成完成]`）。Router 函数通过正则匹配这些标记来决定流程走向。

---

## 4. 关键组件

### 4.1 模板解析引擎（`template_analyzer.py` — 1416 行）

5 层扫描算法：
1. 冒号字段检测（`label:` 模式）
2. 段落下划线字段检测（下划线 run 组拆分）
3. 勾选框检测（□ 符号 + 选项词）
4. 行组检测（表格行分组）
5. 标签-blank 单元格检测

关键特性：
- 多标签段落检测：同一段落中多个非下划线 run 组拆分为独立字段
- 行首标签 section context：优先使用当前行首个单元格内容作为上下文
- 合并单元格处理：XML 级 vMerge 保护

### 4.2 文档填充引擎（`filling/` 子模块 — 1911 行）

| 文件 | 行数 | 职责 |
|------|------|------|
| `base.py` | 289 | 底层 XML 操作（单元格、行、vMerge） |
| `token_style.py` | 318 | 词元级样式继承 v2（run 定位 + 3 模式策略） |
| `colon_filler.py` | 195 | 冒号模式 + 签名/日期填充 |
| `checkbox_filler.py` | 216 | 勾选框检测（两遍扫描：选项词 + □N.） |
| `paragraph_filler.py` | 91 | 段落下划线字段填充 |
| `row_group_filler.py` | 164 | 行组（T0_G0）填充 + multi_col |
| `data_expander.py` | 189 | 通用模板数据预处理 |
| `builtin_expander.py` | 241 | 内置模板数据预处理（考勤/分数段） |
| `doc_builder.py` | 153 | 生成编排（build_report_docx / fill_custom_template） |
| `postprocess.py` | 55 | 合并单元格修复（_fix_merged_cells） |

### 4.3 AI 预填流程（`prefill_tool.py` — 1034 行）

工作流：
1. 接收模板分析结果（字段清单）
2. 读取知识文件内容
3. 调用 LLM 逐字段匹配值
4. 返回三色置信度标记 + 填充率统计

三色分类（cofill 模式）：

| 颜色 | 含义 | 字段示例 |
|------|------|----------|
| 🟢 绿色 | 已确认填充 | 教材名称、主编姓名 |
| 🟡 黄色 | 待人工确认 | 内容简介（AI 不确定） |
| ⚪ 灰色 | 未匹配到数据 | 出版社名称（知识里没有） |

---

## 5. API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/upload` | POST | 上传知识文件 |
| `/upload-template` | POST | 上传模板文件 |
| `/prefill` | POST | AI 预填字段 |
| `/cofill-generate` | POST | 人机协同生成文档 |
| `/cofill-fast-generate` | POST | 快速生成（跳过分析） |
| `/run` | POST | Agent 同步运行 |
| `/stream_run` | POST | Agent SSE 流式运行 |
| `/v1/chat/completions` | POST | OpenAI 兼容接口 |
| `/download-docx` | GET | 下载生成的文档 |
| `/template-preview` | GET | 模板 HTML 预览 |
| `/templates` | GET | 内置模板列表 |
| `/docx-download-url` | GET | 获取文档下载地址 |

---

## 6. 状态管理

### 6.1 Agent 状态（`agent.py`）

```
MultiAgentState(MessagesState):
  - messages: List[BaseMessage]    # 对话历史
  - remaining_steps: int           # 剩余步骤控制
```

### 6.2 表单状态（`FormFillingState`）

```
FormFillingState:
  - label_fields: List[Dict]       # 字段清单
  - row_groups: List[Dict]         # 行组信息
  - field_values: Dict[str, str]   # 字段值
```

### 6.3 记忆持久化（`memory_saver.py`）

基于文件的 Checkpointer，支持：
- 多 session 隔离（通过 sessionId）
- 消息历史持久化
- 降级回退策略

---

## 7. 代码规模

| 模块 | 文件 | 行数 |
|------|------|------|
| Web 服务入口 | `main.py` | 1,081 |
| Agent 编排 | `agent.py` | 451 |
| **工具层合计** | **16 个文件** | **7,911** |
| ├─ 模板分析 | `template_analyzer.py` | 1,416 |
| ├─ AI 预填 | `prefill_tool.py` | 1,034 |
| ├─ 文档填充子模块 | `filling/` (10 文件) | 1,911 |
| ├─ 文档校验 | `docx_validator.py` | 739 |
| ├─ 旧报告提取 | `old_report_extractor.py` | 609 |
| ├─ 知识工具 | `knowledge_tool.py` | 595 |
| ├─ 文档工具 | `edu_report_tool.py` | 599 |
| ├─ 文档预览 | `docx_preview.py` | 487 |
| ├─ 上传服务 | `docx_upload.py` | 255 |
| └─ 错误处理 | `error_handler.py` | 186 |
| 前端 SPA | `index.html` | 2,942 |
| 存储层 | `memory_saver.py` + `db.py` | 338 |
| **合计** | **20 个核心文件** | **~12,723** |

---

## 8. 技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | Python 3.12 / FastAPI |
| AI 框架 | LangChain 1.0 / LangGraph 1.0 |
| 模型 | 豆包 Seed / DeepSeek（OpenAI 兼容接口） |
| 文档处理 | python-docx / OpenXML |
| 包管理 | uv（Python） |
| 存储 | S3 兼容对象存储 / 内存 Checkpointer |
| 前端 | 原生 HTML + CSS + JavaScript（单文件 SPA） |

---

## 9. 评测概况

| 指标 | 数值 |
|------|------|
| 测试用例 | 21（7 模板 × 3 难度） |
| 平均填充率 | 91.7% |
| 平均准确率 | 70.2% |
| 总成本 | ¥0.63 |
| 扩展验证 | 8 非教务通用模板 |