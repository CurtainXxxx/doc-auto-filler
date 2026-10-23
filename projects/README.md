# FormWise 独立服务

Word 模板识别、AI 预填、人工审核、Agent 填写及文档生成。项目已移除 Coze 平台运行依赖。

完整介绍、两种架构、真实测试与目录说明见[仓库 README](../README.md)。

## 在本目录启动

需要 Python 3.12+ 与 uv：

```powershell
uv sync --frozen
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env，配置 EXTERNAL_LLM_BASE_URL / MODEL / API_KEY
uv run --frozen python src/main.py -p 5000
```

打开 http://localhost:5000/web/ 。模型配置为空时仍可上传、预览、手工填写和生成。不要覆盖已有 `.env`；进程环境变量优先。

默认 `AGENT_MODE=workflow`；设置为 `leader_worker` 并重启可启用实验调度。配置示例见[.env.example](.env.example)，运行与存储说明见[独立部署指南](docs/STANDALONE.md)。

## 开发验证

```powershell
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts
node tests/check_frontend.cjs
node tests/check_frontend_behavior.cjs
```

当前有82项Python测试和10项前端行为检查；CI覆盖Windows/Ubuntu与Python 3.12/3.13，回归不调用付费模型。

显式运行真实模型对比：

```powershell
uv run --frozen python scripts/compare_agent_modes.py --live --mode both
```

该命令需要完整模型配置，会调用服务并可能产生费用；记录写入 `data/leader-worker-compare/`。两个场景、两种架构的四次实测均通过，完整数据和限制见[真实A/B报告](docs/LEADER_WORKER_AB_REPORT.md)。

## 文档与边界

- [当前架构与API](docs/ARCHITECTURE.md)
- [Leader-worker设计](docs/LEADER_WORKER_PLAN.md)
- [旧前端与填写引擎清理](docs/LEGACY_CLEANUP_REPORT.md)
- [审查、依赖与验证报告](docs/CODE_REVIEW_REPORT.md)

知识材料支持txt/md/csv/docx/xlsx/pdf，旧doc/xls需先转换，暂无图片OCR。文档通过上传接口或从assets和应用管理的目录读取。

保持单worker。服务默认监听所有网卡，面向受信任单用户；远程多用户部署需要身份认证及资源归属控制。默认内存状态重启丢失，长期文件生命周期仍需管理。模型密钥只放在环境变量或被Git忽略的.env中。
