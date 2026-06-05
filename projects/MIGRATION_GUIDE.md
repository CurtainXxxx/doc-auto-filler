# 项目迁移指南（给新 AI 助手阅读）

## 一、这是什么项目

**高校教务办公数字员工** — 一个 AI 驱动的教务文档自动填写工具。

### 核心功能
用户上传 Word 模板（如试卷分析表、评价报告等）和知识材料（成绩单、旧报告等），AI 自动识别模板字段、从材料中提取信息、填写表单、生成最终 docx 文档。

### 使用场景
- 教师上传试卷分析模板 + 成绩数据 → 自动生成试卷分析报告
- 教务上传评价报告模板 + 课程数据 → 自动生成课程目标达成度评价报告
- 支持 6 种内置模板：试卷分析、评价报告、关联矩阵、教务数据申请、教材建设申报、考场记录表

### 前端界面
- Web 页面：上传模板/知识文件、选择内置模板、实时预览填写进度、下载生成的 docx
- 部署链接（新账号会变）：`https://xxx.dev.coze.site/web/index.html`

---

## 二、项目结构

```
.
├── config/
│   └── agent_llm_config.json    # 模型配置（model、system_prompt、tools）
├── assets/
│   ├── templates/               # 6 个内置 Word 模板
│   └── knowledge/               # 6 个对应的知识文件目录
├── src/
│   ├── agents/
│   │   └── agent.py             # ★ 主逻辑：build_agent() 构建单 Agent
│   ├── tools/
│   │   ├── edu_report_tool.py   # 模板分析、表单填充、文档生成
│   │   ├── knowledge_tool.py    # 知识文件解析、事实提取
│   │   ├── prefill_tool.py      # 知识→字段智能匹配预填
│   │   ├── old_report_extractor.py  # 旧报告反向提取
│   │   ├── template_analyzer.py # 模板结构分析
│   │   ├── docx_preview.py      # docx 预览
│   │   ├── docx_upload.py       # 文件上传处理
│   │   ├── docx_validator.py    # 文档校验
│   │   ├── form_filling_state.py # 表单填充状态管理
│   │   └── error_handler.py     # 错误处理
│   ├── storage/
│   │   ├── memory/memory_saver.py  # 对话记忆（PostgreSQL checkpointer）
│   │   └── database/db.py         # 数据库连接
│   ├── utils/
│   │   └── helper.py            # 工具函数
│   └── main.py                  # FastAPI 入口（不要改）
├── web/
│   └── index.html               # 前端页面
├── local_debug.py               # 本地命令行调试入口
├── pyproject.toml               # 依赖声明（uv 管理）
└── .env                         # 环境变量（不提交到 Git）
```

---

## 三、当前架构（单 Agent 模式）

```
用户消息 → 1 个 Agent（16 个工具）→ 回复
```

Agent 有 16 个工具：模板分析、知识提取、字段匹配、文档生成等，全部挂在一个 Agent 上。

### 关键代码位置

| 文件 | 行数 | 说明 |
|------|------|------|
| `src/agents/agent.py` | 172 行 | `build_agent()` 构建 Agent，LLM 初始化，工具注册 |
| `config/agent_llm_config.json` | - | model、sp（system prompt）、tools 列表 |
| `src/tools/edu_report_tool.py` | - | 核心工具：模板分析、表单填充、文档生成 |
| `src/tools/prefill_tool.py` | - | LLM 驱动的字段匹配预填 |

### 模型配置逻辑（agent.py 第 118-155 行）

```
if .env 有 EXTERNAL_LLM_API_KEY:
    走外部 API（DeepSeek）
else:
    走平台内置模型（读 config/agent_llm_config.json 的 model 字段）
```

---

## 四、迁移注意事项

### 4.1 环境变量（.env）
`.env` 不会随项目导出，新账号需要手动创建：

```env
# 如果用 DeepSeek（推荐，省积分）
EXTERNAL_LLM_API_KEY=sk-xxx
EXTERNAL_LLM_BASE_URL=https://api.deepseek.com/v1
EXTERNAL_LLM_MODEL=deepseek-chat

# 如果用平台内置模型，留空即可，会走 config/agent_llm_config.json
```

