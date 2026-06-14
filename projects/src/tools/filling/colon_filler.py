"""冒号模式字段填充 — 在标签后追加值、替换签名/日期占位符。

典型场景：
    单元格文本为 ``负责人签名：______年____月____日______``，
    自动定位标签 run，在其后追加签名值并替换日期占位符。

依赖：
    - ``base``：底层 XML 操作（单元格文本替换、run 格式获取）
    - ``token_style``：词元级样式继承引擎（v2 填充）
"""

import copy
import re as _re
from docx.oxml.ns import qn

from tools.filling.base import get_unique_cells
from tools.filling.base import set_tc_text, set_paragraph_text_preserve_runs, get_first_run_rpr
from tools.filling.token_style import set_tc_text_v2


_EMBEDDED_DATE_PAT = _re.compile(r"年\s*月\s*日(?:\s*(?:上午|下午|上|下))?")
_DATE_VALUE_PAT = _re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def find_label_rPr_in_row(tr):
    """从一行中找到第一个有 rPr 格式的标签格，返回其 rPr 元素（深拷贝）。

    用于空白格填充时继承同行标签格的字体/字号等格式。
    """
    tcs = tr.findall(qn("w:tc"))
    for tc in tcs:
        for p in tc.findall(qn("w:p")):
            for r in p.findall(qn("w:r")):
                rPr = r.find(qn("w:rPr"))
                if rPr is not None:
                    return rPr
    return None


def replace_signature_and_date_in_tc(tc, label: str, existing_value: str, value: str):
    """处理 ``负责人签名：年月日`` 格式——在标签后追加签名，可选替换日期占位符。

    Args:
        tc: 单元格 XML 元素
        label: 标签文字（如 "负责人签名"）
        existing_value: 单元格现有值
        value: 待填入值

    场景 1：值只有签名（如 ``李明``）→ 在签名后追加名字，日期占位符保持不变
    场景 2：值包含签名+日期（如 ``李明 2024年12月15日``）→ 同时替换日期占位符
    """
    date_match = _DATE_VALUE_PAT.search(value)
    if date_match:
        replace_embedded_date_in_tc(tc, label, existing_value, value)
    else:
        append_value_to_tc_after_label(tc, label, value)


def replace_embedded_date_in_tc(tc, label: str, existing_value: str, value: str):
    """替换嵌入式日期占位符为实际值。

    示例：
        单元格文本 ``考试时间  年  月  日  上午  下午``
        label="考试时间", value="2024年12月15日 上午"
        → 结果: ``考试时间  2024年12月15日 上午``
    """
    for paragraph in tc.findall(qn("w:p")):
        text_nodes = paragraph.findall(".//" + qn("w:t"))
        para_text = "".join((t.text or "") for t in text_nodes)

        label_pos = para_text.find(label)
        if label_pos >= 0:
            after_label = para_text[label_pos + len(label) :]
            replaced = _EMBEDDED_DATE_PAT.sub(value, after_label, count=1)
            new_para_text = para_text[: label_pos + len(label)] + replaced

            effective_rpr = get_first_run_rpr(paragraph)
            set_paragraph_text_preserve_runs(paragraph, new_para_text, effective_rpr)
            return True

    # 回退：直接设置整个单元格文本
    rPr_source = find_label_rPr_in_row(tc.getparent())
    set_tc_text(tc, f"{label} {value}", rPr_source=rPr_source)
    return True


