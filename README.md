# 格智 FormWise — 智能文档自动填写系统

上传 Word 模板与参考材料，系统自动识别字段、提取信息、完整填充并生成 docx 文档，格式完整保留。

![Python](https://img.shields.io/badge/Python-3.12-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![LangGraph](https://img.shields.io/badge/LangGraph-1.0-orange)

## 功能

- **模板自动识别**：上传任意 Word 模板，系统自动扫描字段（冒号字段、空格、下划线、复选框、表格空单元格等），无需手动标注
- **多源数据提取**：支持上传旧报告、成绩单、聊天记录等文件，AI 自动提取对应字段值；也支持对话逐条填写
- **人机协同审核**：预填结果按置信度分三色标注（绿色确定 / 黄色推断 / 灰色缺失），审核确认后一键生成
- **格式 100% 保留**：直接操作 OpenXML 底层结构，Token 级替换文本，不动字体、字号、对齐、合并单元格等格式属性
- **内置 15 类模板**：覆盖教务（教材申报、缓考申请、考场记录、试卷分析、评价报告等）和通用行业（金融、医疗、HR、政务、物流等）

## 快速开始

```bash
# 1. 配置 API Key
cp projects/.env.example projects/.env
# 编辑 projects/.env，填入 EXTERNAL_LLM_API_KEY

# 2. 安装依赖（需要 uv）
uv sync --directory projects

# 3. 启动服务
bash projects/scripts/http_run.sh -p 5000
```

浏览器访问 `http://localhost:5000` 即可使用。

## 系统架构

```
用户上传模板 + 材料
       ↓
  ┌──────────┐
  │  Router   │ ← 条件路由，跳过已完成阶段
  └────┬─────┘
       ↓
  ┌──────────┐    ┌──────────────────┐
  │  知识提取  │ →  │ 旧报告/知识文件解析 │
  │  Agent    │    │ 事实提取 + 预填    │
  └──────────┘    └──────────────────┘
       ↓
  ┌──────────┐    ┌──────────────────┐
  │  字段填充  │ →  │ 模板分析 + 字段匹配 │
  │  Agent    │    │ AI预填 + 置信度标注 │
  └──────────┘    └──────────────────┘
       ↓
  ┌──────────┐    ┌──────────────────┐
  │  文档生成  │ →  │ OpenXML 底层填充  │
  │  Agent    │    │ 校验 + 修复 + 导出 │
  └──────────┘    └──────────────────┘
       ↓
    下载 docx
```

- **共 16 个工具**：知识提取 5 个、字段填充 8 个、文档生成 3 个
- **填充引擎**：10 个子模块（colon_filler / checkbox_filler / paragraph_filler / row_group_filler 等）

## 技术栈

| 层 | 技术 |
|------|------|
| 运行时 | Python 3.12 |
| Web 框架 | FastAPI |
| AI 编排 | LangChain 1.0 / LangGraph 1.0 |
| 大模型 | DeepSeek / 豆包（OpenAI 兼容接口） |
| 文档处理 | python-docx + lxml（OpenXML 底层操作） |
| 前端 | 原生 HTML + CSS + JS（单文件 SPA） |
| 部署 | Coze 智能体平台 |

## 评测结果

- 7 类教务模板 × 3 难度 = 21 个测试用例
- 平均填充率 **91.7%**，文档生成成功率 **100%**
- 格式保持率 **100%**（Word 2019 / WPS Office 双重验证）
- 21 次生成总成本 **0.63 元**

## 项目结构

```
├── projects/
│   ├── src/                  # 源代码
│   │   ├── main.py           # FastAPI 入口
│   │   ├── agents/agent.py   # Router + 3 Agent 流水线
│   │   ├── tools/            # 16 个工具模块
│   │   │   ├── filling/      # 填充引擎子模块（10 文件）
│   │   │   ├── template_analyzer.py
│   │   │   ├── edu_report_tool.py
│   │   │   └── ...
│   │   └── storage/          # 记忆持久化
│   ├── web/index.html        # 前端 SPA
│   ├── assets/templates/     # 15 个 docx 模板
│   ├── assets/knowledge/     # 知识文件
│   ├── config/               # Agent 配置
│   └── scripts/              # 启动脚本
├── .coze                     # Coze 平台配置
├── AGENTS.md                 # 项目记忆与修复记录
└── eval_report_20260609_181045.md
```

## 获奖

2026 年广东省大学生计算机设计大赛  
AI 智能体协作挑战赛（本科组）参赛作品