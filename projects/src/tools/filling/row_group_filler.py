"""行组（重复数据行）和 multi_col 模式字段填充。

行组填充：检测模板行组结构，填充重复行数据，支持行复制。
multi_col 填充：一行内多个待填列，按顺序填入值。

依赖：filling.base (set_tc_text), filling.token_style (set_tc_text_v2),
      filling.colon_filler (find_label_rPr_in_row)
"""

import copy
from typing import List, Dict, Any, Optional
from docx.oxml.ns import qn
from tools.filling.base import set_tc_text
from tools.filling.token_style import set_tc_text_v2
from tools.filling.colon_filler import find_label_rPr_in_row


def fill_simple_row_groups(doc, groups, data):
    """填充简单行组（重复行数据）。

    Args:
        doc: python-docx Document 对象
        groups: 分析结果中的 group 列表，每项含 group_id、table_idx、start_row、template_row_count
        data: {group_id: [[row_data...], ...]} 字典

    Returns:
        dict: field_id → value 映射，格式为 T{t}_G{g}_R{r}_C{c}

    填充策略：
    - 模板行直接填入数据
    - 数据行数超过模板行数时，复制最后一行并填充
    - vMerge continue 格：消耗数据但不填值（记录映射）
    - 空白/占位符格：用 set_tc_text_v2 填入，继承行内 rPr 格式
    """
    rg_field_values = {}
    # Bottom-up processing prevents inserted rows from shifting later groups.
    for g in sorted(groups, key=lambda item: (item["table_idx"], item["start_row"]), reverse=True):
        gid = g["group_id"]
        rows = data.get(gid)
        if not isinstance(rows, list) or not rows:
            continue
        if not all(isinstance(row, (list, tuple)) for row in rows):
            raise ValueError(f"行组 {gid} 必须是二维数组")
        table = doc.tables[g["table_idx"]]
        start = g["start_row"]
        count = g["template_row_count"]
        if count < 1:
            continue
        # Clone the unfilled row, rather than duplicating values written earlier.
        pristine = copy.deepcopy(table.rows[start + count - 1]._tr)
        anchor = table.rows[start + count - 1]._tr
        g_num = int(gid.split("_G")[1]) if "_G" in gid else 0
        for offset, row_data in enumerate(rows):
            if offset < count:
                tr = table.rows[start + offset]._tr
            else:
                tr = copy.deepcopy(pristine)
                anchor.addnext(tr)
                anchor = tr
            data_idx = 0
            for tc in tr.findall(qn("w:tc")):
                if data_idx >= len(row_data):
                    break
                props = tc.find(qn("w:tcPr"))
                merge = props.find(qn("w:vMerge")) if props is not None else None
                continued = merge is not None and merge.get(qn("w:val")) in (None, "", "continue")
                text = "".join(t.text or "" for t in tc.iter(qn("w:t"))).strip()
                if continued or not text or text in ("%", "…", "……"):
                    value = "" if row_data[data_idx] is None else str(row_data[data_idx])
                    if not continued:
                        set_tc_text_v2(tc, value, mode="set", rPr_source=find_label_rPr_in_row(tr))
                    fid = f"T{g['table_idx']}_G{g_num}_R{offset}_C{data_idx}"
                    rg_field_values[fid] = value
                    data_idx += 1
    return rg_field_values


def fill_multi_col_field(doc, field, value):
    """填充 multi_col 模式字段（一行内多个待填列）。

    将逗号分隔的值列表填入指定的可填充列位置。

    Args:
        doc: python-docx Document 对象
        field: 字段描述，含 table_idx、fillable_cols、row_indices
        value: 逗号分隔字符串、数值或列表
    """
    t_idx = field["table_idx"]
    table = doc.tables[t_idx]

    if isinstance(value, str):
        values = [v.strip() for v in value.replace("，", ",").split(",")]
    elif isinstance(value, (int, float)):
        values = [str(value)]
    else:
        values = list(value)

    fillable_cols = field.get("fillable_cols", [])

    for i, col_idx in enumerate(fillable_cols):
        if i >= len(values):
            break
        for ri in field["row_indices"]:
            tr = table.rows[ri]._tr
            tcs = tr.findall(qn("w:tc"))
            if col_idx < len(tcs):
                rPr_source = find_label_rPr_in_row(tr)
                set_tc_text(tcs[col_idx], str(values[i]), rPr_source=rPr_source)