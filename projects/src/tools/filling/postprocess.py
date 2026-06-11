"""
文档生成后处理 — 修复 python-docx 保存时的常见问题。

当前包含：
- ``fix_merged_cells``：清理合并单元格展开导致的 XML 重复

已知问题：
    **vMerge 递归**：python-docx 的 ``row.cells`` 会触发 ``tc._tc_above`` 链向上查找，
    当行结构不一致时抛出 ``ValueError: no tc element at grid_offset=0``。
    所有代码必须避免使用 ``row.cells``，改为 XML 级别 ``tr.findall(qn("w:tc"))`` 直接遍历。
"""

from docx.oxml.ns import qn


def fix_merged_cells(doc):
    """修复 python-docx 保存时合并单元格展开的问题。

    python-docx 在读取 docx 时，合并的单元格会生成多个 cell 对象共享同一个 XML 元素。
    保存时所有 cell 都会被写入，导致内容重复。此函数通过 XML 元素 id 去重清理重复 tc。

    注意：
        - 避免使用 ``row.cells``（会触发 vMerge 递归导致 ``no tc element`` 错误）
        - 改为直接从 XML 层操作 ``<w:tc>`` 元素
        - 在 ``doc.save()`` 前调用此函数

    Args:
        doc: python-docx Document 对象
    """
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    for table in doc.tables:
        for row in table.rows:
            tr = row._tr
            tcs = tr.findall(qn("w:tc"))
            if len(tcs) <= 1:
                continue

            # 按 tc XML 元素 id 去重（避免触发 row.cells 的 vMerge 迭代）
            seen_ids = set()
            unique_tcs = []
            for tc in tcs:
                eid = id(tc)
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    unique_tcs.append(tc)

            # 无重复元素则跳过
            if len(unique_tcs) == len(tcs):
                continue

            # 只保留唯一的 tc 元素
            kept_set = set(unique_tcs)
            for child in list(tr):
                if child.tag == f"{ns}tc" and child not in kept_set:
                    tr.remove(child)