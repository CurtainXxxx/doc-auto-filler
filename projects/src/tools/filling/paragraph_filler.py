"""正文段落中的下划线横线字段填充。

格式：段落中有"标签(无下划线) + 空白(有下划线)"的 run 结构，
将值填入下划线空白 run 中，保留下划线格式。

支持多标签段落拆分（underline_run_start 偏移量）。

依赖：filling.base (get_first_run_rpr)
"""

from typing import List, Dict, Any
from docx.oxml.ns import qn
from tools.filling.base import get_first_run_rpr
from tools.docx_validator import sanitize_fill_text


def fill_paragraph_fields(doc, paragraph_fields, data):
    """填充正文段落中的下划线横线字段。

    Args:
        doc: python-docx Document 对象
        paragraph_fields: 段落字段列表，每项含 label、row_idx、underline_run_start
        data: {字段名: 值} 字典

    多标签段落支持：当字段有 underline_run_start 时，从指定索引开始查找下划线 run。
    每个标签组对应一段非下划线 run → 下划线 run 对，避免同一段落多条横线串位。
    """
    for f in paragraph_fields:
        label = f["label"]
        if label not in data:
            continue
        value = str(data[label])

        p_idx = f["row_idx"]
        if p_idx >= len(doc.paragraphs):
            continue

        para = doc.paragraphs[p_idx]

        # 多标签段落：从指定索引开始查找
        run_start = f.get("underline_run_start", 0)
        runs_to_check = para.runs[run_start:]

        # 找到下划线空白 run 并填入值
        for run in runs_to_check:
            is_underline = False
            if run.underline and run.underline not in (False, 0):
                is_underline = True
            if not is_underline:
                rPr = run._element.find(qn('w:rPr'))
                if rPr is not None:
                    u_elem = rPr.find(qn('w:u'))
                    if u_elem is not None:
                        val = u_elem.get(qn('w:val'))
                        if val and val not in ('none',):
                            is_underline = True

            if is_underline and not run.text.strip('_ '):
                run.text = f" {sanitize_fill_text(value)} "
                break