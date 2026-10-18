# Code Review 与渐进升级报告

日期：2026-10-18（Asia/Shanghai）。基线：去 Coze 版本经 PR #2 合并后的 `main`，提交 `2f3919e`。改进在独立分支 `review/reliability-and-dependencies`，未自动合入 main。

## A. 项目概况与审查范围

FormWise 使用 Python 3.12+、FastAPI、LangChain/LangGraph、python-docx/OpenXML，前端为原生单页 HTML/JavaScript，使用 uv 管理依赖。用户上传 Word 模板与知识材料，通过 Agent 或人工编辑生成文档，支持本地下载、可选 S3 和 PostgreSQL。

主链：浏览器 → main.py → service.py → Router → 知识/填写/生成 Agent → 工具与 filling 子模块 → 文档存储。另有绕过 Agent 的人工确认生成流程。图消息由 checkpointer 管理；字段状态为进程内对象。

审查前读取 README、AGENTS、架构/迁移文档、配置、锁文件、启动/评测脚本，建立全库文件与函数清单，并检查 Git 状态。当前分支创建时工作树干净；先前去 Coze 重构已经提交并合并，不覆盖用户未提交工作。后续修改限于可复现问题、边界控制、兼容依赖和必要工程文件。

安全部分沿用先前对相同重构内容的一次独立基线、架构和文件/网络路径审查，扫描 ID `80e4adbe-6acc-48ec-ace3-b529265ea9a3`。它记录的是修复前快照，覆盖为 partial；没有把本报告或新增测试冒称为对每个 OpenXML 分支的穷尽安全证明。

## B. 整体质量评价

- **正确性**：文档引擎已有细分模块与多个真实模板，但边界值、重复字段名、扩展行、旧报告继承存在可复现缺陷。本轮增加具体回归用例。
- **安全性**：修复前下载白名单过宽、远程代理无目标限制、段落预览有遗漏转义路径。现在采用共享文档边界与受控远程来源。当前仍不是多租户安全系统。
- **架构**：现有 FastAPI + LangGraph + 填充子模块适合当前规模；没有必要整体换框架或引入任务平台。主要缺口是身份/资源归属、状态持久化和生命周期。
- **性能**：异步路由中有同步模型、文档、进程调用；字段合并存在重复全量扫描。已移入线程池、索引合并、复用分析与内存序列化。线程池不等于可强制终止解析的沙箱。
- **可维护性**：过时架构文档错误描述 SQLite、文件 checkpointer 及不存在的 API；已按当前代码重写。大型模板启发式函数和单文件前端仍保留，避免无依据大改。
- **工程**：原有14项测试提供基本回归；新增测试、Ruff正确性规则、前端语法/链接检查与跨平台CI。尚未实施全库严格类型检查，也未机械清理所有风格警告。

## C. 问题与优先级

没有确认 P0 级问题。下列位置用模块/函数锚点，描述修复前根因；受影响代码已变更的，不能把旧行号当作当前漏洞定位。

