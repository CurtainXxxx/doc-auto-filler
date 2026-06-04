"""
prefill_tool.py — AI预填工具：从知识文件自动提取字段值并生成预填结果

核心流程：
1. 解析知识文件内容（复用 knowledge_tool 的文件提取能力）
2. 获取模板字段清单（复用 template_analyzer 的分析能力）
3. LLM智能提取字段值 + 评估置信度
4. 规则匹配兜底
5. 多文件结果合并
6. 返回带置信度的预填结果，供前端审核界面展示
"""

import os
import re
import json
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
_workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
load_dotenv(os.path.join(_workspace, ".env"), override=True)

from langchain.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

from tools.knowledge_tool import _extract_text_from_file, _rule_extract_fields
from tools.template_analyzer import analyze_template


# ── 置信度阈值 ──
CONFIDENCE_CONFIRMED = 0.8    # ≥ 此值标记为 confirmed（绿）
CONFIDENCE_REVIEW = 0.4       # ≥ 此值标记为 review（黄），< 此值标记为 empty（灰）

# ── 单次LLM提取最大字段数（防止prompt过长） ──
MAX_FIELDS_PER_LLM_CALL = 40


def _classify_status(confidence: float) -> str:
    """根据置信度分类字段状态。"""
    if confidence >= CONFIDENCE_CONFIRMED:
        return "confirmed"
    elif confidence >= CONFIDENCE_REVIEW:
        return "review"
    else:
        return "empty"


def _llm_prefill_fields(field_list: list, file_content: str, ctx=None) -> list:
    """使用LLM从文件内容中提取字段值，带置信度评估。

    与 knowledge_tool._llm_extract_fields 的区别：
    - 返回带置信度的结构化结果（不只是 {字段: 值}）
    - 分批处理大量字段（避免prompt过长）
    - 更严格的提取Prompt（明确区分直接提取/推断/猜测）

    Args:
        field_list: 字段信息列表，每个元素为 {"label": str, "field_id": str}
        file_content: 知识文件文本内容
        ctx: 请求上下文

    Returns:
        list: [{"label", "field_id", "value", "confidence", "source"}]
    """
    # 限制文件内容长度
    max_chars = 10000
    if len(file_content) > max_chars:
        file_content = file_content[:max_chars] + "\n...(内容过长已截断)"

    # 分批处理
    all_results = []
    batches = [field_list[i:i + MAX_FIELDS_PER_LLM_CALL]
               for i in range(0, len(field_list), MAX_FIELDS_PER_LLM_CALL)]

    system_prompt = """你是一个教务文档信息提取专家，擅长从教学大纲、成绩单、课程计划等文件中精确提取结构化信息。

# 任务
根据模板字段清单，从提供的文件内容中提取每个字段的值，并评估置信度。

# 置信度定义
- 1.0：直接提取 — 文件中有完全匹配的原文（如标题写"数据结构与算法"→课程名称）
- 0.8：近似提取 — 文件中有语义相近的表述（如"专业核心课"→课程性质=必修）
- 0.6：合理推断 — 从多个信息综合推断（如多门课都是闭卷→推断考试形式）
- 0.3：低置信猜测 — 仅有微弱依据的猜测
- 0.0：未找到 — 文件中完全无关

# 提取规则
1. 直接提取：文件中有明确原文的，直接引用，confidence ≥ 0.8
2. 合理推断：文件中有间接依据的，标注推断逻辑，confidence 0.4-0.7
3. 无法提取：文件中完全无关的，value填null，confidence 0
4. 绝不编造不存在的值
5. 数字类字段只提取数字，不添加单位
6. 日期字段保持原文格式

# 输出格式
严格返回JSON数组，每个元素：
{
  "label": "字段名",
  "value": "提取的值或null",
  "confidence": 0.0到1.0,
  "source": "信息来源描述"
}"""

    for batch in batches:
        fields_desc = "\n".join(
            f"- {f['label']} (field_id: {f['field_id']})"
            for f in batch
        )

        user_prompt = f"""请从以下文件内容中提取这些字段的值：

字段清单：
{fields_desc}

文件内容：
{file_content}

请返回JSON数组，只返回JSON，不要其他文字。"""

        try:
            content_str = _call_llm(system_prompt, user_prompt, ctx)

            # 解析JSON
            batch_result = _parse_json_array(content_str)
            if batch_result:
                # 与输入字段匹配
                for f in batch:
                    matched = _find_matching_result(batch_result, f['label'], f['field_id'])
                    if matched:
                        all_results.append({
                            "label": f['label'],
                            "field_id": f['field_id'],
                            "value": matched.get("value"),
                            "confidence": float(matched.get("confidence", 0)),
                            "source": matched.get("source", ""),
                        })
                    else:
                        all_results.append({
                            "label": f['label'],
                            "field_id": f['field_id'],
                            "value": None,
                            "confidence": 0,
                            "source": "",
                        })
            else:
                # JSON解析失败，所有字段标记为空
                for f in batch:
                    all_results.append({
                        "label": f['label'],
                        "field_id": f['field_id'],
                        "value": None,
                        "confidence": 0,
                        "source": "",
                    })

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"LLM预填提取失败: {e}")
            for f in batch:
                all_results.append({
                    "label": f['label'],
                    "field_id": f['field_id'],
                    "value": None,
                    "confidence": 0,
                    "source": "",
                })

    return all_results


