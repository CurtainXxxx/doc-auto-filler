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

### 修复记录（2026-06-07 缓考申请单模板）

使用 `缓考申请单.docx` + 模拟用户数据完整测试通过：

| 问题 | 根因 | 修复 | 验证 |
|------|------|------|------|
| Fix 1: 课程数据未填入 | `FormFillingState` 忽略 `row_groups` | `init_from_analysis` 存储行组元信息；`bulk_fill` 识别 T0_G0 key；`get_label_value_map` 合并行组数据 | ✅ 3门课全部正确填入 |
| Fix 2: 合并单元格重复 | python-docx 保存时展开合并单元格的副本 | `_fix_merged_cells(doc)` 在 `doc.save()` 前清理重复 `<w:tc>` XML 元素 | ✅ XML 验证 1 tc with gs=N |
| Fix 3: 复选框未勾选 | `_detect_checkbox_row` 未识别 `□N.` 模式 | `_fill_checkbox_rows_in_table` 增加 `□` 检测，通过 `data` 字典直查相邻内容格是否有对应值 | ✅ □3→☑3, □4→☑4 |
| Fix 4: 签名/日期错乱 | 所有日期字段共用 `"学生签名"` section context | `analyze_template` 中新增 `row_first_cell_ctx` / `cell_first_label` 机制，优先使用行首标签作为 section context | ✅ 班主任意见-日期 / 教学秘书意见-日期 / 教学院长意见-日期 各自独立 |
| Fix 5: 学生信息未分离 | `_scan_paragraph_underline_fields` 将整个段落视为一个字段 | 修改检测逻辑，按非下划线 run 组拆分标签，为每个子字段分配 `underline_run_start` 和 `underline_run_count` | ✅ 姓名/学号/所在院系/电话 各自独立填值 |
| Fix 6: 学生签名被过滤 | 过滤逻辑将 `existing_value="日期"` 视为已有数据 | 新增 `_is_placeholder_value` 判断短中文词为非填充数据 | ✅ 学生签名和日期字段均保留 |

### 多标签段落检测机制

`_scan_paragraph_underline_fields` 支持将同段落中多个非下划线 run 组拆分为独立字段：
- 遍历段落的所有 run，标记下划线状态
- 将非下划线 run 按空白 run 分隔为多个标签组
- 每个标签组创建独立字段，指定 `underline_run_start` 和 `underline_run_count`
- `_fill_paragraph_fields` 从指定索引开始查找下划线 run

### 行首标签 section context 机制

`analyze_template` 在检测 colon 字段时，优先使用当前行的首个单元格内容作为 section context：
- `row_first_cell_ctx`：当前行有多个单元格时，Cell 0 的内容
- `cell_first_label`：当前单元格第一个标签（行内只有一个单元格时）
- 优先级：`cell_first_label(同行同单元格)` > `row_first_cell_ctx(同行Cell 0)` > `section_ctx(上方最近section标题)`

## 已知问题与注意事项

1. **cozeloop 兼容层**：`cozeloop` v0.1.x 使用了 LangChain 旧版本导入路径（`langchain.callbacks.base`、`langchain.schema`），已在系统 `dist-packages` 添加兼容 shim。如果迁移到新版本 cozeloop，需移除这些 shim。
2. **无用系统依赖已移除**：`pycairo==1.29.0`、`dbus-python==1.3.2`、`PyGObject==3.48.2` 在源码中未被引用，已从 `pyproject.toml` 和 `requirements.txt` 中移除。
3. **前端挂载**：`main.py` 中前端静态文件通过双路径候选挂载（优先项目根下的 `web/`，回退到 `COZE_WORKSPACE_PATH/web`）。
4. **预览脚本已更新 lock 文件**：`coze-preview-build.sh` 使用 `uv sync`（非 `--frozen`）以允许 lock 文件更新。
5. **模板路径解析**：`edu_report_tool.py` 中 `generate_from_template` 和 `analyze_uploaded_template` 已修复为使用 `_resolve_template_path`，支持从 `assets/` 子目录查找模板文件。
6. **MultiAgentState**：`agent.py` 中 `MultiAgentState` 需包含 `remaining_steps` 字段以满足 LangGraph 1.0 的 `create_react_agent` 要求。
7. **Agent 中间件**：使用 `AgentMiddleware` 子类时需同时实现 `wrap_tool_call`（同步）和 `awrap_tool_call`（异步）方法。
8. **合并单元格处理**：`_fill_custom_template` 在 `doc.save()` 前调用 `_fix_merged_cells(doc)` 清理 python-docx 保存时展开的合并单元格副本。如遇到因合并单元格导致的内容重复，检查此函数是否正确触发。
9. **行组（Row Group）数据支持**：`FormFillingState` 支持行组存储（`bulk_fill` 识别 `T0_G0` 等行组 key），`get_label_value_map` 返回行组数据。Agent 的 `filling_sp` 已提及行组格式，Agent 需通过 `update_form_fields` 将 `{"T0_G0": [[...], [...]]}` 传入。
10. **langchain 兼容 shim**：2026-06-07 重建了 `langchain/callbacks` 和 `langchain/schema` 兼容 shim（指向 `langchain_classic`），解决 `StructuredTool` 启动时 ModuleNotFoundError。
11. **部署 API Key 配置**：`agent.py` 的 `_build_llm` 增加 `config/agent_llm_config.json` 的 `external_llm` 段作为 fallback。部署环境无 `.env` 文件时，从此段读取 `api_key`/`base_url`/`model`。如需更换 API Key，直接修改 `config/agent_llm_config.json` 的 `external_llm.api_key` 后重新部署。

