"""通用模板数据预处理：字段简化 + 归组数据展开。

提供两阶段处理：
1. simplify_generic_fields：将 92+ 个内部字段压缩为用户友好的少量归组字段
2. expand_generic_data：将用户输入的逗号分隔值展开为内部子字段名

与 builtin_expander.py 的区别：此模块用于通用模板场景，
builtin_expander 用于内置模板场景（含考勤映射、分数段自动计算）。

依赖：无（纯 Python 标准库）
"""

import re
from typing import List, Dict, Any


def simplify_generic_fields(label_fields):
    """通用字段简化：自动识别前缀归组，将 92+ 个内部字段压缩为用户友好的少量字段。

    归组规则：
    1. "前缀_数字" 模式 → 归为"前缀"组（如 课程目标1_1, 课程目标1_2 → 课程目标1）
    2. "前缀_第N列" 模式 → 归入已有组（如 课程目标2_第6列 → 课程目标2）
    3. 已有 group 类型字段 → 保留
    4. 单独字段 → 直接保留
    """
    groups = {}       # base_label → {"type", "sub_labels", "sub_fields" / "field"}
    field_order = []  # 保持字段出现顺序

    col_suffixes_re = re.compile(r'^(第\d+列|第\d+格)$')
    num_suffixes_re = re.compile(r'^(\d+)$')

    for f in label_fields:
        label = f["label"]

        if f.get("fill_mode") == "group":
            if label not in groups:
                field_order.append(label)
            groups[label] = {
                "type": "group",
                "base_label": label,
                "sub_labels": f.get("sub_labels", []),
                "sub_fields": [f],
            }
            continue

        parts = label.split("_", 1)
        if len(parts) == 2:
            base, suffix = parts
            should_group = (
                col_suffixes_re.match(suffix) or
                num_suffixes_re.match(suffix) or
                suffix in ("课程目标", "实现途径", "评价方法",
                           "实际平均分", "目标达成评价值") or
                any(k in suffix for k in ["列", "题", "列"])
            )
            if should_group:
                if base in groups:
                    if groups[base]["type"] == "single":
                        existing = groups[base]["field"]
                        groups[base] = {
                            "type": "group",
                            "base_label": base,
                            "sub_labels": [base, suffix],
                            "sub_fields": [existing, f],
                        }
                    else:
                        groups[base]["sub_labels"].append(suffix)
                        groups[base]["sub_fields"].append(f)
                else:
                    field_order.append(base)
                    groups[base] = {
                        "type": "group",
                        "base_label": base,
                        "sub_labels": [suffix],
                        "sub_fields": [f],
                    }
                continue

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
                "label": f["label"],
                "description": f.get("description", f"请填写{f['label']}"),
                "fill_mode": f.get("fill_mode", "set"),
            })
        else:
            compressed = compress_sub_labels(g["sub_labels"])
            user_fields.append({
                "label": g["base_label"],
                "description": f"请填写{g['base_label']}的相关数据",
                "fill_mode": "group",
                "sub_labels": compressed,
            })

    return user_fields


def compress_sub_labels(subs):
    """压缩子标签列表：连续的列索引用范围表示。

    ["1","2","3","4","5","第6列",...,"第26列"]
    → ["数据1~5", "数据6~26"]
    """
    if len(subs) <= 5:
        return subs

    num_re = re.compile(r'^(\d+)$')
    col_re = re.compile(r'^第(\d+)列$')

    num_items = []
    col_items = []
    other_items = []

    for i, s in enumerate(subs):
        m = num_re.match(s)
        if m:
            num_items.append((i, s, int(m.group(1))))
            continue
        m = col_re.match(s)
        if m:
            col_items.append((i, s, int(m.group(1))))
            continue
        other_items.append((i, s))

    result = []

    if num_items:
        sorted_nums = sorted(num_items, key=lambda x: x[2])
        min_n = sorted_nums[0][2]
        max_n = sorted_nums[-1][2]
        if max_n - min_n + 1 == len(sorted_nums):
            result.append(f"数据{min_n}~{max_n}（共{len(sorted_nums)}项）")
        else:
            result.append(f"数据{len(sorted_nums)}项")

    if col_items:
        sorted_cols = sorted(col_items, key=lambda x: x[2])
        min_c = sorted_cols[0][2]
        max_c = sorted_cols[-1][2]
        if max_c - min_c + 1 == len(sorted_cols):
            result.append(f"第{min_c}列~第{max_c}列（共{len(sorted_cols)}项）")
        else:
            result.append(f"列数据{len(sorted_cols)}项")

    for _, s in other_items:
        result.append(s)

    return result if result else subs


def expand_generic_data(label_fields, user_data):
    """将通用模板中归组字段的逗号分隔值展开成内部子字段名。

    例: 用户输入 {"课程目标1": "0.8,0.7,0.6,..."}
    → {"课程目标1_1": "0.8", "课程目标1_2": "0.7", ...}
    """
    expanded = dict(user_data)

    groups = {}
    for f in label_fields:
        label = f["label"]
        for sep in ("_", "·"):
            if sep in label:
                base, sub = label.split(sep, 1)
                if base not in groups:
                    groups[base] = []
                groups[base].append((label, f))
                break

    for base, subs in groups.items():
        if base in user_data and isinstance(user_data[base], str):
            values = [v.strip() for v in user_data[base].split(",")]
            for i, (sub_label, sub_f) in enumerate(subs):
                if i < len(values) and values[i]:
                    expanded[sub_label] = values[i]
            if base in expanded:
                del expanded[base]

    return expanded