"""内置模板数据扩展：考勤映射 + 分数段自动计算。

与 data_expander.py 的区别：
- 此模块用于内置模板（教材建设申报书、试卷分析等），
  有明确的子字段后缀列表和考勤/分数段业务逻辑
- data_expander 用于通用模板（用户上传的 .docx），
  使用自动前缀匹配规则

依赖：tools.template_analyzer (analyze_template)
"""

from typing import List, Dict, Any, Optional
from tools.template_analyzer import analyze_template


def simplify_fields(label_fields):
    """将内部字段列表简化为用户友好的字段列表。

    将 _第1列 等子字段归组到基础字段下，考勤字段自动合并为"考勤数据"组。
    """
    sub_suffixes = ["_第1列", "_第2列", "_第3列", "_第4列", "_第5列",
                    "_一题", "_二题", "_三题", "_四题", "_五题",
                    "_六题", "_七题", "_八题", "_九题",
                    "_<60", "_60-69", "_70-79", "_80-89", "_90-100",
                    "_应到", "_实到", "_缺考", "_缓考", "_作弊", "_取消考试资格"]

    groups = {}
    field_order = []

    for f in label_fields:
        label = f["label"]
        base = None
        suffix = None
        for s in sub_suffixes:
            if label.endswith(s):
                base = label[:-len(s)]
                suffix = s[1:]
                break

        if base and suffix:
            if base in groups and groups[base]["type"] == "group":
                groups[base]["sub_labels"].append(suffix)
                groups[base]["sub_fields"].append(f)
            elif base in groups and groups[base]["type"] == "single":
                existing = groups[base]["field"]
                groups[base] = {
                    "type": "group",
                    "base_label": base,
                    "sub_fields": [existing, f],
                    "sub_labels": [base, suffix],
                }
            else:
                if base not in groups:
                    field_order.append(base)
                groups[base] = {
                    "type": "group",
                    "base_label": base,
                    "sub_fields": [f],
                    "sub_labels": [suffix],
                }
        else:
            if label not in groups:
                field_order.append(label)
                groups[label] = {
                    "type": "single",
                    "base_label": label,
                    "field": f,
                }

    user_fields = []
    for label in field_order:
        g = groups[label]
        if g["type"] == "single":
            f = g["field"]
            user_fields.append({
                "field_id": f["field_id"],
                "label": f["field_id"],
                "raw_label": f["label"],
                "description": f"{f['label']} - 请填写{f['label']}",
                "fill_mode": f["fill_mode"],
            })
        else:
            subs = g["sub_labels"]
            base = g["base_label"]

            attendance_suffixes = {"应到", "实到", "缺考", "缓考", "作弊", "取消考试资格"}
            has_attendance = any(s in attendance_suffixes for s in subs)

            if has_attendance and base != "考勤":
                internal_suffixes = {"第1列", "第2列", "第3列", "第4列", "第5列"}
                base_fields = [sf for sf in g["sub_fields"]
                              if not any(sf["label"].endswith(f"_{s}") for s in attendance_suffixes)
                              and not any(sf["label"].endswith(f"_{s}") for s in internal_suffixes)]
                for sf in base_fields:
                    user_fields.append({
                        "field_id": sf["field_id"],
                        "label": sf["field_id"],
                        "raw_label": sf["label"],
                        "description": f"{sf['label']} - 请填写{sf['label']}",
                        "fill_mode": sf["fill_mode"],
                    })
                att_labels = [s for s in subs if s in attendance_suffixes]
                if att_labels:
                    att_fids = [sf["field_id"] for sf in g["sub_fields"]
                                if any(sf["label"].endswith(f"_{s}") for s in att_labels)]
                    user_fields.append({
                        "label": "考勤数据",
                        "description": "请填写考勤数据：应到、实到、缺考、缓考、作弊、取消考试资格人数",
                        "fill_mode": "group",
                        "sub_labels": att_fids,
                        "sub_field_ids": att_fids,
                    })
            else:
                sub_fids = [sf["field_id"] for sf in g["sub_fields"]]
                user_fields.append({
                    "label": base,
                    "description": f"请填写{base}的相关数据",
                    "fill_mode": "group",
                    "sub_labels": sub_fids,
                    "sub_field_ids": sub_fids,
                })

    return user_fields


