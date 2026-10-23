# Leader-worker 第一轮实现与验证

日期：2026-10-23。分支：`experiment/leader-worker`；基线：`dec854b`。

## 已实现

`AGENT_MODE`支持`workflow`和`leader_worker`，默认仍为旧版。新版采用`prepare → leader ↔ dispatch → finish`的有界循环；Leader根据工具结果自行决定下一步，没有固定的三个业务阶段边。

- 提取Worker：按文件隔离上下文、最多3路并发，返回带连续原文引用的事实。数值/标识符不能用另一个值的子串冒充来源。
- 匹配Worker：候选包含精确字段ID、证据ID和值置信度；同标签来源冲突、未知ID、缺证据、低置信和直接事实遗漏均阻止提交。
- 单一写入：Worker不直接修改字段；提交候选或本轮用户明确给出的修改才能改变草稿。清空操作同样要求用户明确表达。
- 生成门禁：材料失败、未提交候选、来源冲突、无已填值不能生成；写入后检查内容与OpenXML，再发布文件。字段缺失不能仅靠`success`标记绕过。
- 会话与预算：状态随图checkpoint保存，命名空间为`leader_worker:<session_id>`；修改、重新提取或模板变化使旧产物失效。每轮最多8次Leader决策、12次工具执行。
- 兼容入口：保留`/run`、`/stream_run`、`/v1/chat/completions`及取消接口。实验SSE输出工具结果和校验后的最终文本，隐藏内部Worker JSON和未经校验的模型成功声明。

## 冒烟测试

测试使用脚本模型驱动真实LangGraph、Worker代码、checkpoint和Word引擎，不调用付费模型。覆盖：

| 场景 | 实际断言 |
| --- | --- |
| 正常填写、0值 | 下载文件的姓名、成绩单元格精确正确 |
| 同会话修改 | 仅执行用户修改和生成，不重新调用提取/匹配Worker |
| 跨会话隔离 | 新会话没有上一会话的值、产物或消息 |
| 两份材料冲突 | 无产物，不报告生成完成 |
| 匹配遗漏已知事实 | 阻止仅填部分已知值就生成 |
| Worker暂时失败 | 重试成功后解除待处理材料状态，再生成 |
| 无意义循环 | 达到调度预算后保留草稿并结束 |
| 写入引擎没有实际填值 | 内容检查失败，不发布文件 |
| 越权/无依据修改 | 整批拒绝，草稿不变 |
| 新材料被目录规则拒绝 | 旧下载失效，禁止忽略失败材料继续交付 |
| HTTP/SSE和取消 | 保留兼容输出、下载可用，取消后释放任务及会话锁 |
| 数字/学号子串 | 0不能从100中作为独立值提取 |

运行命令：

```powershell
uv run --frozen pytest -q --disable-warnings
uv run --frozen ruff check src tests scripts
node tests/check_frontend.cjs
uv run --frozen python -m compileall -q src scripts
git diff --check
```

本机Windows/Python 3.13执行结果：**74 passed，7 warnings**，其中新增实验测试15项，原59项全部保留通过。Lint、前端检查、源码编译及diff检查均通过。GitHub检查结果见该实验PR，不把本地结果当作已完成跨平台验收。

## 真实模型对比

新增 `scripts/compare_agent_modes.py`，使用相同示例、模型和180秒单轮上限，对比正常生成与日期冲突两类任务。独立记录关键单元格、应留空区域、是否澄清、调用数、耗时和模型返回的Token；Token不完整时记录null，不冒称零成本。

```powershell
uv run --frozen python scripts/compare_agent_modes.py --live
```

本轮执行结果为 **not_run**：本地模型密钥为空。结果存于被Git忽略的`data/leader-worker-compare/`。没有使用聊天中出现的密钥，没有发起真实付费A/B请求。
因此目前只确认新版控制与文档处理链路通过离线回归，**不能认定准确率、速度或Token成本优于旧版**。

## 运行与回退

```powershell
# 在projects/，模型使用本地.env已有配置
$env:AGENT_MODE = 'leader_worker'
uv run --frozen python src/main.py -p 5000

# 停止服务后回退
$env:AGENT_MODE = 'workflow'
uv run --frozen python src/main.py -p 5000
```

切换需要重启。两种模式的checkpoint分开；原有三阶段内部`node_run`名称只适用于workflow，新版内部节点名不同。现有上传、人工填写和下载路由不受模式开关影响。

## 后续边界

首版限制正文12000字符、每批10文件、匹配200事实。引用存在不代表语义必然正确；同义标签的复杂冲突、长材料分页及跨表行组的精确定位仍需扩充验证。
沿用现有模板识别器，例如单独“分数”可能被识别为表头；新增Leader不会自动修复这类识别精度问题。首轮测试fixture因此改用“成绩”，以隔离验证调度和写入门禁。
含行组时部分内容采用全文存在检查，不能证明每个值都在正确格子；无行组的普通字段做目标位置检查，复选框检查对应选项标记。结构检查不能替代逐页视觉验收。
原有身份/多用户归属、文件生命周期、依赖告警与PostgreSQL等限制仍在，未借本次实验进行迁移。

## 继续工作清单

1. 本地模型配置就绪后运行两种模式的真实A/B，记录失败，不自动重试到“最好结果”。
2. 根据失败增加fixture，优先修正真实的工具选择或语义匹配问题。
3. 证据支持后再决定扩大试验或切换默认模式；不自动合并main。

## 迭代2：确认后继续与可修正的Worker反馈

日期：2026-10-23。本轮先用失败回归复现两处问题，再修改：

- 确认单个冲突字段会删除未提交的候选，原实现可能只生成用户确认的一个值、漏掉其余材料。新增`needs_matching`状态；确认冲突后须重新匹配、提交其余事实。已提交文档的局部修改仍可直接重新生成。
- 用户原话为“成绩改为100”时，原来的子串检查会错误接受0。用户修改现在复用完整数字/标识符的引用边界检查。
- Worker输出格式、引用不合格时返回受控的具体反馈；重试提取附上该材料上次错误，重试匹配附上候选错误和用户已确认值。供应商原始异常不转发给Leader。
- 实测脚本增加`--case`，保存最终答复、工具失败和候选错误；每个场景开始和结束均写入结果，手动中断保留已完成记录。

本轮本地验证：`uv run --frozen pytest -q --disable-warnings` → **78 passed，7 warnings，39.36秒**。新增4项回归；Lint、前端语法/下载链接/转义检查、源码编译和`git diff --check`通过。测试仍使用离线脚本模型。

本机已将被Git忽略的`.env`设为`AGENT_MODE=leader_worker`；DeepSeek地址和`deepseek-flash`模型配置保留。执行以下真实测试命令，结果仍为 **not_run**，缺失项明确为`EXTERNAL_LLM_API_KEY`，没有发生真实模型调用：

```powershell
cd 'D:\FormWise临时文件\doc-auto-filler\projects'
& 'D:/FormWise临时文件/.tools/uv.exe' run --frozen python scripts/compare_agent_modes.py --live --mode leader_worker
# 仅重新验证某一个场景时追加：--case single_document 或 --case conflicting_dates
```

本轮证据：`data/leader-worker-compare/20261023T023338767895Z/results.json`（被Git忽略）。两场景分别验证6个关键单元格及指定留空区域、日期冲突时追问且不发布产物。密钥在本地`.env`配置完成后执行上述命令即可继续；当前不能宣称新版已通过真实模型测试。