def _llm_prefill_row_groups(row_groups: list, file_content: str, ctx=None) -> dict:
    """使用LLM从文件内容中提取行组数据（动态表格行）。

    row_groups 是模板中识别出的动态行区域（如关联矩阵表中的数据行），
    每行的列数由模板定义，但行数取决于知识文件内容。

    Args:
        row_groups: 行组列表 [{"group_id": "T0_G0", "column_labels": [...], ...}]
        file_content: 知识文件文本内容
        ctx: 请求上下文

    Returns:
        dict: {group_id: [["col1", "col2", ...], ...]}
    """
    if not row_groups:
        return {}

    max_chars = 10000
    if len(file_content) > max_chars:
        file_content = file_content[:max_chars] + "\n...(内容过长已截断)"

    groups_desc = []
    for g in row_groups:
        cols = g.get("column_labels", [])
        groups_desc.append({
            "group_id": g["group_id"],
            "table_idx": g.get("table_idx", 0),
            "columns": cols,
            "header": g.get("header_text", " | ".join(cols)),
        })

    system_prompt = """你是一个教务文档信息提取专家，擅长从文件内容中提取结构化表格数据。

# 任务
根据提供的表格列定义，从文件内容中提取每一行的数据。

# 关键规则
1. 每行数据是一个数组，按列顺序排列，长度必须与列数一致
2. 仔细阅读全文，不要遗漏任何段落中的数据
3. 非常重要：文件中的数据可能是分多个段落描述的，例如：
   - 段落A说："一级指标1工程知识，二级指标1.2问题分析" → 提供了列1和列2
   - 段落B说："目标1支撑毕业要求1.2" → 提供了列3的支撑关系
   你需要把A和B的数据合并为一行：["工程知识", "1.2问题分析", "课程目标1"]
4. 按列1的值对行去重，同一行不要出现多次
5. 如果确实找不到某列的数据，不要编造，但尽量从全文搜索匹配

# 输出格式
严格返回JSON对象，键为group_id，值为二维数组:
{
  "T0_G0": [["列1值", "列2值", "列3值"]],
  "T1_G0": []
}"""

    groups_json = json.dumps(groups_desc, ensure_ascii=False)
    user_prompt = f"""请从以下文件内容中提取这些表格的数据。

表格定义：
{groups_json}

文件内容：
{file_content}

重要提示：
- 全文搜索，数据可能分散在多个段落
- 对于三列表格，如果列1列2在一个段落，列3（支撑/对应关系）在另一个段落，必须合并填充
- 例如：如果文中先说"指标A对应指标B"，后面说"目标X支撑指标B"，则整合为 ["A", "B描述", "课程目标X"]

请返回JSON对象，只返回JSON，不要其他文字。"""

    try:
        content_str = _call_llm(system_prompt, user_prompt, ctx)
        result = _parse_json_object(content_str)
        if result and isinstance(result, dict):
            # 后处理：尝试从文件内容中补全支撑关系等缺失列
            result = _post_process_row_groups(result, row_groups, file_content)
            return result
        return {}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"LLM行组提取失败: {e}")
        return {}