def expand_report_data(template_path: str, user_data: dict, analysis_result: dict = None) -> dict:
    """将用户友好的字段数据扩展为完整的内部字段数据。

    处理逻辑：
    1. 逗号分隔的分组字段（如"及百分比": "15,15,20,..."）自动展开为子字段
    2. 勾选框字段（如"课程性质": "必修"）由 fill_checkbox_rows_in_table 处理，此处原样保留
    3. 考勤数据自动映射到子字段
    4. 分数段比例自动计算（如果用户未提供）

    Returns:
        dict: {内部字段名: 值} 的字典，可直接用于填充
    """
    analysis = analysis_result or analyze_template(template_path)
    label_fields = analysis["label_fields"]

    sub_suffixes = ["_第1列", "_第2列", "_第3列", "_第4列", "_第5列",
                    "_一题", "_二题", "_三题", "_四题", "_五题",
                    "_六题", "_七题", "_八题", "_九题",
                    "_<60", "_60-69", "_70-79", "_80-89", "_90-100",
                    "_应到", "_实到", "_缺考", "_缓考", "_作弊", "_取消考试资格"]

    base_to_subs = {}
    for f in label_fields:
        label = f["label"]
        for s in sub_suffixes:
            if label.endswith(s):
                base = label[:-len(s)]
                if base not in base_to_subs:
                    base_to_subs[base] = []
                base_to_subs[base].append({"full_label": label, "suffix": s[1:], "field": f})
                break

    expanded = {}
    attendance_suffixes = {"应到", "实到", "缺考", "缓考", "作弊", "取消考试资格"}

    # 先处理独立的考勤字段（应到=45, 实到=43等），映射到 full_label
    for key, value in list(user_data.items()):
        if key in attendance_suffixes:
            for base_name, subs in base_to_subs.items():
                for sub in subs:
                    if sub["suffix"] == key:
                        expanded[sub["full_label"]] = str(value)
                        if key in user_data:
                            del user_data[key]

    for key, value in user_data.items():
        if key == "考勤数据":
            if isinstance(value, dict):
                for att_key, att_val in value.items():
                    for base_name, subs in base_to_subs.items():
                        for sub in subs:
                            if sub["suffix"] == att_key:
                                expanded[sub["full_label"]] = str(att_val)
            elif isinstance(value, str) and "," in value:
                parts = [p.strip() for p in value.split(",")]
                for base_name, subs in base_to_subs.items():
                    att_subs = [s for s in subs if s["suffix"] in attendance_suffixes]
                    att_subs.sort(key=lambda s: ["应到", "实到", "缺考", "缓考", "作弊", "取消考试资格"].index(s["suffix"]))
                    for i, sub in enumerate(att_subs):
                        if i < len(parts):
                            expanded[sub["full_label"]] = parts[i]
            continue

        if key in base_to_subs:
            subs = base_to_subs[key]
            has_attendance = any(s["suffix"] in attendance_suffixes for s in subs)

            if has_attendance:
                non_att_subs = [s for s in subs if s["suffix"] not in attendance_suffixes]
                if isinstance(value, str) and "," in value and non_att_subs:
                    parts = [p.strip() for p in value.split(",")]
                    for i, sub in enumerate(non_att_subs):
                        if i < len(parts):
                            expanded[sub["full_label"]] = parts[i]
                else:
                    expanded[key] = value
            elif isinstance(value, list) and len(value) == len(subs):
                for i, sub in enumerate(subs):
                    expanded[sub["full_label"]] = value[i]
            elif isinstance(value, str) and "," in value:
                parts = [p.strip() for p in value.split(",")]
                for i, sub in enumerate(subs):
                    if i < len(parts):
                        expanded[sub["full_label"]] = parts[i]
            else:
                expanded[key] = value
        else:
            expanded[key] = value

    # 自动计算分数段比例（如果用户未提供但有分数段人数）
    dist_bases = [b for b in base_to_subs if b.startswith("分数分布")]
    for base_name in dist_bases:
        subs = base_to_subs[base_name]
        ratio_subs = [s for s in subs if s["suffix"].startswith("第")]
        count_subs = [s for s in subs if s["suffix"] in ["<60", "60-69", "70-79", "80-89", "90-100"]]

        if ratio_subs and count_subs:
            has_ratio = any(s["full_label"] in expanded for s in ratio_subs)
            if not has_ratio:
                total = 0
                counts = []
                for s in count_subs:
                    v = expanded.get(s["full_label"], "0")
                    try:
                        n = float(v)
                    except (ValueError, TypeError):
                        n = 0
                    counts.append(n)
                    total += n

                if total > 0:
                    for i, s in enumerate(ratio_subs):
                        if i < len(counts):
                            pct = counts[i] / total * 100
                            expanded[s["full_label"]] = f"{pct:.1f}%"

    return expanded