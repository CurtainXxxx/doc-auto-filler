# FormWise 独立服务

Word 模板分析、知识提取、人工编辑、Agent 填写、格式保留生成及下载。

## 运行

```powershell
uv sync --frozen
Copy-Item .env.example .env
# 填写自己选择的模型服务配置
uv run python src/main.py -p 5000
```

访问 http://localhost:5000/web/ 。无模型配置时仍可上传、预览和手工生成；AI 功能需要支持工具调用的兼容模型服务。

- [配置与迁移说明](docs/STANDALONE.md)
- [当前架构与真实接口](docs/ARCHITECTURE.md)
- [审查、改进与验证报告](docs/CODE_REVIEW_REPORT.md)

支持知识材料 txt/md/csv/docx/xlsx/pdf。旧版二进制 doc/xls 需先转换；当前未实现图片 OCR。
文档必须位于 assets 或应用管理的文档目录；通过上传接口导入本机其他位置的文件。

## 开发验证

```powershell
uv run pytest -q
uv run ruff check src tests scripts
node tests/check_frontend.cjs
node tests/check_frontend_behavior.cjs
```

保持单 worker。应用监听所有网卡，供受信任单用户使用；远程多用户部署还需要身份认证及会话/文件归属控制。模型与云存储密钥只放在环境变量或未跟踪的 .env 中。

前端职责划分、填写引擎整理及回归证据见[旧代码清理报告](docs/LEGACY_CLEANUP_REPORT.md)。

## Leader-worker 实验版本

原工作流仍为默认值。设置 `AGENT_MODE=leader_worker` 并重启可试用受约束的Leader调度版本。
方案、运行和验证边界见 [迭代方案](docs/LEADER_WORKER_PLAN.md) 与 [实现及冒烟报告](docs/LEADER_WORKER_REPORT.md)。
同模板、同材料的两个真实模型对比场景均通过，数据与限制见 [轻量A/B报告](docs/LEADER_WORKER_AB_REPORT.md)。
真实A/B命令：`uv run --frozen python scripts/compare_agent_modes.py --live`，需要本地配置模型；默认测试不调用付费接口。
