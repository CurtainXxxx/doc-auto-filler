# 独立运行改造

评估结论：可移除平台依赖。文档分析、字段状态、格式保留和生成引擎均为普通 Python 实现，可以保留。

| 原依赖 | 替代方式 |
| --- | --- |
| 平台 LLMClient、鉴权头 | model_provider.py 统一调用显式配置的兼容接口 |
| 平台图执行器、Chat Handler | service.py 使用 LangGraph 原生异步调用与事件流 |
| 请求上下文、日志追踪 | dataclass + contextvars + Python logging |
| 平台 S3 SDK | 默认本地文件与下载路由，可选 boto3 |
| Workload Identity | .env 和进程环境变量，环境变量优先 |
| 平台工作区、预览脚本 | 基于源码定位的项目目录与 uv 启动脚本 |
| 平台配置和兼容 shim | 删除，不修改全局 Python 安装 |

## 启动

安装 Python 3.12+ 与 uv，在仓库目录执行：

```powershell
cd projects
uv sync --frozen
Copy-Item .env.example .env
# 在 .env 中填写自己选择的模型服务配置
uv run python src/main.py -m http -p 5000
```

打开 http://localhost:5000/web/ 。未配置模型也可启动服务、上传模板、预览、手工填写并生成下载；对话与 AI 预填需要配置模型。

Linux/macOS 同样可直接执行 uv 命令，或使用 `bash scripts/http_run.sh -p 5000`。
Windows 也可使用 `./scripts/run.ps1 -p 5000`。

模型配置统一使用 `EXTERNAL_LLM_BASE_URL`、`EXTERNAL_LLM_MODEL`、`EXTERNAL_LLM_API_KEY`，模型应支持工具调用。不从版本控制中的 JSON 读取密钥，也没有隐式平台回退。本地无鉴权服务可用非空占位密钥。

## 存储与会话

生成文件默认在 `projects/data/generated/`，通过 `/download-docx` 下载。`APP_PUBLIC_URL` 可指定下载链接的公共前缀；留空时使用同源相对链接。S3 设置见 `.env.example`，支持标准 boto3 凭据链。显式选择 S3 后，上传失败会报错。

会话使用 `session_id` 关联历史，`X-Run-ID` 标识单次运行，`/cancel/{run_id}` 取消执行。未配置 PostgreSQL 时，对话与字段填写状态保存在内存，重启会丢失。字段状态目前仍是进程内缓存，所以保持单 worker；PostgreSQL 仅持久化图的消息历史。

保留 `/run`、`/stream_run`、`/v1/chat/completions`、上传、预填、生成与下载端点。`/stream_run` 使用 content/tool 事件；依赖旧平台专有事件格式的外部调用方需适配。现有网页使用的 Chat Completions 流保留文本 delta 和工具结果 delta。仅实现项目所需的兼容子集，不承诺完整协议覆盖。

PDF 导出需要另装 LibreOffice；普通 Word 生成不需要。

## 验证

```powershell
uv sync --frozen
uv run pytest
```

自动测试不调用付费模型。真实模型质量、云端 S3 与 PostgreSQL 部署仍需相应环境联调。

## 审查后的访问和运行限制

本地文档仅允许 assets 与应用 data 下的 uploads/generated/pdf_output；源码、.env 和整个系统临时目录不能通过下载接口读取。本机其他位置的文件通过上传接口导入。知识材料支持 txt/md/csv/docx/xlsx/pdf，旧 doc/xls 先转换，当前不提供 OCR。

远程代理下载需要显式 DOWNLOAD_ALLOWED_ORIGINS（精确 scheme://host:port，多项逗号分隔）；S3_ENDPOINT_URL 的来源自动允许。代理不跟随重定向，最多读取10MB。存储来源由管理员配置，不接受请求临时授权任意目标。

上传每文件10MB、每次10个、总HTTP请求32MB；docx/xlsx展开后50MB、2048条目。上传目录上限100MB，不自动清理既有用户文件。长期生成文件配额与保留策略仍待完善。

服务仍监听0.0.0.0且未增加多用户登录/权限体系；远程多用户使用前必须完成身份认证及资源归属设计。同会话并发保护只避免状态竞争。

工程检查：`uv run ruff check src tests scripts`，前端检查：`node tests/check_frontend.cjs`。完整改进与未解决事项见 [CODE_REVIEW_REPORT.md](CODE_REVIEW_REPORT.md)。
