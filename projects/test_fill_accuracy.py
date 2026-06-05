"""
填充准确率自动化测试脚本
直接调用工具函数，绕过 AI 助手，模拟真实填充链路。

运行方式（在 Coze 终端）：
    cd /workspace/projects
    python test_fill_accuracy.py

输出：每个模板 × 每个难度 → 填充率表格
"""

import os
import sys
import json
import time

WORKSPACE = os.getenv("COZE_WORKSPACE_PATH", os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(WORKSPACE, "src")
sys.path.insert(0, SRC_DIR)

# ── 配置 ──
TEMPLATES = {
    "考场记录表":  "assets/templates/考场记录表.docx",
    "教务数据申请": "assets/templates/教务数据申请表.docx",
    "教材建设申报": "assets/templates/教材建设申报书.docx",
    "试卷分析":    "assets/templates/试卷分析模板.docx",
    "关联矩阵":    "assets/templates/关联矩阵模板.docx",
    "评价报告":    "assets/templates/评价报告模板.docx",
}

# 测试矩阵：每个模板对应的知识文件（相对于 assets/knowledge/）
TEST_MATRIX = {
    "考场记录表": [
        ("简单", "assets/knowledge/考场记录表/考场记录表_【简单】.txt"),
        ("中等", "assets/knowledge/考场记录表/考场记录表_【中等】.txt"),
        ("困难", "assets/knowledge/考场记录表/考场记录表_【困难】.txt"),
    ],
    "教务数据申请": [
        ("简单", "assets/knowledge/教务数据申请/教务数据申请_【简单】.txt"),
        ("中等", "assets/knowledge/教务数据申请/教务数据申请_【中等】.txt"),
        ("困难", "assets/knowledge/教务数据申请/教务数据申请_【困难】.txt"),
    ],
    "教材建设申报": [
        ("简单", "assets/knowledge/教材建设申报/教材建设申报_【简单】.txt"),
        ("中等", "assets/knowledge/教材建设申报/教材建设申报_【中等】.txt"),
        ("困难", "assets/knowledge/教材建设申报/教材建设申报_【困难】.txt"),
    ],
    "试卷分析": [
        ("简单", "assets/knowledge/试卷分析/试卷分析_【简单】.txt"),
        ("中等", "assets/knowledge/试卷分析/试卷分析_【中等】.txt"),
        ("困难", "assets/knowledge/试卷分析/试卷分析_【困难】.txt"),
    ],
    "关联矩阵": [
        ("中等", "assets/knowledge/关联矩阵/关联矩阵_【中等】.txt"),
        ("困难", "assets/knowledge/关联矩阵/关联矩阵_【困难】.txt"),
    ],
    "评价报告": [
        ("中等", "assets/knowledge/评价报告/评价报告_【中等】.txt"),
        ("困难", "assets/knowledge/评价报告/评价报告_【困难】.txt"),
    ],
}


def resolve_path(relative_path: str) -> str:
    """解析文件路径（兼容 Coze 和本地）"""
    full = os.path.join(WORKSPACE, relative_path)
    if os.path.isfile(full):
        return full
    return relative_path


def test_single(template_name: str, template_path: str,
                difficulty: str, knowledge_path: str) -> dict:
    """测试单个知识文件对模板的填充率"""
    from tools.prefill_tool import prefill_from_file_paths

    tpl = resolve_path(template_path)
    kn = resolve_path(knowledge_path)

    if not os.path.isfile(tpl):
        return {"error": f"模板不存在: {tpl}"}
    if not os.path.isfile(kn):
        return {"error": f"知识文件不存在: {kn}"}

    start = time.time()
    try:
        result = prefill_from_file_paths([kn], tpl)
        elapsed = time.time() - start

        if not result.get("success"):
            return {"error": result.get("message", "未知错误"), "elapsed": elapsed}

        # 收集字段详情
        field_details = []
        for f in result.get("fields", []):
            field_details.append({
                "label": f.get("label", ""),
                "field_id": f.get("field_id", ""),
                "value": str(f.get("value", ""))[:50] if f.get("value") else "",
                "confidence": f.get("confidence", 0),
                "status": f.get("status", "empty"),
            })

        return {
            "success": True,
            "template": template_name,
            "difficulty": difficulty,
            "total_fields": result.get("template_fields", 0),
            "prefilled": result.get("prefilled", 0),
            "needs_review": result.get("needs_review", 0),
            "still_empty": result.get("still_empty", 0),
            "fill_rate_pct": result.get("fill_rate_pct", 0),
            "elapsed": round(elapsed, 1),
            "message": result.get("summary", ""),
            "field_details": field_details,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {"error": str(e), "elapsed": elapsed}


def main():
    print("=" * 80)
    print("教务文档自动填充 — 准确率测试")
    print(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"工作目录: {WORKSPACE}")
    print("=" * 80)
    print()

    all_results = []
    total_tests = sum(len(v) for v in TEST_MATRIX.values())

    for template_name, test_cases in TEST_MATRIX.items():
        template_path = TEMPLATES[template_name]
        print(f"\n{'─' * 60}")
        print(f"  📋 {template_name}")
        print(f"{'─' * 60}")

        for difficulty, knowledge_path in test_cases:
            print(f"\n  [{difficulty}] {os.path.basename(knowledge_path)}")
            result = test_single(template_name, template_path, difficulty, knowledge_path)

            if "error" in result:
                print(f"    ❌ 错误: {result['error']}")
                all_results.append({
                    "template": template_name,
                    "difficulty": difficulty,
                    "error": result["error"],
                })
            else:
                fill_rate = result["fill_rate_pct"]
                bar = "█" * int(fill_rate / 5) + "░" * (20 - int(fill_rate / 5))
                print(f"    字段数: {result['total_fields']}")
                print(f"    填充率: {fill_rate}%  {bar}")
                print(f"    已填: {result['prefilled']} | 待审核: {result['needs_review']} | 未填: {result['still_empty']}")
                print(f"    耗时: {result['elapsed']}s")
                print(f"    总结: {result['message']}")

                # 显示低填充字段
                empty_fields = [f for f in result["field_details"] if f["status"] == "empty"]
                review_fields = [f for f in result["field_details"] if f["status"] == "review"]
                if empty_fields:
                    print(f"    ⚠ 未填充字段 ({len(empty_fields)}):")
                    for f in empty_fields[:5]:
                        print(f"      - {f['label']} ({f['field_id']})")
                    if len(empty_fields) > 5:
                        print(f"      ... 还有 {len(empty_fields) - 5} 个")
                if review_fields:
                    print(f"    🔶 待确认字段 ({len(review_fields)}):")
                    for f in review_fields[:3]:
                        print(f"      - {f['label']}: {f['value']} (置信度: {f['confidence']})")

                all_results.append(result)

    # ── 汇总表格 ──
    print(f"\n\n{'=' * 80}")
    print("  汇总表格")
    print(f"{'=' * 80}")
    print(f"{'模板':<12} {'难度':<6} {'总字段':<8} {'已填':<6} {'填充率':<8} {'耗时':<8}")
    print("-" * 60)

    for r in all_results:
        if "error" in r:
            print(f"{r['template']:<12} {r['difficulty']:<6} {'ERROR':<8} {r['error'][:30]:<30}")
        else:
            print(f"{r['template']:<12} {r['difficulty']:<6} "
                  f"{r['total_fields']:<8} {r['prefilled']:<6} "
                  f"{r['fill_rate_pct']}%{'':<5} {r['elapsed']}s{'':<3}")

    # ── 按难度汇总 ──
    print(f"\n{'─' * 60}")
    print("  按难度汇总")
    print(f"{'─' * 60}")

    for difficulty in ["简单", "中等", "困难"]:
        same_diff = [r for r in all_results if r.get("difficulty") == difficulty and "error" not in r]
        if same_diff:
            avg = sum(r["fill_rate_pct"] for r in same_diff) / len(same_diff)
            print(f"  {difficulty}: 平均填充率 {avg:.1f}% ({len(same_diff)} 个测试)")

    # ── 总体平均 ──
    valid = [r for r in all_results if "error" not in r]
    if valid:
        overall = sum(r["fill_rate_pct"] for r in valid) / len(valid)
        print(f"\n  ★ 总体平均填充率: {overall:.1f}%")

    # ── 保存结果 ──
    output_path = os.path.join(WORKSPACE, "test_accuracy_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, ensure_ascii=False, fp=f, indent=2, default=str)
    print(f"\n  详细结果已保存到: {output_path}")

    print(f"\n{'=' * 80}")
    print("  测试完成")
    print(f"{'=' * 80}")


if __name__ == "__main__":
    main()