def _post_process_row_groups(rg_data: dict, row_groups: list, file_content: str) -> dict:
    """后处理行组数据：补全LLM未提取出或提取不完整的行组。

    两步策略：
    1. 如果LLM已返回行数据但某些列缺失 → 合并支撑关系文本补全
    2. 如果LLM返回空 → 直接从文件内容中用规则提取行数据
    """
    if not row_groups:
        return rg_data

    import re as _re

    for g in row_groups:
        gid = g["group_id"]
        rows = rg_data.get(gid, [])
        col_labels = g.get("column_labels", [])

        # 找到"支撑/对应"列索引
        support_col_idx = None
        for i, label in enumerate(col_labels):
            if any(kw in label for kw in ['支撑', '对应', '关系', '关联']):
                support_col_idx = i
                break

        # 收集支撑关系文本行
        support_lines = []
        for line in file_content.split('\n'):
            line = line.strip()
            if not line:
                continue
            if any(kw in line for kw in ['支撑', '对应', '关系']):
                support_lines.append(line)
        support_text = ' '.join(support_lines)

        # ── 情况1：LLM返回了空数据 → 尝试规则提取 ──
        if not rows:
            rows = _rule_extract_row_group_data(file_content, g)
            if rows:
                rg_data[gid] = rows

        # ── 情况2：补全缺失的支撑列 ──
        if support_col_idx is not None and rows:
            for row in rows:
                if support_col_idx < len(row) and row[support_col_idx] and str(row[support_col_idx]).strip():
                    continue

                search_keys = []
                for val in row:
                    if not val or not str(val).strip():
                        continue
                    nums = _re.findall(r'[\d]+\.[\d]+', str(val))
                    search_keys.extend(nums)

                if not search_keys:
                    continue

                matched_goals = []
                for key in search_keys:
                    escaped_key = _re.escape(key)
                    pattern = r'(?:课程)?目标\d+(?=[^，。；;]*?' + escaped_key + r')'
                    goals = _re.findall(pattern, support_text)
                    if goals:
                        matched_goals.extend(goals)

                if matched_goals:
                    unique_vals = list(dict.fromkeys(matched_goals))
                    while len(row) <= support_col_idx:
                        row.append("")
                    row[support_col_idx] = "、".join(unique_vals)

    return rg_data


def _rule_extract_row_group_data(file_content: str, row_group: dict) -> list:
    """当LLM无法提取行组数据时，用正则规则兜底提取。

    支持的格式：
    - "一级指标1工程知识，二级指标1.2问题分析" → 拆分为多行
    - "目标1对应第1、2题" → 拆分为多行
    """
    import re as _re
    col_labels = row_group.get("column_labels", [])
    num_cols = len(col_labels)

    # ── 判断表格类型 ──
    # 类型A：含有"一级指标"/"二级指标"等 → 毕业要求关联矩阵
    if any('一级指标' in c or '二级指标' in c for c in col_labels):
        return _extract_graduation_requirement_rows(file_content, num_cols)

    # 类型B：含有"课程目标"/"题号"等 → 课程目标关联表
    if any('课程目标' in c or '目标' in c for c in col_labels):
        return _extract_course_objective_rows(file_content, num_cols)

    return []


