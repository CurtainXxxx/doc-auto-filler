"""
底层 Word OpenXML 操作模块。

提供最基础的单元格/段落/run 级别 XML 操作，不涉及任何业务逻辑。
所有填充策略模块（colon_filler、checkbox_filler 等）均依赖此模块。

函数分类：
- 单元格去重与合并检测：get_unique_cells / is_vmerge_continue / is_vmerge_restart
- run 格式操作：clone_rpr / get_first_run_rpr / ensure_run_text
- 段落文本写入：set_paragraph_text_preserve_runs / append_paragraph_with_text
- 单元格文本写入：set_tc_text / set_cell_text
- 表格行操作：add_table_row_after
"""

import copy
from typing import Optional

from docx.oxml.ns import qn

from tools.docx_validator import sanitize_fill_text


# ── 单元格去重与合并检测 ──────────────────────────────────────

def get_unique_cells(row) -> list:
    """获取行中的独立单元格（通过 XML 元素 id 去重）。

    python-docx 对合并单元格会生成多个 cell 对象共享同一 XML 元素，
    此函数过滤掉重复引用，返回真正独立的单元格列表。

    Args:
        row: python-docx Row 对象

    Returns:
        list[Cell]: 去重后的单元格列表
    """
    seen = set()
    unique = []
    for cell in row.cells:
        cid = id(cell._element)
        if cid not in seen:
            seen.add(cid)
            unique.append(cell)
    return unique


def is_vmerge_continue(cell) -> bool:
    """判断单元格是否为纵向合并的延续格（vMerge=continue）。

    纵向合并单元格中，除第一行外的后续行都标记为 vMerge（无 val 或 val=""），
    这些单元格不需要单独填充内容。

    Args:
        cell: python-docx Cell 对象

    Returns:
        True 如果该单元格是 vMerge 延续格
    """
    tc = cell._element
    tcPr = tc.find(qn("w:tcPr"))
    if tcPr is not None:
        vm = tcPr.find(qn("w:vMerge"))
        if vm is not None:
            val = vm.get(qn("w:val"))
            if val is None or val == "":
                return True
    return False


def is_vmerge_restart(cell) -> bool:
    """判断单元格是否为纵向合并的起始格（vMerge=restart）。

    Args:
        cell: python-docx Cell 对象

    Returns:
        True 如果该单元格是 vMerge 起始格
    """
    tc = cell._element
    tcPr = tc.find(qn("w:tcPr"))
    if tcPr is not None:
        vm = tcPr.find(qn("w:vMerge"))
        if vm is not None:
            val = vm.get(qn("w:val"))
            if val == "restart":
                return True
    return False


# ── run 格式操作 ──────────────────────────────────────────────

def clone_rpr(rPr_source):
    """深拷贝 run 格式元素（w:rPr），用于在新 run 中继承字体/字号等格式。

    Args:
        rPr_source: lxml 元素（可能是 w:rPr 本身或包含 w:rPr 的父元素）

    Returns:
        lxml 元素 | None: 深拷贝的 w:rPr 元素，或 None
    """
    if rPr_source is None:
        return None
    if getattr(rPr_source, "tag", None) == qn("w:rPr"):
        return copy.deepcopy(rPr_source)
    src_rPr = rPr_source.find(qn("w:rPr"))
    if src_rPr is not None:
        return copy.deepcopy(src_rPr)
    return None


def get_first_run_rpr(paragraph):
    """获取段落中第一个 run 的格式元素（w:rPr）。

    Args:
        paragraph: lxml w:p 元素

    Returns:
        lxml 元素 | None: 第一个 run 的 w:rPr，或 None
    """
    for run in paragraph.findall(qn("w:r")):
        rPr = run.find(qn("w:rPr"))
        if rPr is not None:
            return rPr
    return None


def ensure_run_text(run, text: str, rPr_source=None):
    """向 run 中写入文本，尽量复用已有结构，不破坏段落格式。

    如果 run 没有 w:rPr，则从 rPr_source 克隆一份插入。
    如果 run 没有 w:t 元素，则创建一个。

    Args:
        run: lxml w:r 元素
        text: 要写入的文本
        rPr_source: 格式来源（lxml 元素或 None）
    """
    if run.find(qn("w:rPr")) is None:
        cloned = clone_rpr(rPr_source)
        if cloned is not None:
            run.insert(0, cloned)

    text_nodes = run.findall(qn("w:t"))
    if not text_nodes:
        t_elem = run.makeelement(qn("w:t"), {})
        run.append(t_elem)
        text_nodes = [t_elem]

    text_nodes[0].text = text
    text_nodes[0].set(qn("xml:space"), "preserve")
    for extra_t in text_nodes[1:]:
        extra_t.text = ""


