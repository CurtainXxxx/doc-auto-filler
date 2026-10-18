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

## 首次验证时的 AI 与外部集成状态（历史记录）

用户选择 DeepSeek。已创建被 Git 忽略的本地 `.env`，填好地址和模型名；检查时 API Key 尚为空，没有发起付费模型请求。
浏览器实际打开 `/web/` 并点击“内置模板”后，页面显示缺少 `EXTERNAL_LLM_*` 配置，故没有把浏览器 AI 流程记为通过。

首轮以下项目尚未验证；DeepSeek后续结果见下文：

1. DeepSeek 真实预填、完整 Agent 对话及事实准确率：等待用户在本地填入密钥。
2. PDF 转换与逐页视觉保真：本机未安装 LibreOffice。
3. 真实 S3、PostgreSQL，以及未纳入本轮的任意用户模板。

上述为密钥配置前的状态。后续用户在本地配置好密钥并授权继续，完成以下真实AI验证。

## DeepSeek 实际联调与追加修复

模型：`deepseek-flash`，官方地址 `https://api.deepseek.com`。未使用模型替身；仅使用仓库内的示例教务数据，不包含用户真实业务资料。密钥由用户填写在被Git忽略的 `.env`，未打印或提交。

### 真实预填 → 生成 → 下载

知识材料为 `assets/knowledge/教务数据申请/教务数据申请_【简单】.txt`，模板为 `assets/templates/教务数据申请表.docx`。
真实 `/prefill` 请求耗时5.92秒，预填8/14项；申请人姓名、工资号、电话、邮箱、申请部门和日期等6项逐值与原文一致，来源为模型提取而非规则兜底；无材料依据的6项为空。
直接使用AI返回值调用 `/cofill-generate`，HTTP下载后检查6个目标单元格原文精确一致、8个已填值存在、OOXML完整并可再次预览。

### 完整 Agent 的失败与修复

1. **首轮SSE失败**：DeepSeek返回400，明确要求工具调用后回传`reasoning_content`。按[官方文档](https://api-docs.deepseek.com/guides/tool_calls/)对官方DeepSeek端点显式关闭思考模式，其他服务不发送该参数；没有伪造reasoning内容。
2. **第二轮仅填1/14项**：虽然工具和生成API返回成功，但知识Agent把路径作为正文，填充Agent提交的显示字段缺少ID，合并时空ID相互覆盖。本次未把该结果记为验收通过。
3. **追加修复**：提供受目录边界约束、可分页的`read_knowledge_file`工具；提示词要求先读正文。`prefill_from_knowledge`支持仅传文件和模板路径，从实际模板获取全部字段ID；兼容旧清单时缺省ID逐项唯一。模型输出优先按field_id匹配，避免同名签名串值。
4. **复测成功**：独立新会话经真实SSE依次完成知识读取、事实提取、模板分析、初始化、批量更新和生成；21.36秒完成，无SSE错误，`update_form_fields.matched_count=8`。下载Word的6个关键单元格与原文精确一致。
5. **文档细节**：实读输出发现跨run的冒号标签会把值插到冒号前面，现按文本偏移在分隔符后插入，保留run格式和原有尾注；加入3种分段格式回归。

失败及各阶段原始结果分别保存在被Git忽略的 `data/live-deepseek/agent-events.json`、`agent-events-after.json`、`agent-events-final.json`，未把失败文件覆盖成成功记录。

### 实际浏览器验收

修复后打开真实 `/web/`，通过输入框提交“按知识文件填写模板并生成”的请求。
页面依次展示知识提取、填写与生成结果，显示8/14项已填，并出现可点击的Word下载链接。
从页面实际返回的链接下载文档，额外确认：

- 6个关键单元格逐值正确。
- 缺少原文的数据用途、申请数据描述保留原占位提示；4处签名仍未填写。
- 安全承诺、公章文字位于标签冒号之后，未插到冒号前。
- ZIP CRC及XML可解析，页面节属性与原模板一致。

浏览器结果文件为 `data/live-deepseek/教务数据申请表-DeepSeek浏览器实测.docx`；验证记录 `browser-verification.json`，现场截图 `browser-success.png`。
完成卡片原先固定写“所有字段已填入”和“链接24小时内有效”，与实际部分填写和本地存储行为不符，现改为核对内容、留意空白和保存文档的提示。
刷新后在同一真实会话再次请求生成并下载，比较`word/document.xml`与上一份完全相同，验证了连续对话及字段保留；证据为`browser-followup-verification.json`，更新后的界面截图为`browser-success-final.png`。模型自行描述的文件名片段不当作真实文件哈希，SHA256由验证脚本独立计算。

### 最新回归结果及边界

- `uv run --frozen pytest -q --disable-warnings`：**59 passed，7 warnings**。
- `uv run --frozen ruff check src tests scripts`、`node tests/check_frontend.cjs`、源码编译和`git diff --check`通过。
- 新增回归包含实际SDK工具往返的DeepSeek参数隔离、分页读取/目录边界、完整模板字段、缺省ID不覆盖、同名字段ID优先和冒号分隔符位置。

上述通过表示此示例的真实AI、本地Agent及下载链路已验证，不是任意模板识别准确率或逐页视觉保真的证明。其他模型、实际S3/PostgreSQL、LibreOffice PDF及历史21用例评测仍未验证。