def _extract_graduation_requirement_rows(file_content: str, num_cols: int) -> list:
    """从文件中提取毕业要求关联矩阵行。"""
    import re as _re
    rows = []

    # 提取 "一级指标N名称，二级指标N.N名称" 模式
    pattern = r'一级指标(\d+)\s*([^，,;；。]+)[，,;；]\s*二级指标\s*([\d.]+)\s*([^，,;；。]+)'
    for m in _re.finditer(pattern, file_content):
        indicator_num = m.group(1)
        indicator_name = m.group(2).strip()
        sub_num = m.group(3).strip()
        sub_name = m.group(4).strip()

        if num_cols >= 3:
            rows.append([indicator_name, f"{sub_num}{sub_name}", ""])
        elif num_cols == 2:
            rows.append([indicator_name, f"{sub_num}{sub_name}"])

    return rows


def _extract_course_objective_rows(file_content: str, num_cols: int) -> list:
    """从文件中提取课程目标关联行。"""
    import re as _re
    rows = []

    # 提取 "目标N对应第X、Y题" 或 "目标N对第X、Y题"
    for line in file_content.split('\n'):
        line = line.strip()
        # 匹配 "目标N对应/对第X、Y题" 模式
        pattern = r'(?:课程)?目标(\d+)\s*(?:对应|对)\s*(.+)'
        m = _re.search(pattern, line)
        if m:
            obj_num = m.group(1)
            question_ref = m.group(2).strip().rstrip('。，,;；')
            if num_cols >= 2:
                rows.append([f"目标{obj_num}", question_ref])

    return rows