# ── 段落文本写入 ──────────────────────────────────────────────

def set_paragraph_text_preserve_runs(paragraph, text: str, rPr_source=None):
    """替换段落文本，尽量保留现有 paragraph/run 结构。

    策略：找到第一个包含 w:t 的 run 作为目标，写入新文本；
    其余 run 的文本清空（保留格式元素）。

    Args:
        paragraph: lxml w:p 元素
        text: 新文本
        rPr_source: 兜底格式来源
    """
    runs = paragraph.findall(qn("w:r"))
    target_run = None
    for run in runs:
        if run.findall(qn("w:t")):
            target_run = run
            break

    if target_run is None:
        if runs:
            target_run = runs[0]
        else:
            target_run = paragraph.makeelement(qn("w:r"), {})
            paragraph.append(target_run)

    effective_rpr = get_first_run_rpr(paragraph)
    if effective_rpr is None:
        effective_rpr = rPr_source
    ensure_run_text(target_run, text, effective_rpr)

    for run in runs:
        if run is target_run:
            continue
        for t_elem in run.findall(qn("w:t")):
            t_elem.text = ""


def append_paragraph_with_text(tc, text: str, rPr_source=None, pPr_source=None):
    """在单元格末尾追加一个带文本的段落，继承段落/字体格式。

    Args:
        tc: lxml w:tc 元素（表格单元格）
        text: 段落文本
        rPr_source: 字体格式来源
        pPr_source: 段落格式来源（如对齐、缩进）

    Returns:
        lxml 元素: 新创建的 w:p 段落元素
    """
    paragraph = tc.makeelement(qn("w:p"), {})
    if pPr_source is not None:
        paragraph.append(copy.deepcopy(pPr_source))
    run = paragraph.makeelement(qn("w:r"), {})
    cloned = clone_rpr(rPr_source)
    if cloned is not None:
        run.append(cloned)
    t_elem = run.makeelement(qn("w:t"), {})
    t_elem.text = text
    t_elem.set(qn("xml:space"), "preserve")
    run.append(t_elem)
    paragraph.append(run)
    tc.append(paragraph)
    return paragraph


# ── 单元格文本写入 ────────────────────────────────────────────

def set_tc_text(tc, text: str, rPr_source=None):
    """替换 tc 元素中的文本，尽量保留原模板的段落/run 结构。

    多行文本按 \\n 拆分，每行对应一个段落。
    行数多于段落数时追加新段落，少于时清空多余段落。

    Args:
        tc: lxml w:tc 元素（表格单元格）
        text: 新文本（可含 \\n）
        rPr_source: 兜底格式来源
    """
    text = sanitize_fill_text(str(text))

    paragraphs = tc.findall(qn("w:p"))
    if not paragraphs:
        append_paragraph_with_text(tc, text, rPr_source=rPr_source)
        return

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        lines = [""]

    first_ppr = paragraphs[0].find(qn("w:pPr"))
    for idx, line in enumerate(lines):
        if idx < len(paragraphs):
            effective_rpr = get_first_run_rpr(paragraphs[idx])
            if effective_rpr is None:
                effective_rpr = rPr_source
            set_paragraph_text_preserve_runs(paragraphs[idx], line, effective_rpr)
        else:
            append_paragraph_with_text(tc, line, rPr_source=rPr_source, pPr_source=first_ppr)

    for paragraph in paragraphs[len(lines):]:
        effective_rpr = get_first_run_rpr(paragraph)
        if effective_rpr is None:
            effective_rpr = rPr_source
        set_paragraph_text_preserve_runs(paragraph, "", effective_rpr)


def set_cell_text(cell, text: str, rPr_source=None):
    """设置单元格文本（清空后写入），保留格式。

    Args:
        cell: python-docx Cell 对象
        text: 新文本
        rPr_source: 兜底格式来源
    """
    set_tc_text(cell._element, text, rPr_source=rPr_source)


# ── 表格行操作 ────────────────────────────────────────────────

def add_table_row_after(table, after_row_idx):
    """在指定行后复制一行（用于行组填充时扩展数据行）。

    Args:
        table: python-docx Table 对象
        after_row_idx: 源行索引

    Returns:
        lxml 元素: 新创建的 w:tr 行元素
    """
    src_row = table.rows[after_row_idx]
    new_tr = copy.deepcopy(src_row._tr)
    src_row._tr.addnext(new_tr)
    return new_tr
