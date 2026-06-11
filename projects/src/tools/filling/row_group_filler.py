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
    for g in groups:
        gid = g["group_id"]
        if gid not in data:
            continue

        row_data_list = data[gid]
        if not isinstance(row_data_list, list):
            continue

        t_idx = g["table_idx"]
        table = doc.tables[t_idx]
        start_row = g["start_row"]
        template_row_count = g["template_row_count"]

        # 从 group_id 提取 group 序号 (如 "T0_G1" → 1)
        g_num = int(gid.split("_G")[1]) if "_G" in gid else 0

        # 填充模板行
        for row_offset, row_data in enumerate(row_data_list):
            if row_offset >= template_row_count:
                break
            actual_row = start_row + row_offset

            tr = table.rows[actual_row]._tr
            tcs = tr.findall(qn("w:tc"))

            data_idx = 0
            for ti, tc in enumerate(tcs):
                if data_idx >= len(row_data):
                    break

                # vMerge continue 格：消耗数据但不填值
                tcPr = tc.find(qn("w:tcPr"))
                if tcPr is not None:
                    vm = tcPr.find(qn("w:vMerge"))
                    if vm is not None:
                        val = vm.get(qn("w:val"))
                        if val is None or val == "":
                            fid = f"T{t_idx}_G{g_num}_R{row_offset}_C{data_idx}"
                            rg_field_values[fid] = str(row_data[data_idx])
                            data_idx += 1
                            continue

                # 提取格内文本
                all_text = []
                for p in tc.findall(qn("w:p")):
                    for r in p.findall(qn("w:r")):
                        for t in r.findall(qn("w:t")):
                            if t.text:
                                all_text.append(t.text)
                text = "".join(all_text).strip()

                # 空白格或占位符格：填入数据
                if not text or text in ("%", "…", "……"):
                    rPr_source = find_label_rPr_in_row(tr)
                    set_tc_text_v2(tc, str(row_data[data_idx]), mode="set", rPr_source=rPr_source)
                    fid = f"T{t_idx}_G{g_num}_R{row_offset}_C{data_idx}"
                    rg_field_values[fid] = str(row_data[data_idx])
                    data_idx += 1

        # 数据行数 > 模板行数：复制最后一行并填充
        if len(row_data_list) > template_row_count:
            for extra_idx in range(template_row_count, len(row_data_list)):
                last_row = table.rows[start_row + template_row_count - 1]
                new_tr = copy.deepcopy(last_row._tr)
                last_row._tr.addnext(new_tr)

                tcs = new_tr.findall(qn("w:tc"))
                row_data = row_data_list[extra_idx]
                data_idx = 0
                for ti, tc in enumerate(tcs):
                    if data_idx >= len(row_data):
                        break
                    tcPr = tc.find(qn("w:tcPr"))
                    if tcPr is not None:
                        vm = tcPr.find(qn("w:vMerge"))
                        if vm is not None:
                            val = vm.get(qn("w:val"))
                            if val is None or val == "":
                                fid = f"T{t_idx}_G{g_num}_R{extra_idx}_C{data_idx}"
                                rg_field_values[fid] = str(row_data[data_idx])
                                data_idx += 1
                                continue
                    all_text = []
                    for p in tc.findall(qn("w:p")):
                        for r in p.findall(qn("w:r")):
                            for t in r.findall(qn("w:t")):
                                if t.text:
                                    all_text.append(t.text)
                    text = "".join(all_text).strip()
                    if not text or text in ("%", "…", "……"):
                        set_tc_text_v2(tc, str(row_data[data_idx]), mode="set")
                        fid = f"T{t_idx}_G{g_num}_R{extra_idx}_C{data_idx}"
                        rg_field_values[fid] = str(row_data[data_idx])
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