## 源码仓库

- **GitHub**：`https://github.com/CurtainXxxx/doc-auto-fillter`
- **当前分支**：`feature/v3-multi-agent`
- **最新提交**：`9258f13` — feat: 自动评测脚本 + 21 GT JSON + 多 agent 知识文件

## 评测结果（2026-06-09）

全量 21 用例端到端评测（7 模板 × 3 难度）：

| 指标 | 数值 |
|------|------|
| 平均填充率 | 92.3%（13 完成用例） |
| 平均准确率 | 65.9%（13 完成用例） |
| 总 Token | 443,672 |
| 总成本 | ¥0.55（21 次调用） |
| 总耗时 | 32.4 分钟 |

### 各模板表现

| 模板 | 填充率 | 准确率 | 覆盖 |
|------|--------|--------|------|
| ✅ 教材建设申报书 | 100% | 90.5% | 3/3 |
| ✅ 缓考申请单 | 100% | 52.8% | 3/3 |
| ✅ 考场记录表 | 100% | 74.1% | 3/3 |
| ✅ 试卷分析模板 | 100% | 87.4% | 3/3 |
| ✅ 教务数据申请表 | 100% | 65.0% | 2/3 |
| ✅ 关联矩阵模板 | 50% | 37.5% | 2/3 |
| ⚠️ 评价报告模板 | - | - | 耗时过长 |

评测脚本：`scripts/auto_eval.py`；Ground Truth：`/workspace/projects/ground_truth/*.json`

## 下一步开发计划

1. ~~**多 Agent 架构**~~ ✅ 已完成：StateGraph 3 节点流水线（知识提取 → 填充 → 生成），见 `src/agents/agent.py`
2. **通用模板引擎重构**（`docs/refactor_plan.md`）：field_id 为主、label 为兼容、事实提取分离、生成后校验

## 多 Agent 架构详情

```
START → Router → (条件路由) → knowledge_extraction / filling / generation → END
```

| Agent | 工具数 | 核心职责 |
|---|---|---|
| 知识提取 Agent | 5 | 旧报告提取、知识文件解析、事实提取、预填、数据准备清单 |
| 填充 Agent | 8 | 模板分析、表单初始化、字段更新、AI预填、状态查询 |
| 生成 Agent | 4 | 表单文档生成、内置模板生成、上传模板生成、模板分析 |

**Router 机制**：根据对话中是否已有 `[FACTS]`/`[FIELDS]`/`[生成完成]` 标记，跳过已完成的阶段，只调用需要的 Agent。每个 Agent 的 system_prompt 包含阶段完成标记指令（`[知识提取完成]`/`[填充完成]`/`[生成完成]`）。

### 测试结果（2026-06-05）

使用 `教材建设申报_【简单】.txt` 知识文件 + `教材建设申报书.docx` 模板完整测试通过：

| 阶段 | 状态 | 详情 |
|------|------|------|
| 知识提取 Agent | ✅ | 提取 15 条事实（申报人、教材、项目描述等） |
| 填充 Agent | ✅ | 识别 29 字段，14 条自动匹配（48.3%），剩余由 AI 合理补全 |
| 生成 Agent | ✅ | 100% 填写率，7 项校验全通过，生成 docx 并上传对象存储 |

考场记录表 + 试卷分析模板也通过测试（100% / 97.5% 填写率）。

## 修复记录（2026-06-09 vMerge 生成失败）

### 问题
试卷分析模板、评价报告模板、关联矩阵_困难的 Agent 无法生成 docx 下载链接。Agent 执行报错 `no tc element at grid_offset=0`。

### 根因
`_fix_merged_cells(doc)` 中 `row.cells` 访问触发了 python-docx 的 vMerge（纵向合并单元格）递归。当表格存在 `vMerge` 单元格时，python-docx 内部会沿 `tc._tc_above` 链向上查找，如果行结构不一致则抛出 `ValueError: no tc element at grid_offset=0`。

