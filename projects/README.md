# 格智 FormWise — 智能文档自动填写系统

上传 Word 模板与参考材料，系统自动识别字段、提取信息、完整填充文档，格式完整保留。

## 快速上手

### 1. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，填入大模型 API Key：

```env
EXTERNAL_LLM_API_KEY=sk-your-api-key-here
EXTERNAL_LLM_BASE_URL=https://api.deepseek.com
EXTERNAL_LLM_MODEL=deepseek-chat
```

> ⚠️ 未配置 API Key 时系统无法运行。

### 2. 安装依赖

```bash
uv sync
```

### 3. 启动服务

```bash
bash scripts/http_run.sh -p 5000
```

浏览器访问 `http://localhost:5000` 即可使用。

## 使用方式

**方式一：上传模板填写**
1. 上传任意 Word 模板（.docx）
2. 上传知识文件（旧报告、成绩单、聊天记录等）
3. 系统自动提取数据并填充
4. 预览确认后下载文档

**方式二：内置模板填写**
1. 选择内置模板（评价报告、试卷分析、关联矩阵等）
2. 上传知识文件或对话补充信息
3. 系统自动填充并生成文档

**方式三：人机协同填写**
1. 上传模板后点击「人机协同填写」
2. AI 自动预填，三色标注置信度
3. 审核确认后一键生成文档

## 覆盖模板

已适配 15 类 Word 模板：

| 类别 | 模板 |
|------|------|
| 教务 | 教材建设申报书、缓考申请单、考场记录表、试卷分析模板、教务数据申请表、评价报告模板、关联矩阵模板 |
| 通用 | 个人贷款申请表、产品质量检测报告、住院登记表、发货委托单、员工入职登记表、团队旅游报名表、房屋信息登记表、行政审批申请表 |

## 项目结构

```
├── config/
│   └── agent_llm_config.json     # Agent 配置（系统提示词 + 工具注册）
├── scripts/
│   ├── http_run.sh               # 服务启动脚本
│   ├── coze-preview-build.sh     # 预览构建
│   └── coze-preview-run.sh       # 预览运行
├── assets/
│   ├── templates/                # 15 个 docx 模板
│   └── knowledge/                # 知识文件（各模板 3 种难度）
├── src/
│   ├── main.py                   # FastAPI 服务入口
│   ├── agents/agent.py           # Agent 主逻辑（Router + 3 Agent）
│   ├── tools/                    # 工具模块（共 16 个工具）
│   │   ├── template_analyzer.py  # 模板解析引擎
│   │   ├── edu_report_tool.py    # 文档生成引擎
│   │   ├── filling/              # 填充子模块（10 文件）
│   │   └── ...
│   └── storage/                  # 记忆持久化
├── web/
│   └── index.html                # 前端 SPA（单文件）
├── pyproject.toml                # 项目配置 + 依赖
└── .env.example                  # 环境变量模板
```

## 技术栈

- **后端**：Python 3.12 / FastAPI / LangChain 1.0 / LangGraph 1.0
- **前端**：原生 HTML + CSS + JavaScript（单文件 SPA）
- **模型**：通过 OpenAI 兼容接口接入（DeepSeek / 豆包等）
- **文档处理**：python-docx + OpenXML 底层操作
- **架构**：Router + 3 Agent 条件路由（知识提取 → 字段填充 → 文档生成）

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/run` | Agent 同步调用 |
| POST | `/stream_run` | Agent 流式调用（SSE） |
| POST | `/upload` | 上传知识文件 |
| POST | `/upload-template` | 上传自定义模板 |
| POST | `/prefill` | AI 预填 |
| GET | `/template-preview` | 模板预览 |
| GET | `/download-docx` | 下载生成文档 |

## 评测结果

- 7 类教务模板 × 3 难度，共 21 个测试用例
- 平均填充率 **91.7%**，文档生成成功率 **100%**
- 格式保持率 **100%**（Word 2019 / WPS Office 验证）
- 21 次生成总成本 **0.63 元**