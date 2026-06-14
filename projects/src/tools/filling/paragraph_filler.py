"""正文段落中的下划线横线字段填充。

格式：段落中有"标签(无下划线) + 空白(有下划线)"的 run 结构，
将值填入下划线空白 run 中，保留下划线格式。

支持多标签段落拆分（underline_run_start 偏移量）。

支持文字下划线模式：段落中使用 ____ 字符而非 Word 下划线格式时，
按 run 内 ____ 出现顺序逐个替换。

依赖：filling.base (get_first_run_rpr)
"""

import re
from typing import List, Any
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

    文字下划线模式：字段将从 run 文本中按 ____ 出现顺序逐个替换。
    """
    for f in paragraph_fields:
        label = f["label"]
        fid = f.get("field_id", "")
        if label in data:
            value = str(data[label])
        elif fid in data:
            value = str(data[fid])
        else:
            continue

        p_idx = f["row_idx"]
        if p_idx >= len(doc.paragraphs):
            continue

        para = doc.paragraphs[p_idx]

        # 多标签段落：从指定索引开始查找
        run_start = f.get("underline_run_start", 0)
        runs_to_check = para.runs[run_start:]

        # 找到下划线空白 run 并填入值
        found = False
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
                found = True
                break

        if found:
            continue

        # ── 文字下划线模式 ____+（ASCII 下划线，无 Word 格式） ──
        # 处理逻辑：因前一个字段替换后会改变 run.text，
        # 每次只替换当前 run.text 中第一个 ____+ 即可。
        # 字段处理顺序与 ____ 在文本中的出现顺序一致，
        # 因此逐个替换第一个匹配就是正确的。
        for run in runs_to_check:
            if '____' not in run.text:
                continue
            run.text = re.sub(
                r'_{4,}',
                f' {sanitize_fill_text(value)} ',
                run.text,
                count=1  # 只替换第一个匹配
            )
            found = True
            break