### 4.2 模型确认
- 当前 config 中 model 为 `doubao-seed-1-6-251015`
- 如果新账号不支持此模型，改为 `glm-4-7-251222` 或其他可用模型
- **建议**：配置 DeepSeek（`.env`），不消耗 Coze 积分

### 4.3 依赖安装
Coze 平台会自动安装 `pyproject.toml` 中的依赖，无需手动操作。
关键依赖：`langchain==1.0.3`、`langgraph==1.0.2`、`coze-coding-utils`、`coze-coding-dev-sdk`

### 4.4 部署链接
新账号部署后 URL 会变，格式为：`https://{new-id}.dev.coze.site/web/index.html`

### 4.5 数据库/存储
- `storage/memory/memory_saver.py` 使用 PostgreSQL checkpointer（平台内置）
- `storage/database/db.py` 数据库连接（平台内置）
- 这些是 Coze 平台级服务，新账号自动可用

---

## 五、下一步开发计划

### 目标：多 Agent 架构（比赛核心要求）

当前是**单 Agent**，比赛要求展示**多 Agent 协作**。需要改造为：

```
用户 → 知识提取Agent → 填充Agent → 生成Agent → 输出
```

### 最简方案（推荐，约 80 行代码）

1. 在 `agent.py` 中用 `StateGraph` 建 3 个节点
2. 每个节点是独立的 `create_agent`（配不同工具 + prompt）
3. 固定顺序连接（add_edge），不需要 Supervisor 路由
4. 每个 Agent 输出带标签（如 `[知识提取Agent]`），评审能看到协作过程

### 工具分配

| Agent | 工具 |
|-------|------|
| 知识提取 Agent | `parse_knowledge_file`、`extract_facts`、`extract_from_old_report`、`prefill_from_old_report`、`get_fill_checklist` |
| 填充 Agent | `analyze_uploaded_template`、`list_templates`、`init_form_filling`、`update_form_fields`、`prefill_from_knowledge`、`prefill_from_multiple_knowledge`、`get_form_status` |
| 生成 Agent | `generate_form_document`、`generate_edu_report`、`generate_from_template`、`analyze_report_template` |

### 不要改的文件

| 文件 | 原因 |
|------|------|
| `src/tools/*.py` | 工具逻辑不动 |
| `src/main.py` | 平台入口 |
| `web/index.html` | 前端 |
| `local_debug.py` | 本地调试 |

### 测试方式

1. **Coze 平台测试**：`test_run` 工具
2. **本地测试**（推荐，不消耗积分）：
   ```bash
   # 先配 .env（DeepSeek）
   python local_debug.py
   # 输入：请帮我填一份试卷分析表，课程是高等数学
   ```

---

## 六、已知问题

| 问题 | 状态 | 说明 |
|------|------|------|
| DeepSeek 不支持 `response_format` | 已知 | 结构化输出用纯文本 + 标签解析，不用 `with_structured_output` |
| 多 Agent 架构之前失败 | 已放弃 | 之前尝试的 Supervisor + Command 路由太复杂，改为简化方案 |
| 预览表格错乱 | 已修复 | commit `1a391e8` 修复了 td display:flex 问题 |

---

## 七、GitHub 仓库

- 地址：`https://github.com/CurtainXxxx/doc-auto-fillter`
- 当前分支：`feature/v2-fill-enhance-v2`
- 最新提交：`90259b7` — fix: 打印CSS补全

---

## 八、给新 AI 的指令

请帮我完成以下任务：

1. **确认项目能跑**：在 Coze 平台运行 `test_run`，输入"列出可用模板"，确认 Agent 正常回复
2. **实现多 Agent 架构**：按照第五章的最简方案，在 `agent.py` 中实现 3 节点流水线
3. **不要改 tools/**：所有工具函数保持不变
4. **不要用 with_structured_output**：DeepSeek 不支持
5. **测试**：用 `local_debug.py` 本地测试，不消耗 Coze 积分
