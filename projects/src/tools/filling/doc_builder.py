"""文档生成编排层：组合各填充模块，产出最终 docx。

提供两个主入口：
1. build_report_docx — 内置模板专用，使用 expand_report_data 扩展数据
2. fill_custom_template — 通用模板专用，使用 expand_generic_data 扩展数据

两者在填充管线（标签字段 → 勾选框 → 行组 → 修复）上一致，
区别仅在数据扩展策略和 multi_col 字段处理。
"""

import tempfile
import os
from typing import Dict, Any, Optional, Tuple
from docx import Document
from tools.template_analyzer import analyze_template
from tools.filling.base import get_unique_cells
from tools.filling.colon_filler import fill_label_fields
from tools.filling.checkbox_filler import fill_checkbox_rows_in_table, detect_checkbox_row
from tools.filling.paragraph_filler import fill_paragraph_fields
from tools.filling.row_group_filler import fill_simple_row_groups, fill_multi_col_field
from tools.filling.data_expander import expand_generic_data
from tools.filling.builtin_expander import expand_report_data
from tools.filling.postprocess import fix_merged_cells


def build_report_docx(template_path: str, user_data: dict, analysis_result: dict = None):
    """内置模板：分析 → 扩展 → 填充 → 修复 → 保存。

    处理流程：
    1. 分析模板结构（或使用已有 analysis_result）
    2. 扩展用户数据（考勤映射、分数段自动计算等）
    3. 过滤勾选框相关字段（由 fill_checkbox_rows_in_table 独立处理）
    4. 分离段落级下划线字段
    5. 填充标签字段 → 段落下划线字段 → 勾选框 → 行组
    6. 修复合并单元格展开
    7. 保存到临时文件并返回字节内容

    Returns:
        tuple: (docx_bytes, analysis, expanded_data, rg_field_values)
    """
    analysis = analysis_result or analyze_template(template_path)
    doc = Document(template_path)

    expanded_data = expand_report_data(template_path, user_data, analysis_result=analysis_result)

    # 识别勾选框行，排除冲突的标签字段
    checkbox_blank_cells = set()
    for t_idx, table in enumerate(doc.tables):
        for r_idx, row in enumerate(table.rows):
            unique = get_unique_cells(row)
            groups = detect_checkbox_row(unique)
            for g in groups:
                for ci in g["option_blanks"].values():
                    checkbox_blank_cells.add((t_idx, r_idx, ci))

    filtered_fields = []
    paragraph_fields = []
    for f in analysis["label_fields"]:
        if f.get("pattern") == "checkbox":
            continue
        if f.get("pattern") == "paragraph_underline":
            paragraph_fields.append(f)
            continue
        t_idx = f["table_idx"]
        skip = False
        for r_idx in f["row_indices"]:
            if (t_idx, r_idx, f["col_idx"]) in checkbox_blank_cells:
                skip = True
                break
        if not skip:
            filtered_fields.append(f)

    fill_label_fields(doc, filtered_fields, expanded_data)
    fill_paragraph_fields(doc, paragraph_fields, expanded_data)
    fill_checkbox_rows_in_table(doc, analysis, expanded_data)
    rg_field_values = fill_simple_row_groups(doc, analysis["row_groups"], expanded_data)

    fix_merged_cells(doc)

    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    try:
        doc.save(tmp.name)
        tmp.seek(0)
        content = tmp.read()
    finally:
        tmp.close()
        os.unlink(tmp.name)

    return content, analysis, expanded_data, rg_field_values


def fill_custom_template(template_path: str, user_data: dict, analysis_result: dict = None):
    """通用模板纯填充：只向空白格/待填格写入文本，绝不改变文档格式。

    与 build_report_docx 的区别：
    - 不调用 expand_report_data（内置模板专用扩展逻辑）
    - 使用 expand_generic_data 展开通用归组字段
    - 额外处理 multi_col 模式字段
    - 完整保留原有字体、字号、加粗、对齐等格式

    Returns:
        tuple: (docx_bytes, analysis, expanded_data, rg_field_values)
    """
    analysis = analysis_result or analyze_template(template_path)
    doc = Document(template_path)

    expanded_data = expand_generic_data(analysis["label_fields"], user_data)

    checkbox_blank_cells = set()
    for t_idx, table in enumerate(doc.tables):
        for r_idx, row in enumerate(table.rows):
            unique = get_unique_cells(row)
            groups = detect_checkbox_row(unique)
            for g in groups:
                for ci in g["option_blanks"].values():
                    checkbox_blank_cells.add((t_idx, r_idx, ci))

    filtered_fields = []
    paragraph_fields = []
    for f in analysis["label_fields"]:
        if f.get("pattern") == "paragraph_underline":
            paragraph_fields.append(f)
            continue
        t_idx = f["table_idx"]
        skip = False
        for r_idx in f["row_indices"]:
            if (t_idx, r_idx, f["col_idx"]) in checkbox_blank_cells:
                skip = True
                break
        if not skip:
            filtered_fields.append(f)

    fill_label_fields(doc, filtered_fields, expanded_data)
    fill_paragraph_fields(doc, paragraph_fields, expanded_data)
    fill_checkbox_rows_in_table(doc, analysis, expanded_data)
    rg_field_values = fill_simple_row_groups(doc, analysis["row_groups"], expanded_data)

    # 填充 multi_col 字段（如评价报告中的课程目标表）
    for f in analysis["label_fields"]:
        if f.get("pattern") == "multi_col" and f["label"] in expanded_data:
            fill_multi_col_field(doc, f, expanded_data[f["label"]])

    fix_merged_cells(doc)

    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    try:
        doc.save(tmp.name)
        tmp.seek(0)
        content = tmp.read()
    finally:
        tmp.close()
        os.unlink(tmp.name)

    return content, analysis, expanded_data, rg_field_values