def _call_llm(system_prompt: str, user_prompt: str, ctx=None) -> str:
    """调用LLM，支持外部API和平台内置LLM。带重试逻辑。"""
    import time
    ext_api_key = os.getenv("EXTERNAL_LLM_API_KEY")
    ext_base_url = os.getenv("EXTERNAL_LLM_BASE_URL")

    if ext_api_key and ext_base_url:
        from langchain_openai import ChatOpenAI
        ext_model = os.getenv("EXTERNAL_LLM_MODEL", "deepseek-chat")
        from langchain_core.messages import SystemMessage as SM, HumanMessage as HM

        last_error = None
        for attempt in range(3):
            try:
                ext_llm = ChatOpenAI(
                    model=ext_model,
                    api_key=ext_api_key,
                    base_url=ext_base_url,
                    temperature=0.1,
                    max_tokens=4096,
                )
                response = ext_llm.invoke([SM(content=system_prompt), HM(content=user_prompt)])
                return response.content
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))  # 退避: 2s, 4s
        raise last_error
    else:
        from coze_coding_dev_sdk import LLMClient
        client = LLMClient(ctx=ctx)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response = client.invoke(
            messages=messages,
            model="doubao-seed-1-6-lite-251015",
            temperature=0.1,
            max_completion_tokens=4096,
        )
        content = response.content
        if isinstance(content, list):
            return " ".join(
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        elif isinstance(content, str):
            return content
        else:
            return str(content)


def _parse_json_array(text: str) -> list:
    """从LLM返回文本中解析JSON数组。"""
    text = text.strip()

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取JSON代码块
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if json_match:
        try:
            result = json.loads(json_match.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 尝试提取方括号内容
    bracket_match = re.search(r'\[[\s\S]*\]', text)
    if bracket_match:
        try:
            result = json.loads(bracket_match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return []


def _parse_json_object(text: str) -> dict:
    """从LLM返回文本中解析JSON对象。"""
    text = text.strip()

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试提取JSON代码块
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if json_match:
        try:
            result = json.loads(json_match.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 尝试提取花括号内容
    brace_start = text.find('{')
    if brace_start >= 0:
        brace_count = 0
        for i in range(brace_start, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    try:
                        result = json.loads(text[brace_start:i + 1])
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        pass
                    break

    return {}


def _find_matching_result(results: list, label: str, field_id: str) -> dict:
    """在LLM返回的结果列表中找到匹配label的结果。"""
    # 精确匹配label
    for r in results:
        if isinstance(r, dict) and r.get("label") == label:
            return r

    # 模糊匹配（去除空格、标点后比较）
    import unicodedata
    def _normalize(s):
        s = unicodedata.normalize('NFKC', str(s))
        s = re.sub(r'[\s\u3000：:，,。.、]', '', s)
        return s

    norm_label = _normalize(label)
    for r in results:
        if isinstance(r, dict):
            r_label = _normalize(r.get("label", ""))
            if r_label and (r_label == norm_label or norm_label in r_label or r_label in norm_label):
                return r

    return None


def _rule_prefill_fields(field_list: list, file_content: str) -> list:
    """使用规则匹配预填（兜底方案），给规则匹配的结果一个固定置信度。"""
    labels = [f['label'] for f in field_list]
    rule_extracted = _rule_extract_fields(labels, file_content)

    results = []
    for f in field_list:
        if f['label'] in rule_extracted:
            results.append({
                "label": f['label'],
                "field_id": f['field_id'],
                "value": rule_extracted[f['label']],
                "confidence": 0.75,  # 规则匹配给0.75置信度（中等偏高，但需审核）
                "source": "规则匹配",
            })
        else:
            results.append({
                "label": f['label'],
                "field_id": f['field_id'],
                "value": None,
                "confidence": 0,
                "source": "",
            })

    return results


def _merge_prefill_results(llm_results: list, rule_results: list) -> list:
    """合并LLM和规则匹配的预填结果，LLM优先。"""
    # 以LLM结果为基础
    merged = {r['field_id']: r for r in llm_results}

    # 规则匹配补充LLM未找到的字段
    for r in rule_results:
        fid = r['field_id']
        if fid not in merged or (merged[fid]['value'] is None and r['value'] is not None):
            merged[fid] = r
        elif merged[fid]['value'] is None and r['value'] is not None:
            # LLM没找到，但规则找到了
            merged[fid] = r

    return list(merged.values())


def _merge_multi_file_results(all_file_results: list) -> list:
    """合并多个文件的预填结果。

    策略：同一字段取置信度最高的值；冲突时标记为review。
    """
    if not all_file_results:
        return []

    # 收集所有field_id
    field_ids = set()
    for results in all_file_results:
        for r in results:
            field_ids.add(r['field_id'])

    merged = []
    for fid in field_ids:
        candidates = []
        for results in all_file_results:
            for r in results:
                if r['field_id'] == fid and r['value'] is not None:
                    candidates.append(r)

        if not candidates:
            # 所有文件都没找到
            label = ""
            for results in all_file_results:
                for r in results:
                    if r['field_id'] == fid:
                        label = r['label']
                        break
            merged.append({
                "label": label,
                "field_id": fid,
                "value": None,
                "confidence": 0,
                "source": "",
            })
        elif len(candidates) == 1:
            merged.append(candidates[0])
        else:
            # 多个文件都有值
            # 检查值是否一致
            values = set(r['value'] for r in candidates)
            if len(values) == 1:
                # 值一致，取置信度最高的
                best = max(candidates, key=lambda r: r['confidence'])
                sources = [r['source'] for r in candidates if r['source']]
                best['source'] = " + ".join(sources) if sources else best['source']
                merged.append(best)
            else:
                # 值冲突，取置信度最高的但降低置信度
                best = max(candidates, key=lambda r: r['confidence'])
                best['confidence'] = min(best['confidence'], 0.6)  # 冲突降级
                best['source'] = f"多文件有不同值({'+'.join(str(v)[:20] for v in values)})"
                merged.append(best)

    return merged


# ── 对外工具函数 ──

@tool
def prefill_from_knowledge(file_path: str, template_fields_json: str, row_groups_json: str = "", template_path: str = "") -> str:
    """从知识文件中提取模板字段值，生成带置信度的预填结果。

    用户上传知识文件（教学大纲、成绩单等）后，调用此工具自动提取所有字段的值。
    返回的预填结果包含置信度，前端据此展示不同颜色标记：
    - confirmed(绿): 置信度≥0.8，可直接使用
    - review(黄): 置信度0.4-0.8，需用户确认
    - empty(灰): 置信度<0.4，需用户手动填写

    Args:
        file_path: 知识文件路径
        template_fields_json: 模板字段清单JSON（来自analyze_template或analyze_uploaded_template的label_fields）
        row_groups_json: 模板行组清单JSON（可选。如果传了template_path，此参数可省略——会自动分析模板获取行组）
        template_path: 模板文件路径（可选。传入后自动分析模板获取行组，无需单独传row_groups_json）
    """
    ctx = request_context.get() or new_context(method="prefill_from_knowledge")

    try:
        # 1. 解析文件路径
        workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
        if not os.path.isabs(file_path):
            full_path = os.path.join(workspace, file_path)
        else:
            full_path = file_path

        if not os.path.exists(full_path):
            return json.dumps({"success": False, "message": f"文件不存在: {file_path}"}, ensure_ascii=False)

        # 2. 提取文件文本内容
        file_content = _extract_text_from_file(full_path)
        if file_content.startswith('['):
            return json.dumps({"success": False, "message": f"文件解析失败: {file_content}"}, ensure_ascii=False)

        # 3. 解析模板字段
        if isinstance(template_fields_json, str):
            template_fields = json.loads(template_fields_json)
        else:
            template_fields = template_fields_json

        # 构建 field_list
        field_list = []
        for f in template_fields:
            label = f.get("raw_label") or f.get("label", "")
            field_id = f.get("field_id", "")
            if label:
                field_list.append({"label": label, "field_id": field_id})

        if not field_list:
            return json.dumps({"success": False, "message": "字段清单为空"}, ensure_ascii=False)

        # 4. 自动获取行组（如果传了 template_path 但没传 row_groups_json）
        if not row_groups_json and template_path:
            try:
                analysis = analyze_template(template_path)
                row_groups = analysis.get("row_groups", [])
                if row_groups:
                    row_groups_json = json.dumps(row_groups, ensure_ascii=False)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"自动分析模板行组失败: {e}")
                row_groups_json = ""

        # 5. LLM智能提取
        llm_results = _llm_prefill_fields(field_list, file_content, ctx=ctx)

        # 6. 规则匹配兜底
        rule_results = _rule_prefill_fields(field_list, file_content)

        # 7. 合并结果
        merged = _merge_prefill_results(llm_results, rule_results)

        # 8. 标记状态
        for r in merged:
            r['status'] = _classify_status(r['confidence'])

        # 9. 统计
        confirmed = sum(1 for r in merged if r['status'] == 'confirmed')
        review = sum(1 for r in merged if r['status'] == 'review')
        empty = sum(1 for r in merged if r['status'] == 'empty')
        total = len(merged)

        result = {
            "success": True,
            "file_name": os.path.basename(full_path),
            "template_fields": total,
            "prefilled": confirmed + review,
            "needs_review": review,
            "still_empty": empty,
            "fill_rate": f"{confirmed + review}/{total}",
            "fill_rate_pct": round((confirmed + review) / total * 100, 1) if total > 0 else 0,
            "fields": merged,
            "summary": f"已预填 {confirmed + review}/{total} 个字段（{confirmed}个高置信，{review}个需审核，{empty}个未填）"
        }

        # 10. 提取行组数据
        if row_groups_json:
            try:
                if isinstance(row_groups_json, str):
                    row_groups = json.loads(row_groups_json)
                else:
                    row_groups = row_groups_json
                if row_groups:
                    rg_data = _llm_prefill_row_groups(row_groups, file_content, ctx=ctx)
                    row_group_info = []
                    for g in row_groups:
                        gid = g["group_id"]
                        row_group_info.append({
                            "group_id": gid,
                            "table_idx": g["table_idx"],
                            "column_labels": g.get("column_labels", []),
                            "template_row_count": g["template_row_count"],
                            "data": rg_data.get(gid, []),
                        })
                    result["row_groups"] = row_group_info
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"行组数据提取失败: {e}")

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"预填工具执行失败: {e}", exc_info=True)
        return json.dumps({"success": False, "message": f"预填失败: {e}"}, ensure_ascii=False)


@tool
def prefill_from_multiple_knowledge(file_paths_json: str, template_fields_json: str, row_groups_json: str = "", template_path: str = "") -> str:
    """从多个知识文件中联合提取模板字段值，生成带置信度的预填结果。

    与 prefill_from_knowledge 的区别：支持多个知识文件，自动合并结果。
    多文件场景下，同一字段的值取置信度最高的；冲突时降级标记。

    Args:
        file_paths_json: 知识文件路径列表JSON，如 '["/tmp/file1.docx", "/tmp/file2.pdf"]'
        template_fields_json: 模板字段清单JSON
        row_groups_json: 模板行组清单JSON（可选。如果传了template_path，此参数可省略）
        template_path: 模板文件路径（可选。传入后自动分析模板获取行组）
    """
    ctx = request_context.get() or new_context(method="prefill_from_multiple_knowledge")

    try:
        # 解析参数
        if isinstance(file_paths_json, str):
            file_paths = json.loads(file_paths_json)
        else:
            file_paths = file_paths_json

        if isinstance(template_fields_json, str):
            template_fields = json.loads(template_fields_json)
        else:
            template_fields = template_fields_json

        # 自动获取行组（如果传了 template_path 但没传 row_groups_json）
        row_groups = []
        if row_groups_json:
            if isinstance(row_groups_json, str):
                row_groups = json.loads(row_groups_json)
            else:
                row_groups = row_groups_json
        elif template_path:
            try:
                analysis = analyze_template(template_path)
                row_groups = analysis.get("row_groups", [])
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"自动分析模板行组失败: {e}")

        if not file_paths:
            return json.dumps({"success": False, "message": "未提供文件路径"}, ensure_ascii=False)

        # 构建 field_list
        field_list = []
        for f in template_fields:
            label = f.get("raw_label") or f.get("label", "")
            field_id = f.get("field_id", "")
            if label:
                field_list.append({"label": label, "field_id": field_id})

        if not field_list:
            return json.dumps({"success": False, "message": "字段清单为空"}, ensure_ascii=False)

        # 逐文件提取
        all_file_results = []
        all_rg_results = {}
        file_names = []
        for fp in file_paths:
            workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
            if not os.path.isabs(fp):
                full_path = os.path.join(workspace, fp)
            else:
                full_path = fp

            if not os.path.exists(full_path):
                continue

            file_content = _extract_text_from_file(full_path)
            if file_content.startswith('['):
                continue

            file_names.append(os.path.basename(full_path))

            # LLM + 规则 提取字段
            llm_results = _llm_prefill_fields(field_list, file_content, ctx=ctx)
            rule_results = _rule_prefill_fields(field_list, file_content)
            merged = _merge_prefill_results(llm_results, rule_results)
            all_file_results.append(merged)

            # LLM提取行组数据
            if row_groups:
                rg_data = _llm_prefill_row_groups(row_groups, file_content, ctx=ctx)
                all_rg_results.update(rg_data)

        if not all_file_results:
            return json.dumps({"success": False, "message": "所有文件解析失败"}, ensure_ascii=False)

        # 多文件合并
        final_results = _merge_multi_file_results(all_file_results)

        # 标记状态
        for r in final_results:
            r['status'] = _classify_status(r['confidence'])

        # 统计
        confirmed = sum(1 for r in final_results if r['status'] == 'confirmed')
        review = sum(1 for r in final_results if r['status'] == 'review')
        empty = sum(1 for r in final_results if r['status'] == 'empty')
        total = len(final_results)

        result = {
            "success": True,
            "file_names": file_names,
            "file_count": len(file_names),
            "template_fields": total,
            "prefilled": confirmed + review,
            "needs_review": review,
            "still_empty": empty,
            "fill_rate": f"{confirmed + review}/{total}",
            "fill_rate_pct": round((confirmed + review) / total * 100, 1) if total > 0 else 0,
            "fields": final_results,
            "summary": f"从{len(file_names)}个文件中预填 {confirmed + review}/{total} 个字段（{confirmed}个高置信，{review}个需审核，{empty}个未填）"
        }

        # 附加行组数据
        if row_groups:
            row_group_info = []
            for g in row_groups:
                gid = g["group_id"]
                row_group_info.append({
                    "group_id": gid,
                    "table_idx": g["table_idx"],
                    "column_labels": g.get("column_labels", []),
                    "template_row_count": g["template_row_count"],
                    "data": all_rg_results.get(gid, []),
                })
            result["row_groups"] = row_group_info

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"多文件预填工具执行失败: {e}", exc_info=True)
        return json.dumps({"success": False, "message": f"多文件预填失败: {e}"}, ensure_ascii=False)