| 优先级 | 位置 | 已确认问题、影响 | 处理与收益/成本/风险 |
| --- | --- | --- | --- |
| P1 | main._safe_path / download-docx | 允许整个源码工作区、整个系统临时目录，任意文件直接返回；部署后配置文件可能泄漏 | 已修复：共享文档目录及扩展名边界。成本低；旧全局临时路径需重新上传，有意收紧危险行为 |
| P1 | main.http_download_docx | 任意HTTP目标与自动重定向，读取内网响应；无响应字节上限 | 已修复：精确来源白名单、禁用重定向、流式10MB限制与独立临时文件清理。S3代理需配置受信来源 |
| P1 | docx_preview._render_paragraph_with_fields | 插入字段span前保留了未经转义的段落文本，模板内容可进入innerHTML | 已修复：先转义原文再插入受控markup。成本低，保留字段编辑结构 |
| P1 | form_filling_state.bulk_fill / main.cofill_generate | truthiness检查丢弃0和False | 已修复：仅把None/空字符串视为空值；明确field_id优先。低风险，有端到端值验证 |
| P1 | filling.row_group_filler | 扩展行复制已填模板、反复插在同一锚点，导致重复旧值/乱序；前一行组插入使后续坐标错位 | 已修复：保留未填原型、移动锚点、按起始行倒序处理组。成本中，含多组和顺序回归 |
| P1 | edu_report_tool.generate_form_document / colon_filler / paragraph_filler | label映射覆盖同名日期等值，ID值被label优先级遮蔽 | 已修复：携带ID映射并优先精确ID。兼容label输入，减少错误位置填写 |
| P1 | agents._load_tools / old_report_extractor | 旧报告工具使用独立字典，无法获取填写会话；纯值单元格无标签时被丢弃；匹配结果类型用错导致prefilled_fields为空 | 已修复：共享现有状态引用、保留无前缀纯值、正确构造已填结果。新增真实预填回归 |
| P1（远程多用户） | service._config / 全局表单状态 | 客户端提供session_id即可选择共享历史，没有用户身份与资源归属绑定 | 保留：需要选择认证及租户模型，不能把并发保护当权限系统；远程开放前必须解决 |
| P2 | paragraph_filler | 正则替换把Windows路径等值里的反斜杠当替换语法，报错或改值 | 已修复：使用callable replacement，按字面文本写入 |
| P2 | form_filling_state | 空key可匹配空raw_label、空确认列表确认全部、序列化丢session、重初始化遗留行组 | 已修复：明确空值和None语义、保存会话、清除旧组值 |
| P2 | prefill_tool._merge_multi_file_results | 不稳定set顺序、重复扫描、修改输入、结构化value无法放入set | 已修复：一次索引、稳定顺序、复制候选、结构化值比较；含本机基准 |
| P2 | main异步路由 / filling.doc_builder | 同步解析/模型/LibreOffice阻塞事件循环；生成时不必要临时磁盘往返；重复模板分析 | 已修复：线程池、BytesIO与复用已计算analysis。生成字节接口保持一致 |
| P2 | upload接口 | 完整read后才验大小、无数量/总请求限制、压缩内容展开无界、长期保存无配额 | 已部分修复：读/接收/数量/ZIP/上传存储配额；不自动删除用户文件。生成目录长期保留仍待设计 |
| P2 | docx_upload | S3上传发生在XML自动修复之前，远端与本地预览不一致；修复后校验状态仍旧 | 已修复：先修复重验，再发布最终字节；模拟S3验证字节一致 |
| P2 | service / JSON API | 同一会话并发修改、错误SSE仍标成功、非对象JSON触发异常 | 已修复：会话占用保护、错误不发成功结束帧、请求对象/类型校验；保持独立run_id取消 |
| P2 | dependency manifests / PDF提取 | 大量未使用依赖及双重PDF库；旧PyPDF2仍在实际使用，锁定版本有已知漏洞匹配 | 已升级兼容补丁/次版本，PDF统一pypdf；保留高风险跨代依赖问题见下文 |
| P2 | error_handler / 工程文档 | 导入即写固定平台日志目录、基础设施错误串返回用户、文档与实际不符 | 已移除模块级独立日志初始化，隐藏边界内基础设施细节，更新真实架构与运行文档 |
| P3 | 大型前端及模板启发式 | 长函数、重复模板注册表和部分未用导入 | 暂留：大规模拆分缺少独立用户收益证据，先维持回归资产；后续可按功能逐块拆 |

## D. 实际实施

新增 `document_access.py`、`http_limits.py`；更新Web入口、图服务、Agent工具状态连接、字段/行组/段落填写、旧报告提取、多文件预填、存储校验、知识解析及错误边界。
新增 `test_review_regressions.py`、`check_frontend.cjs`，建立 `.github/workflows/tests.yml`。更新依赖声明、锁文件、requirements、环境示例和架构/迁移文档。

兼容性保留：路由名称、会话字段、主要JSON结果、前端工具delta、本地默认存储、可选S3/PostgreSQL与单worker约束。明确变化：禁止下载源码/配置/全局临时文件；远程代理来源必须配置；损坏Word返回422；超限上传返回客户端错误；旧doc/xls明确要求转换；同会话并发修改返回冲突或SSE错误。

## E. 验证结果

验证环境为本机Windows/Python 3.13.3。未把新增Linux/Python3.12 CI配置当作已经执行的结果；最终CI状态见PR的Actions检查。

- 基线：`uv run --frozen pytest -q --disable-warnings`：14项通过。
- 最终本地全量：`uv run --frozen pytest -q --disable-warnings`，45项通过、7项警告。
- `uv run --frozen ruff check src tests scripts`：通过。规则为E9/F63/F7/F82，主要阻断明确错误；不是全规则风格评级。
- `node tests/check_frontend.cjs`：通过，检查SPA JavaScript语法、本地下载链接和文本转义。
- `uv run --frozen python -m compileall -q src`、`uv lock --check`、`git diff --check`：通过。
- 覆盖上传/预览/手工生成/下载、6个仓库模板生成且源模板字节不变、真实嵌套LangGraph模拟模型会话、工具事件、取消、同会话并发、旧报告继承、边界值、行组、多文件合并、远程重定向/大小限制、压缩条目/请求限制、模拟S3发布一致性。
- 为验证性能，使用相同合成数据、1000字段×3文件、每版本5次，排除数据复制的计时部分。旧实现中位96.24ms，新实现4.66ms。不能外推到网络/模型耗时或全部真实工作负载。
- 无真实凭据被打印、验证或提交；没有调用付费模型、真实云存储/数据库或执行生产迁移。