### 修复
`edu_report_tool.py: _fix_merged_cells()` 改用 XML 级别操作（`tr.findall(qn("w:tc"))`）直接遍历 `<w:tc>` 元素去重，完全避免触发 vMerge 迭代。

### 验证
| 模板 | 修复前 | 修复后 |
|------|--------|--------|
| 试卷分析_简单 | ❌ 无下载链接 | ✅ 100%填充, 85.2%准确 |
| 试卷分析_中等 | ❌ 无下载链接 | ✅ 100%填充, 88.9%准确 |
| 试卷分析_困难 | ❌ 无下载链接 | ✅ 100%填充, 88.9%准确 |
| 评价报告模板 | ❌ 无下载链接 | ✅ 生成成功（耗时较长） |
| 关联矩阵_困难 | ❌ 无下载链接 | ✅ 100%填充, 生成成功 |
| 教务数据_简单 | ❌ 知识不足 | ⚠️ 数据覆盖问题，非代码 bug |

## 用户偏好与长期约束

- Python 3.12，使用 `uv` 管理依赖
- 预览端口固定 5000，监听 `0.0.0.0`
- 禁止使用 9000 端口
- Node.js 项目仅允许 `pnpm`

## 代码注释规范（2026-06-10）

所有核心 Python 文件已按要求添加必要注释：

| 文件 | 注释状态 | 说明 |
|------|----------|------|
| `src/main.py` | ✅ 增强 | 文件头 + 各 API 端点 docstring |
| `src/agents/agent.py` | ✅ 已有 | 模块 docstring + 函数注释完整 |
| `src/tools/edu_report_tool.py` | ✅ 已有+审查 | 各填充函数 docstring + 内联注释 |
| `src/tools/template_analyzer.py` | ✅ 已有+审查 | 5 层扫描算法各阶段均有注释 |
| `src/tools/form_filling_state.py` | ✅ 已有 | 状态机各方法参数/返回已标注 |
| `src/tools/prefill_tool.py` | ✅ 已有 | AI 预填流程注释完整 |
| `src/tools/knowledge_tool.py` | ✅ 已有 | 文件解析 + 规则提取注释完整 |
| `src/tools/docx_validator.py` | ✅ 已有 | 校验管线各步骤均有 docstring |
| `src/tools/docx_upload.py` | ✅ 已有 | S3 上传 + 本地保存注释完整 |
| `src/tools/docx_preview.py` | ✅ 已有 | HTML 转换 + field_map 注释 |
| `src/tools/error_handler.py` | ✅ 已有 | 装饰器 + 日志配置注释完整 |
| `src/tools/old_report_extractor.py` | ✅ 已有 | 旧报告提取 + 预填注释完整 |
| `src/storage/memory/memory_saver.py` | ✅ 增强 | 模块 docstring + 降级策略注释 |
| `src/storage/database/db.py` | ✅ 增强 | 引擎创建 + 重试机制注释 |

注释原则：
- 每个 `.py` 文件顶部有模块级 docstring 说明职责
- 每个公开/核心函数有 Args/Returns docstring
- 复杂逻辑（字段匹配、行组填充、vMerge 修复）有决策说明
- 已知 Bug 的修复处标注了根因和修复策略

## 重构记录（2026-06-11）

### edu_report_tool.py 解耦拆分

将 2233 行单文件拆分为 `src/tools/filling/` 子模块（10 个文件）：

| 文件 | 行数 | 职责 |
|------|------|------|
| `edu_report_tool.py` | 599 | 仅保留 @tool 函数 + FormFillingState 桥接 |
| `filling/base.py` | 289 | 底层 XML 操作（单元格、行、vMerge） |
| `filling/token_style.py` | 318 | 词元级样式继承 v2（run 定位 + 3 模式策略） |
| `filling/colon_filler.py` | 190 | 冒号模式 + 签名/日期填充 |
| `filling/checkbox_filler.py` | 216 | 勾选框检测（两遍扫描：选项词 + □N.） |
| `filling/paragraph_filler.py` | 59 | 段落下划线字段填充 |
| `filling/row_group_filler.py` | 164 | 行组（T0_G0）填充 + multi_col |
| `filling/data_expander.py` | 189 | 通用模板数据预处理 |
| `filling/builtin_expander.py` | 241 | 内置模板数据预处理（考勤/分数段） |
| `filling/doc_builder.py` | 153 | 生成编排（build_report_docx / fill_custom_template） |
| `filling/postprocess.py` | 55 | 合并单元格修复（_fix_merged_cells） |

**验证结果**：
- 10 个新文件独立导入 ✅
- edu_report_tool.py 从 2233→599 行 ✅
- agent.py 全链路 _load_tools() 16 工具 ✅
- 重复定义（_get_unique_cells）已消除 ✅
- 端到端冒烟测试通过（缓考申请单、教材建设申报书）