def append_value_to_tc_after_label(tc, label: str, value: str):
    """在标签 run 后追加一个新的 value run，保留标签 run 原有格式。

    改进点：不再把标签和值合并到同一个 run，而是在标签 run 之后
    插入一个新的 run 来放置值，这样标签和值可以保持各自的格式。
    """
    for paragraph in tc.findall(qn("w:p")):
        text_nodes = paragraph.findall(".//" + qn("w:t"))
        para_text = "".join((t.text or "") for t in text_nodes)
        if label in para_text:
            runs = paragraph.findall(qn("w:r"))
            for run in runs:
                run_text = "".join((t.text or "") for t in run.findall(qn("w:t")))
                if label in run_text:
                    new_run = copy.deepcopy(run)
                    for t_elem in new_run.findall(qn("w:t")):
                        t_elem.text = value
                        t_elem.set(qn("xml:space"), "preserve")
                    run.addnext(new_run)
                    return True

            # 没有找到包含完整 label 的 run（跨 run 标签），回退到简单方式
            effective_rpr = get_first_run_rpr(paragraph)
            replaced = para_text.replace(label, f"{label}{value}", 1)
            set_paragraph_text_preserve_runs(paragraph, replaced, effective_rpr)
            return True

    paragraphs = tc.findall(qn("w:p"))
    if paragraphs:
        paragraph = paragraphs[0]
        para_text = "".join((t.text or "") for t in paragraph.findall(".//" + qn("w:t")))
        effective_rpr = get_first_run_rpr(paragraph)
        set_paragraph_text_preserve_runs(paragraph, para_text + str(value), effective_rpr)
        return True
    return False


def fill_label_fields(doc, label_fields, data):
    """填充标签字段，支持多种填充模式。保留原有格式。

    填充模式：
        - ``set``：标签格+空白格模式，直接设置空白格内容
        - ``replace``：占位符替换模式，支持签名+日期联合替换
        - ``append``：标准冒号模式，在标签后追加值

    字段跳转：
        - ``pattern == "paragraph_underline"`` 的字段由
          ``paragraph_filler.fill_paragraph_fields`` 单独处理，此处跳过。

    Args:
        doc: python-docx Document 对象
        label_fields: 标签字段列表，每项含 label/col_idx/row_indices/fill_mode/pattern 等
        data: 字段名 → 字段值的字典
    """
    for f in label_fields:
        label = f["label"]
        fid = f.get("field_id", "")
        # 段落级字段由 paragraph_filler 单独处理
        if f.get("pattern") == "paragraph_underline":
            continue
        # 优先用 label 查 data，回退到 field_id
        if label in data:
            value = data[label]
        elif fid in data:
            value = data[fid]
        else:
            continue
        fill_mode = f.get("fill_mode", "append")
        pattern = f.get("pattern", "colon")
        existing_value = f.get("existing_value", "")

        for ri in f["row_indices"]:
            t_idx = f["table_idx"]
            table = doc.tables[t_idx]

            if fill_mode == "set":
                col_idx = f["col_idx"]
                unique_cells = get_unique_cells(table.rows[ri])
                if col_idx < len(unique_cells):
                    tc = unique_cells[col_idx]._element
                    tr = table.rows[ri]._tr
                    rPr_source = find_label_rPr_in_row(tr)
                    set_tc_text_v2(tc, str(value), mode="set", rPr_source=rPr_source)

            elif fill_mode == "replace":
                col_idx = f["col_idx"]
                unique_cells = get_unique_cells(table.rows[ri])
                if col_idx < len(unique_cells):
                    tc = unique_cells[col_idx]._element
                    raw_label = f.get("raw_label", label)
                    existing_value = f.get("existing_value", "")
                    tr = table.rows[ri]._tr

                    if pattern == "colon" and existing_value and _EMBEDDED_DATE_PAT.search(existing_value):
                        replace_signature_and_date_in_tc(tc, raw_label, existing_value, str(value))
                    elif pattern == "colon" and not existing_value:
                        append_value_to_tc_after_label(tc, raw_label, str(value))
                    else:
                        rPr_source = find_label_rPr_in_row(tr)
                        set_tc_text_v2(tc, str(value), mode="replace", rPr_source=rPr_source)

            elif fill_mode == "append":
                col_idx = f["col_idx"]
                unique_cells = get_unique_cells(table.rows[ri])
                if col_idx < len(unique_cells):
                    tc = unique_cells[col_idx]._element
                    if existing_value and _EMBEDDED_DATE_PAT.search(existing_value):
                        replace_embedded_date_in_tc(tc, label, existing_value, str(value))
                    else:
                        append_value_to_tc_after_label(tc, label, str(value))