升级过程中曾复现`langgraph-prebuilt` 1.0.9/1.0.13与保留的LangGraph缺少ExecutionInfo的导入冲突，未提交该失败组合。当前固定为LangGraph 1.0.10 + prebuilt 1.0.8，经导入与完整图测试确认。

### 依赖审计

本地执行 `uv tool run pip-audit -r requirements.txt --no-deps --disable-pip --format json`，审计全部已导出的生产依赖。该命令返回1意味着仍命中漏洞条目，不是运行测试失败。
审计记录匹配从102条降至9条，去重ID从65个降至5个，受影响包从16个降至4个。数据源存在同一ID的重复条目，因此不把原始条目数描述为互相独立的漏洞或可利用攻击路径。摘要见 [DEPENDENCY_AUDIT_SUMMARY.json](DEPENDENCY_AUDIT_SUMMARY.json)。

| 剩余依赖 | 原因与限制 |
| --- | --- |
| langchain 1.0.3 | 修复线要求较新LangGraph，涉及整体框架/持久化组合；未单独强升 |
| langgraph-checkpoint 3.0.1 | 修复版本跨到4.x，需评估已有checkpoint数据与序列化兼容性 |
| langgraph-checkpoint-postgres 3.0.5 | 修复线依赖checkpoint4；未做有风险的生产数据迁移 |
| langgraph-sdk 0.3.15 | 修复版本要求0.4.x，新版本受当前保守LangGraph约束；应用未直接调用远程SDK API，不据此宣称可利用 |

lxml采用6.1.3属于主版本变化，但使用边界为python-docx/OpenXML稳定操作，不涉及持久化模式迁移；6个仓库模板、结构读写与回归用例均验证通过。checkpoint4迁移风险更高，留给专门升级分支。

## F. 尚未解决的事项

1. **远程身份与资源归属（P1）**：无登录/owner绑定，已知session_id仍可选择同一图历史，字段状态也未租户隔离。服务仍按AGENTS约束监听0.0.0.0。身份认证网关可以限制访问，但多人互不信任时还需应用内归属校验。
2. **剩余5个依赖审计ID（P1/P2，依实际路径判定）**：保留且记录，未宣称依赖无漏洞。下一轮需连同checkpoint4一起进行可回滚迁移验证。
3. **资源生命周期（P2）**：生成文件、分析缓存和完成会话的内存保留无统一TTL/配额；本轮只约束请求、解析与上传目录，不自动删用户资料。线程池无法强制终止底层同步解析/模型调用。
4. **可选数据库（P2）**：PostgreSQL真实连接/恢复/池关闭/首次同步建表耗时未联调；字段状态未持久化，不能增加worker数。SQLAlchemy备用模块仍保留以避免删除潜在外部调用接口。
5. **解析精度与格式（P2）**：启发式算法对所有任意Word的字段识别没有保证；例如单行“分数”可能被当作表头而跳过，未据此改写整套算法。旧报告段落字段兼容路径也需更多真实样本。本文的“生成通过”不等于逐页视觉保真或AI事实准确性通过。
6. **错误与类型规范（P2/P3）**：部分历史工具保留自定义异常字符串；未建立完整静态类型门禁。保持弃用API所产生的警告，待框架迁移时统一解决。
7. **外部集成**：未验证真实模型质量、实际S3/PostgreSQL、LibreOffice PDF和历史21用例付费评测，不复用历史准确率作为本轮结论。

## G. 后续升级路线

| 顺序 | 工作 | 收益/成本/风险 | 是否必要 |
| --- | --- | --- | --- |
| 1 | 明确本地单用户或远程多用户；远程时加入认证和owner范围 | 安全收益高，成本中，需要产品身份决策 | 远程开放前必要 |
| 2 | 独立分支升级LangChain/LangGraph/checkpoint4，准备现有数据库副本、序列化回滚测试 | 处理剩余依赖告警，成本中高，有持久化兼容风险 | 必要，专门验证后执行 |
| 3 | 文档保留/备份/配额、会话缓存生命周期与持久化 | 控制长期资源增长，成本中，清理策略必须保护用户资料 | 长期部署必要 |
| 4 | 扩大真实模板fixture与结构/视觉/内容正确性回归 | 提高解析和格式可信度，成本中 | 持续维护必要 |
| 5 | 分块拆前端、集中模板注册表、逐步引入严格类型 | 改善维护，成本中，当前收益低于上述问题 | 可选渐进 |

## 参考资料

- [LangGraph官方迁移说明](https://docs.langchain.com/oss/python/migrate/langgraph-v1)：弃用Agent工厂有明确替代，但本轮保留旧接口以降低行为变化。
- [LangGraph官方安全公告](https://github.com/langchain-ai/langgraph/security/advisories)：用于校准依赖升级与持久化风险。
- [PyPDF2项目页](https://pypi.org/project/PyPDF2/)：原PDF路径已迁往现有pypdf依赖。
