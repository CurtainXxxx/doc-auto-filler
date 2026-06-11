"""
词元级样式继承引擎 — 智能定位空白/占位符 run，保留原模板格式。

核心策略（按优先级）：
1. 找到空白 run → 直接改写 text，100% 保留该 run 的字体/下划线/字号
2. 找到占位符 run（"yyy"、"xxx"、"请填写"）→ 替换内容
3. 退回段落级整体替换（``set_paragraph_text_preserve_runs``）

三种填充模式：
- **set**：智能定位空白/占位符 run，零格式损失
- **append**：在标签后追加文本，保留标签 run 格式
- **replace**：整体替换，保留段落结构

依赖：
    base.py（get_first_run_rpr, set_paragraph_text_preserve_runs, append_paragraph_with_text）
"""

import re as _re
from typing import Callable, Optional

from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from tools.filling.base import (
    get_first_run_rpr,
    set_paragraph_text_preserve_runs,
    append_paragraph_with_text,
)
from tools.docx_validator import sanitize_fill_text


# ══════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════

_PLACEHOLDER_PATTERNS: tuple[str, ...] = (
    "yyyy", "xxx", "XX", "请填写", "请输入", "（请", "(请",
)
"""占位符文本模式，命中后优先替换内容而非改写空白 run。"""


# ══════════════════════════════════════════════════════════════
# 内部辅助：run 查找
# ══════════════════════════════════════════════════════════════


def get_run_text(run) -> str:
    """提取 run 元素中所有 ``<w:t>`` 的拼接文本。

    Args:
        run: ``<w:r>`` lxml 元素

    Returns:
        拼接后的纯文本字符串
    """
    t_elems = run.findall(qn("w:t"))
    return "".join(t.text or "" for t in t_elems)


def is_blank_run(run) -> bool:
    """判断 run 是否为空白且包含 ``<w:t>`` 元素（可安全覆写）。

    Args:
        run: ``<w:r>`` lxml 元素

    Returns:
        True 如果 run 文本为空/纯空格且含 ``<w:t>``
    """
    if not run.findall(qn("w:t")):
        return False
    return get_run_text(run).strip() == ""


def is_placeholder_run(run) -> bool:
    """判断 run 是否包含占位符文本。

    Args:
        run: ``<w:r>`` lxml 元素

    Returns:
        True 如果 run 文本包含任意占位符模式
    """
    t_text = get_run_text(run)
    return any(pat in t_text for pat in _PLACEHOLDER_PATTERNS)


def find_fill_target_run(paragraph) -> Optional[object]:
    """在段落中找到最适合填入文本的 run。

    优先级：
        1. 文本为空白/纯空格的 run → 保留其下划线、字体等 rPr
        2. 包含占位符文本的 run → 替换占位符内容
        3. 第一个包含 ``<w:t>`` 的 run → 兜底写入
        4. 任意 run

    Args:
        paragraph: ``<w:p>`` lxml 元素

    Returns:
        目标 run 元素，段落无 run 时返回 ``None``
    """
    runs = paragraph.findall(qn("w:r"))
    if not runs:
        return None

    # 优先 1：空白 run（保留下划线等格式）
    for run in runs:
        if is_blank_run(run):
            return run

    # 优先 2：占位符 run
    for run in runs:
        if is_placeholder_run(run):
            return run

    # 优先 3：第一个有 <w:t> 的 run
    for run in runs:
        if run.findall(qn("w:t")):
            return run

    # 优先 4：任意 run
    return runs[0]


# ══════════════════════════════════════════════════════════════
# 内部辅助：run 文本写入
# ══════════════════════════════════════════════════════════════


def write_text_to_run(run, text: str) -> None:
    """向目标 run 写入文本，保留该 run 的 rPr 格式。

    - 设置第一个 ``<w:t>`` 的文本
    - 移除多余的 ``<w:t>`` 子元素
    - 若 run 无 ``<w:t>`` 则新建一个

    Args:
        run: 目标 ``<w:r>`` lxml 元素
        text: 要写入的文本
    """
    t_elems = run.findall(qn("w:t"))
    if t_elems:
        t_elems[0].text = text
        t_elems[0].set(qn("xml:space"), "preserve")
        for extra_t in t_elems[1:]:
            run.remove(extra_t)
    else:
        new_t = OxmlElement("w:t")
        new_t.text = text
        new_t.set(qn("xml:space"), "preserve")
        run.append(new_t)


def clear_other_runs_text(paragraph, keep_run) -> None:
    """清空段落中除 keep_run 之外所有 run 的文本。

    保留元素结构不删，避免格式丢失。

    Args:
        paragraph: ``<w:p>`` lxml 元素
        keep_run: 保留文本的 run 元素
    """
    for run in paragraph.findall(qn("w:r")):
        if run is keep_run:
            continue
        for t in run.findall(qn("w:t")):
            t.text = ""


# ══════════════════════════════════════════════════════════════
# 内部辅助：rPr 获取
# ══════════════════════════════════════════════════════════════