# ── 非工具函数（供API直接调用） ──

def prefill_from_file_paths(file_paths: list, template_path: str) -> dict:
    """直接API调用：从文件路径列表和模板路径生成预填结果。

    不经过Agent，直接供 /prefill API 调用。

    Args:
        file_paths: 知识文件路径列表
        template_path: 模板文件路径

    Returns:
        dict: 预填结果
    """
    try:
        # 1. 分析模板字段和行组
        analysis = analyze_template(template_path)
        template_fields = analysis.get("label_fields", [])
        row_groups = analysis.get("row_groups", [])

        if not template_fields:
            return {"success": False, "message": "模板字段识别为空"}

        # 2. 构建field_list
        field_list = []
        for f in template_fields:
            label = f.get("raw_label") or f.get("label", "")
            field_id = f.get("field_id", "")
            if label:
                field_list.append({"label": label, "field_id": field_id})

        # 3. 逐文件提取
        all_file_results = []
        all_rg_results = {}
        file_names = []
        workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")

        for fp in file_paths:
            if not os.path.isabs(fp):
                full_path = os.path.join(workspace, fp)
            else:
                full_path = fp

            if not os.path.exists(full_path):
                continue

            file_content = _extract_text_from_file(full_path)
            if file_content.startswith('['):
                continue

            file_names.append(os.path.basename(full_path))

            # LLM + 规则 提取字段
            ctx = new_context(method="prefill_api")
            llm_results = _llm_prefill_fields(field_list, file_content, ctx=ctx)
            rule_results = _rule_prefill_fields(field_list, file_content)
            merged = _merge_prefill_results(llm_results, rule_results)
            all_file_results.append(merged)

            # LLM提取行组数据
            if row_groups:
                rg_data = _llm_prefill_row_groups(row_groups, file_content, ctx=ctx)
                all_rg_results.update(rg_data)

        if not all_file_results:
            return {"success": False, "message": "所有知识文件解析失败"}

        # 4. 多文件合并
        final_results = _merge_multi_file_results(all_file_results)

        # 5. 标记状态
        for r in final_results:
            r['status'] = _classify_status(r['confidence'])

        # 6. 统计
        confirmed = sum(1 for r in final_results if r['status'] == 'confirmed')
        review = sum(1 for r in final_results if r['status'] == 'review')
        empty = sum(1 for r in final_results if r['status'] == 'empty')
        total = len(final_results)

        # 构建行组信息（带列名，供前端和Agent使用）
        row_group_info = []
        for g in row_groups:
            gid = g["group_id"]
            row_group_info.append({
                "group_id": gid,
                "table_idx": g["table_idx"],
                "column_labels": g.get("column_labels", []),
                "template_row_count": g["template_row_count"],
                "data": all_rg_results.get(gid, []),
            })

        result = {
            "success": True,
            "template_name": os.path.basename(template_path),
            "file_names": file_names,
            "file_count": len(file_names),
            "template_fields": total,
            "prefilled": confirmed + review,
            "needs_review": review,
            "still_empty": empty,
            "fill_rate": f"{confirmed + review}/{total}",
            "fill_rate_pct": round((confirmed + review) / total * 100, 1) if total > 0 else 0,
            "fields": final_results,
            "row_groups": row_group_info,
            "summary": f"从{len(file_names)}个文件中预填 {confirmed + review}/{total} 个字段"
        }

        return result

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"API预填执行失败: {e}", exc_info=True)
        return {"success": False, "message": f"预填失败: {e}"}
