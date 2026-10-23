"""Shared Word filling pipeline, with separate built-in/custom data expansion."""
import io

from docx import Document

from tools.template_analyzer import analyze_template
from tools.filling.base import get_unique_cells
from tools.filling.builtin_expander import expand_report_data
from tools.filling.checkbox_filler import detect_checkbox_row, fill_checkbox_rows_in_table
from tools.filling.colon_filler import fill_label_fields
from tools.filling.data_expander import expand_generic_data
from tools.filling.field_values import field_value
from tools.filling.paragraph_filler import fill_paragraph_fields
from tools.filling.postprocess import fix_merged_cells
from tools.filling.row_group_filler import fill_multi_col_field, fill_simple_row_groups


def _partition_fields(doc, fields):
    """Reserve checkbox targets, then separate table and paragraph writers."""
    checkbox_cells = {
        (table_index, row_index, column)
        for table_index, table in enumerate(doc.tables)
        for row_index, row in enumerate(table.rows)
        for group in detect_checkbox_row(get_unique_cells(row))
        for column in group["option_blanks"].values()
    }
    table_fields, paragraph_fields = [], []
    for field in fields:
        if field.get("pattern") == "paragraph_underline":
            paragraph_fields.append(field)
        elif field.get("pattern") != "checkbox" and not any(
            (field["table_idx"], row, field["col_idx"]) in checkbox_cells
            for row in field["row_indices"]
        ):
            table_fields.append(field)
    return table_fields, paragraph_fields


def _fill_document(template_path, analysis, expanded_data, *, include_multi_col):
    """Run each writer, repair merged XML, and serialize in memory."""
    doc = Document(template_path)
    table_fields, paragraph_fields = _partition_fields(doc, analysis["label_fields"])
    fill_label_fields(doc, table_fields, expanded_data)
    fill_paragraph_fields(doc, paragraph_fields, expanded_data)
    fill_checkbox_rows_in_table(doc, analysis, expanded_data)
    row_values = fill_simple_row_groups(doc, analysis["row_groups"], expanded_data)

    if include_multi_col:
        for field in analysis["label_fields"]:
            if field.get("pattern") == "multi_col":
                value = field_value(field, expanded_data)
                if value is not None:
                    fill_multi_col_field(doc, field, value)

    fix_merged_cells(doc)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue(), analysis, expanded_data, row_values


def build_report_docx(template_path: str, user_data: dict, analysis_result: dict = None):
    """Generate a built-in report; keep attendance/score expansion behavior."""
    analysis = analysis_result or analyze_template(template_path)
    expanded = expand_report_data(template_path, user_data, analysis_result=analysis)
    return _fill_document(template_path, analysis, expanded, include_multi_col=False)


def fill_custom_template(template_path: str, user_data: dict, analysis_result: dict = None):
    """Fill a custom template, preserving the existing four-item return tuple."""
    analysis = analysis_result or analyze_template(template_path)
    expanded = expand_generic_data(analysis["label_fields"], user_data)
    return _fill_document(template_path, analysis, expanded, include_multi_col=True)