def resolve_effective_rpr(paragraph, fallback=None) -> Optional[object]:
    """获取段落的有效 rPr，优先取段落内第一个 run，否则用兜底值。

    Args:
        paragraph: ``<w:p>`` lxml 元素
        fallback: 兜底 rPr 来源（lxml 元素或 None）

    Returns:
        rPr lxml 元素或 None
    """
    rpr = get_first_run_rpr(paragraph)
    return rpr if rpr is not None else fallback


# ══════════════════════════════════════════════════════════════
# 模式处理器（策略）
# ══════════════════════════════════════════════════════════════


def handle_set_mode(paragraph, line: str, rPr_source=None) -> None:
    """set 模式：智能定位空白/占位符 run，直接改 text 保格式。

    - 找到目标 run → ``write_text_to_run`` + 清空其他 run
    - 找不到 → 退回 ``set_paragraph_text_preserve_runs`` 兜底

    Args:
        paragraph: ``<w:p>`` lxml 元素
        line: 要写入的单行文本
        rPr_source: 兜底格式来源
    """
    target_run = find_fill_target_run(paragraph)
    if target_run is not None:
        write_text_to_run(target_run, line)
        clear_other_runs_text(paragraph, target_run)
        return

    # 无可用 run，退回兜底写入
    set_paragraph_text_preserve_runs(paragraph, line, rPr_source)


def handle_append_mode(paragraph, line: str, rPr_source=None) -> None:
    """append 模式：在现有文本后追加，保留标签 run 的格式。

    Args:
        paragraph: ``<w:p>`` lxml 元素
        line: 要写入的单行文本
        rPr_source: 兜底格式来源
    """
    effective_rpr = resolve_effective_rpr(paragraph, fallback=rPr_source)
    set_paragraph_text_preserve_runs(paragraph, line, effective_rpr)


def handle_replace_mode(paragraph, line: str, rPr_source=None) -> None:
    """replace 模式：整体替换文本，保留 pPr + 第一个 run 的 rPr。

    Args:
        paragraph: ``<w:p>`` lxml 元素
        line: 要写入的单行文本
        rPr_source: 兜底格式来源
    """
    effective_rpr = resolve_effective_rpr(paragraph, fallback=rPr_source)
    set_paragraph_text_preserve_runs(paragraph, line, effective_rpr)


# ── 策略分派表 ──
_MODE_HANDLERS: dict[str, Callable[[object, str, object | None], None]] = {
    "set": handle_set_mode,
    "append": handle_append_mode,
    "replace": handle_replace_mode,
}


# ══════════════════════════════════════════════════════════════
# 单行填充
# ══════════════════════════════════════════════════════════════


def fill_paragraph_line(paragraph, line: str, mode: str, rPr_source=None) -> None:
    """将单行文本填入段落，按 mode 分派到对应处理器。

    Args:
        paragraph: ``<w:p>`` lxml 元素
        line: 要写入的单行文本
        mode: ``"set"`` | ``"append"`` | ``"replace"``
        rPr_source: 兜底格式来源
    """
    handler = _MODE_HANDLERS.get(mode, handle_replace_mode)
    handler(paragraph, line, rPr_source)


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════


def set_tc_text_v2(tc, text: str, mode: str = "set", rPr_source=None):
    """词元级样式继承版本的 set_tc_text。

    与 set_tc_text 的区别：
        - **set** 模式：找到空白/占位符 run，直接修改其 ``.text``，100% 保留格式
        - **append** 模式：在标签后追加，保留标签 run 的格式
        - **replace** 模式：整体替换文本，保留段落和第一个 run 的格式

    Args:
        tc: 表格单元格 ``<w:tc>`` XML 元素
        text: 要填入的文本
        mode: ``"set"`` | ``"append"`` | ``"replace"``
        rPr_source: 兜底格式来源（lxml 元素或 None）
    """
    text = sanitize_fill_text(str(text))

    # ── 卫语句：无段落时直接追加 ──
    paragraphs = tc.findall(qn("w:p"))
    if not paragraphs:
        append_paragraph_with_text(tc, text, rPr_source=rPr_source)
        return

    # ── 预处理：拆行 ──
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n") or [""]

    # ── 填充已有段落 ──
    first_ppr = paragraphs[0].find(qn("w:pPr"))

    for idx, line in enumerate(lines):
        if idx < len(paragraphs):
            fill_paragraph_line(paragraphs[idx], line, mode, rPr_source)
        else:
            append_paragraph_with_text(tc, line, rPr_source=rPr_source, pPr_source=first_ppr)

    # ── 清空多余段落 ──
    for paragraph in paragraphs[len(lines):]:
        effective_rpr = resolve_effective_rpr(paragraph, fallback=rPr_source)
        set_paragraph_text_preserve_runs(paragraph, "", effective_rpr)


def set_cell_text_v2(cell, text: str, mode: str = "set", rPr_source=None):
    """词元级样式继承版本的 set_cell_text。

    Args:
        cell: python-docx Cell 对象
        text: 要填入的文本
        mode: ``"set"`` | ``"append"`` | ``"replace"``
        rPr_source: 兜底格式来源
    """
    set_tc_text_v2(cell._element, text, mode=mode, rPr_source=rPr_source)