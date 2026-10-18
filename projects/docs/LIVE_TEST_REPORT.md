# 实际服务验证记录

日期：2026-10-18（Asia/Shanghai）。分支：`review/reliability-and-dependencies`。

## 测试方式

独立启动 `python src/main.py -p 5000`，通过 `http://127.0.0.1:5000` 的真实 TCP HTTP 请求访问服务。
使用仓库内真实 Word 模板和配套简单知识文件；填写值来自示例资料及明确标记的验收样例，不含用户私密资料。
未使用 FastAPI TestClient、模型替身、网络 Mock 或云存储 Mock。

可复现命令（在 `projects/`，依赖已安装）：

```powershell
# 终端一：使用独立数据目录，保持单 worker
$env:STORAGE_BACKEND = 'local'
$env:PGDATABASE_URL = ''
$env:APP_DATA_DIR = "$PWD/data/live-verification-20261018"
uv run --frozen python src/main.py -p 5000

# 终端二
uv run --frozen python scripts/live_smoke.py --output-dir data/live-http-results-after
```

每个用例执行：知识文件上传 → Word 模板上传 → 模板 HTML 预览 → 人工确认值生成 → HTTP 下载 → 重读 DOCX → 生成结果预览。
检查 ZIP CRC、各 XML 部件可解析、表格数量和页面节属性不变、预期文本存在、选项对应的格子有正确勾号、原模板 SHA256 不变。
本测试不将“包含预期文本”解释为所有文本位置、逐页视觉排版及格式均正确。

## 首轮实际发现与修复

首次运行结果 **4/6 通过**。考场记录表、试卷分析模板出现生成成功但复选框未勾选的问题。
原因：HTTP 生成入口把输入归一化为 field_id，而复选框填写器仍只查询 label；同一行第二组选项还可能仅被分析为普通字段。

修复 `src/tools/filling/checkbox_filler.py`：按表、行、组选项标签查找对应字段，优先取精确 field_id，兼容旧 label 输入；不把一行不同组的选项混填。
补充3个回归场景：考试方式、同一行两组选项、field_id 与 label 冲突时精确 ID 优先。

首轮原始结果仍保留于 `data/live-http-results/results.json`，未覆盖为通过结果。

## 修复后重跑

| 真实模板 | 输入值存在检查数 | DOCX 字节数 | 结果 |
| --- | ---: | ---: | --- |
| 教务数据申请表 | 8 | 17544 | 通过 |
| 教材建设申报书 | 10 | 20642 | 通过 |
| 考场记录表（含7行验收考生、超出原5行） | 45 | 14210 | 通过 |
| 评价报告模板 | 7 | 173417 | 通过 |
| 试卷分析模板（含0值及多组选项） | 19 | 24146 | 通过 |
| 关联矩阵模板 | 10 | 21023 | 通过 |

最终 **6/6 通过**。计数为输入值检查次数，包含重复值，不是独立字段识别准确率。
修复后下载文件与完整 JSON 证据保存在 `projects/data/live-http-results-after/`，该目录被 Git 忽略。

额外回归验证：

- `uv run --frozen pytest -q --disable-warnings`：**48 passed，7 warnings**。
- `uv run --frozen ruff check src tests scripts`：通过。
- `node tests/check_frontend.cjs`：通过。

## AI 与外部集成的实际状态

用户选择 DeepSeek。已创建被 Git 忽略的本地 `.env`，填好地址和模型名；检查时 API Key 尚为空，没有发起付费模型请求。
浏览器实际打开 `/web/` 并点击“内置模板”后，页面显示缺少 `EXTERNAL_LLM_*` 配置，故没有把浏览器 AI 流程记为通过。

以下项目仍**未验证**：

1. DeepSeek 真实预填、完整 Agent 对话及事实准确率：等待用户在本地填入密钥。
2. PDF 转换与逐页视觉保真：本机未安装 LibreOffice。
3. 真实 S3、PostgreSQL，以及未纳入本轮的任意用户模板。

结论：本轮验证了真实 HTTP 下的本地人工填写和文档生成链路，并实际发现、修复了一处遗漏；没有据此宣称全部功能已验收。
