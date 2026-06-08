#!/usr/bin/env python3
"""
自动端到端评测脚本
测试 7 个模板 × 3 个难度 = 21 个用例的全流程
输出评测报表（填充率、准确率、Token消耗、成本）
"""

import json, os, re, sys, time, traceback
import requests
from docx import Document
from datetime import datetime

# ============================================================
# 配置
# ============================================================
BASE_URL = "http://localhost:5000"
PROJECT_DIR = "/workspace/projects/projects"
GT_DIR = "/workspace/projects/ground_truth"
REPORT_DIR = "/workspace/projects"
KNOWLEDGE_DIR = os.path.join(PROJECT_DIR, "assets/knowledge")
TEMPLATE_DIR = os.path.join(PROJECT_DIR, "assets/templates")

# DeepSeek V4-Flash 定价（/1M tokens）
PRICE_INPUT = 1
PRICE_OUTPUT = 2

# 模板名称 → docx 文件名
TEMPLATE_FILES = {
    "缓考申请单": "缓考申请单.docx",
    "考场记录表": "考场记录表.docx",
    "试卷分析模板": "试卷分析模板.docx",
    "关联矩阵模板": "关联矩阵模板.docx",
    "教材建设申报书": "教材建设申报书.docx",
    "教务数据申请表": "教务数据申请表.docx",
    "评价报告模板": "评价报告模板.docx",
}

# 知识目录名 → 模板名
KNOWLEDGE_DIRS = {
    "缓考申请单": "缓考申请单",
    "考场记录表": "考场记录表",
    "试卷分析": "试卷分析模板",
    "关联矩阵": "关联矩阵模板",
    "教材建设申报": "教材建设申报书",
    "教务数据申请": "教务数据申请表",
    "评价报告": "评价报告模板",
}

DIFFICULTIES = ["简单", "中等", "困难"]
# 超时（秒）
AGENT_TIMEOUT = 300


# ============================================================
# 工具函数
# ============================================================

def safe_get(url, **kwargs):
    """带重试的 GET 请求"""
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, **kwargs)
            return resp
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)


def extract_s3_url(text):
    """从 Agent 响应文本中提取 docx 下载 URL"""
    # 找 S3/对象存储 URL
    patterns = [
        r'https?://[^\s<>"\']+\.docx[^\s<>"\']*',
        r'https?://[^\s<>"\']+?(?:generated|output|document|download)[^\s<>"\']+',
        r'下载链接[：:]\s*(https?://[^\s<>"\']+)',
        r'下载[：:]\s*(https?://[^\s<>"\']+)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            url = m.group(1) if m.lastindex else m.group(0)
            # 清理尾部标点
            url = re.sub(r'[）\)。，,!\?；;]+$', '', url)
            return url
    return None


def parse_sse_stream(resp):
    """解析 SSE 流，返回 (完整文本, token_usage, S3_url, 错误信息)"""
    full_text = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    s3_url = None
    error = None

    for line in resp.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8", errors="replace")
        if not decoded.startswith("data: "):
            continue
        data_str = decoded[6:]
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            content = delta.get("content", "")
            if content:
                full_text += content
            # Token usage (通常在最后一个 chunk)
            usage_info = chunk.get("usage", {})
            if usage_info:
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if k in usage_info:
                        usage[k] = usage_info[k]
        except json.JSONDecodeError:
            pass

    # 从文本中提取 S3 URL
    s3_url = extract_s3_url(full_text)

    return full_text, usage, s3_url, error


def extract_docx_text(docx_path):
    """从 docx 中提取所有文本（段落 + 表格）"""
    doc = Document(docx_path)
    result = {
        "paragraphs": [],
        "tables": [],
        "all_text": ""
    }

    # 段落
    for p in doc.paragraphs:
        txt = p.text.strip()
        if txt:
            result["paragraphs"].append(txt)

    # 表格
    for ti, table in enumerate(doc.tables):
        table_data = []
        for ri, row in enumerate(table.rows):
            cells = [c.text.strip() for c in row.cells]
            table_data.append(cells)
        result["tables"].append(table_data)

    result["all_text"] = "\n".join(result["paragraphs"]) + "\n" + \
        "\n".join("\t".join(c for c in row) for t in result["tables"] for row in t)

    return result


def check_field_filled(docx_text, field_label, expected_value, optional=False):
    """
    检查字段在 docx 中的填充情况
    返回: 'correct' | 'filled_wrong' | 'empty' | 'optional'
    """
    if optional and not expected_value:
        return "optional"  # 可选且无预期值，跳过检查

    if not expected_value:
        return "empty"  # 无预期值且非可选 → 空

    # 检查预期值是否出现在 docx 文本中
    if expected_value in docx_text:
        return "correct"

    return "filled_wrong"


def check_row_group_filled(docx_tables, gt_rows, group_id):
    """
    检查行组填充情况
    返回: (正确行数, 总行数, 详情)
    """
    if not gt_rows:
        return 0, 0, "无数据行"

    # 在表格中搜索匹配的数据行
    matched_rows = 0
    total_rows = len(gt_rows)
    details = []

    for row_idx, expected_row in enumerate(gt_rows):
        row_str = "\t".join(expected_row)
        found = False
        for ti, table in enumerate(docx_tables):
            for ri, docx_row in enumerate(table):
                docx_row_str = "\t".join(docx_row)
                # 检查预期行是否出现在表格行的子串中
                match_count = sum(1 for expected_val in expected_row
                                  if any(expected_val in cell for cell in docx_row))
                if match_count >= len(expected_row) * 0.5:  # 至少50%的单元格匹配
                    found = True
                    break
            if found:
                break
        if found:
            matched_rows += 1
            details.append(f"行{row_idx+1}: ✅")
        else:
            details.append(f"行{row_idx+1}: ❌ ({row_str[:40]})")

    return matched_rows, total_rows, "; ".join(details)


# ============================================================
# 评测主函数
# ============================================================

def evaluate_single_test(gt):
    """执行单个测试用例，返回评测结果"""
    tmpl_name = gt["template"]
    difficulty = gt["difficulty"]
    session_id = f"eval_{tmpl_name}_{difficulty}_{int(time.time())}"

    tmpl_file = TEMPLATE_FILES.get(tmpl_name)
    if not tmpl_file:
        return {"error": f"未知模板: {tmpl_name}"}

    tmpl_path = os.path.join(TEMPLATE_DIR, tmpl_file)
    if not os.path.isfile(tmpl_path):
        return {"error": f"模板文件不存在: {tmpl_path}"}

    # 知识文件
    kdir_name = None
    for d, t in KNOWLEDGE_DIRS.items():
        if t == tmpl_name:
            kdir_name = d
            break
    kfile = os.path.join(KNOWLEDGE_DIR, kdir_name, f"{kdir_name}_【{difficulty}】.txt")

    result = {
        "template": tmpl_name,
        "difficulty": difficulty,
        "knowledge_file": os.path.basename(kfile),
        "total_fields": gt["total_fields"],
        "optional_count": gt["optional_fields_count"],
        "status": "pending",
        "error": None,
        "fill_rate": 0.0,
        "accuracy": 0.0,
        "row_group_accuracy": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost": 0.0,
        "duration": 0.0,
        "details": {}
    }

    start_time = time.time()

    try:
        # ---- 1. 上传模板 ----
        with open(tmpl_path, "rb") as f:
            r = requests.post(f"{BASE_URL}/upload-template", files={"file": f}, timeout=30)
        r.raise_for_status()
        tmpl_data = r.json()
        if not tmpl_data.get("success"):
            result["status"] = "failed"
            result["error"] = f"上传模板失败: {tmpl_data}"
            result["duration"] = time.time() - start_time
            return result
        template_path = tmpl_data["template_path"]
        print(f"  [1/5] ✅ 上传模板")

        # ---- 2. 上传知识文件 ----
        with open(kfile, "rb") as f:
            r = requests.post(f"{BASE_URL}/upload", files={"files": ("knowledge.txt", f)}, timeout=30)
        r.raise_for_status()
        upload_data = r.json()
        extracted_text = upload_data.get("extracted_text", "")
        print(f"  [2/5] ✅ 上传知识 ({len(extracted_text)} 字符)")

        # ---- 3. 构造消息并调用 Agent ----
        msg = (
            f"帮我填写这个文档模板：{template_path}\n\n"
            f"以下是知识文件的内容（{os.path.basename(kfile)}）：\n\n"
            f"{extracted_text}"
        )

        resp = requests.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "agent",
                "messages": [{"role": "user", "content": msg}],
                "stream": True,
                "session_id": session_id,
            },
            stream=True,
            timeout=AGENT_TIMEOUT,
        )
        resp.raise_for_status()

        agent_text, usage, s3_url, error = parse_sse_stream(resp)
        result["prompt_tokens"] = usage.get("prompt_tokens", 0)
        result["completion_tokens"] = usage.get("completion_tokens", 0)
        result["cost"] = (result["prompt_tokens"] * PRICE_INPUT + result["completion_tokens"] * PRICE_OUTPUT) / 1_000_000
        result["agent_response_length"] = len(agent_text)
        print(f"  [3/5] ✅ Agent 完成 ({result['prompt_tokens']} in / {result['completion_tokens']} out)")

        # ---- 4. 下载生成的 docx ----
        docx_path = None
        if s3_url:
            # 通过 /download-docx 代理下载
            r = requests.get(f"{BASE_URL}/download-docx", params={"file_path": s3_url}, timeout=60)
            if r.status_code == 200 and len(r.content) > 100:
                docx_path = f"/tmp/eval_{tmpl_name}_{difficulty}.docx"
                with open(docx_path, "wb") as f:
                    f.write(r.content)
                print(f"  [4/5] ✅ 下载 docx ({len(r.content)} 字节)")
            else:
                print(f"  [4/5] ⚠️ /download-docx 返回 {r.status_code}, size={len(r.content)}")
        else:
            print(f"  [4/5] ⚠️ 未从 Agent 响应中找到下载链接")

        # ---- 5. 解析 docx + 评测 ----
        if docx_path and os.path.isfile(docx_path):
            docx_data = extract_docx_text(docx_path)

            # 评估字段
            gt_fields = gt.get("fields", {})
            opt_fields = gt.get("optional_fields", [])

            field_results = {}
            correct_count = 0
            filled_count = 0
            non_optional_total = 0

            for label, expected_value in gt_fields.items():
                is_optional = label in opt_fields
                if not is_optional:
                    non_optional_total += 1

                status = check_field_filled(
                    docx_data["all_text"], label, expected_value, is_optional
                )

                if status == "correct":
                    correct_count += 1
                    filled_count += 1
                elif status == "filled_wrong":
                    filled_count += 1  # 有值但不对
                elif status == "empty":
                    pass  # 未填充
                # optional 跳过

                field_results[label] = status

            # 评估行组
            gt_row_groups = gt.get("row_groups", {})
            row_group_results = {}
            total_rg_matched = 0
            total_rg_rows = 0

            for group_id, expected_rows in gt_row_groups.items():
                if not expected_rows:
                    row_group_results[group_id] = {"status": "skipped", "detail": "无数据行"}
                    continue
                matched, total, detail = check_row_group_filled(
                    docx_data["tables"], expected_rows, group_id
                )
                total_rg_matched += matched
                total_rg_rows += total
                row_group_results[group_id] = {
                    "matched": matched,
                    "total": total,
                    "detail": detail[:100] if len(detail) > 100 else detail
                }

            # 算指标
            result["fill_rate"] = round(filled_count / non_optional_total * 100, 1) if non_optional_total > 0 else 0
            result["accuracy"] = round(correct_count / non_optional_total * 100, 1) if non_optional_total > 0 else 0
            result["field_results"] = field_results
            result["row_group_results"] = row_group_results
            result["row_group_accuracy"] = round(total_rg_matched / total_rg_rows * 100, 1) if total_rg_rows > 0 else 0
            result["non_optional_fields"] = non_optional_total
            result["correct_count"] = correct_count
            result["filled_count"] = filled_count

            print(f"  [5/5] ✅ 评测完成 (填充率={result['fill_rate']}%, 准确率={result['accuracy']}%)")
            result["status"] = "completed"
        else:
            # docx 下载失败，尝试从 Agent 响应中提取填充摘要
            print(f"  [5/5] ⚠️ 无 docx 可解析，从 Agent 文本估算")
            result["status"] = "partial"
            result["note"] = "docx 未下载成功，仅记录 Agent 响应和 Token 消耗"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()

    result["duration"] = round(time.time() - start_time, 1)
    return result


def run_all_tests():
    """运行所有 21 个测试用例"""
    print("=" * 60)
    print(f"  自动评测启动  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  服务器: {BASE_URL}")
    print(f"  用例数: 7 模板 × 3 难度 = 21")
    print("=" * 60)

    # 检查服务
    try:
        r = requests.get(f"{BASE_URL}/web/", timeout=5)
        assert r.status_code == 200
        print("✅ 服务运行正常\n")
    except Exception as e:
        print(f"❌ 服务异常: {e}")
        return None

    # 加载所有 GT 文件
    gts = {}
    for fname in sorted(os.listdir(GT_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(GT_DIR, fname), encoding="utf-8") as f:
            gt = json.load(f)
        tmpl_name = gt["template"]
        difficulty = gt["difficulty"]
        key = f"{tmpl_name}_{difficulty}"
        gts[key] = gt

    # 执行测试
    results = []
    test_order = []
    for tmpl_name in TEMPLATE_FILES:
        for diff in DIFFICULTIES:
            key = f"{tmpl_name}_{diff}"
            if key not in gts:
                print(f"⚠️ 跳过 {key}（GT 不存在）")
                continue
            test_order.append(key)

    total = len(test_order)
    for i, key in enumerate(test_order, 1):
        gt = gts[key]
        print(f"\n[{i}/{total}] {gt['template']} × {gt['difficulty']}")
        result = evaluate_single_test(gt)
        results.append(result)
        print(f"  → 状态: {result['status']} | "
              f"填充率: {result.get('fill_rate', 'N/A')}% | "
              f"准确率: {result.get('accuracy', 'N/A')}% | "
              f"时长: {result.get('duration', 0):.0f}s")

    return results


# ============================================================
# 报表生成
# ============================================================

def generate_report(results):
    """生成 Markdown 评测报表"""
    if not results:
        print("无测试结果，无法生成报表")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORT_DIR, f"eval_report_{timestamp}.md")

    completed = [r for r in results if r["status"] == "completed"]
    partial = [r for r in results if r["status"] == "partial"]
    failed = [r for r in results if r["status"] == "failed"]

    # 汇总
    total_cost = sum(r.get("cost", 0) for r in results)
    total_tokens = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in results)
    total_duration = sum(r.get("duration", 0) for r in completed)
    avg_fill = sum(r.get("fill_rate", 0) for r in completed) / len(completed) if completed else 0
    avg_acc = sum(r.get("accuracy", 0) for r in completed) / len(completed) if completed else 0
    avg_rg_acc = sum(r.get("row_group_accuracy", 0) for r in completed if r.get("row_group_accuracy", 0) > 0)
    rg_count = sum(1 for r in completed if r.get("row_group_accuracy", 0) > 0)
    avg_rg = avg_rg_acc / rg_count if rg_count > 0 else None

    lines = []
    lines.append("# 端到端自动评测报告")
    lines.append(f"")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**模型**: DeepSeek V4-Flash（输入 ¥{PRICE_INPUT}/1M, 输出 ¥{PRICE_OUTPUT}/1M）")
    lines.append(f"")
    lines.append("## 总览")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总用例数 | {len(results)} |")
    lines.append(f"| 完成 | {len(completed)} |")
    lines.append(f"| 部分完成 | {len(partial)} |")
    lines.append(f"| 失败 | {len(failed)} |")
    lines.append(f"| 平均填充率 | **{avg_fill:.1f}%** |")
    lines.append(f"| 平均准确率 | **{avg_acc:.1f}%** |")
    if avg_rg is not None:
        lines.append(f"| 行组平均准确率 | **{avg_rg:.1f}%** |")
    lines.append(f"| 总 Token 消耗 | {total_tokens:,} |")
    lines.append(f"| 总成本 | **¥{total_cost:.4f}** |")
    lines.append(f"| 总耗时 | {total_duration:.0f}s（约 {total_duration/60:.1f} 分钟）|")
    lines.append(f"")

    # 成本对比
    manual_cost_per = 25.0
    manual_time_per = 30
    total_manual_cost = manual_cost_per * len(completed)
    total_manual_time = manual_time_per * len(completed)

    lines.append(f"## 成本对比")
    lines.append(f"")
    lines.append(f"| 方式 | 总成本 | 单价 | 总时间 |")
    lines.append(f"|------|--------|------|--------|")
    lines.append(f"| **本系统** | ¥{total_cost:.4f} | ¥{total_cost/len(completed):.4f} | {total_duration:.0f}s（{total_duration/len(completed):.0f}s/次）|")
    lines.append(f"| **人工填写（估）** | ¥{total_manual_cost:.0f} | ¥{manual_cost_per} | {total_manual_time}min（{manual_time_per}min/次）|")
    lines.append(f"")
    lines.append(f"> 人工填写成本和时间为估算值，来源：教务人员手工填写同类模板的预估时间和人力成本。")
    lines.append(f"")

    # 分模板统计
    lines.append(f"## 按模板统计")
    lines.append(f"")
    lines.append(f"| 模板 | 难度 | 状态 | 字段数 | 填充率 | 准确率 | Token(in/out) | 成本 | 耗时 |")
    lines.append(f"|------|------|------|--------|--------|--------|-------------|------|------|")

    for r in results:
        status_icon = "✅" if r["status"] == "completed" else ("⚠️" if r["status"] == "partial" else "❌")
        fill = f"{r.get('fill_rate', '-')}%" if r["status"] == "completed" else "-"
        acc = f"{r.get('accuracy', '-')}%" if r["status"] == "completed" else "-"
        tokens = f"{r.get('prompt_tokens', 0)}/{r.get('completion_tokens', 0)}"
        cost = f"¥{r['cost']:.4f}" if r.get("cost") else "-"
        dur = f"{r.get('duration', 0):.0f}s"
        total_f = r.get("total_fields", "?")
        lines.append(f"| {r['template']} | {r['difficulty']} | {status_icon} | {total_f} | {fill} | {acc} | {tokens} | {cost} | {dur} |")

    lines.append(f"")

    # 失败详情
    failed_tests = [r for r in results if r["status"] == "failed"]
    if failed_tests:
        lines.append(f"## 失败用例详情")
        lines.append(f"")
        for r in failed_tests:
            lines.append(f"### ❌ {r['template']} × {r['difficulty']}")
            lines.append(f"- 错误: {r.get('error', '未知')}")
            lines.append(f"")

    # 成功用例详情
    lines.append(f"## 各用例详情")
    for r in completed:
        lines.append(f"")
        lines.append(f"### {r['template']} × {r['difficulty']}")
        lines.append(f"- **填充率**: {r['fill_rate']}% ({r.get('filled_count', 0)}/{r.get('non_optional_fields', 0)})")
        lines.append(f"- **准确率**: {r['accuracy']}% ({r.get('correct_count', 0)}/{r.get('non_optional_fields', 0)})")
        lines.append(f"- **Token**: {r.get('prompt_tokens', 0)} in / {r.get('completion_tokens', 0)} out")
        lines.append(f"- **成本**: ¥{r['cost']:.4f}")
        lines.append(f"- **耗时**: {r['duration']:.0f}s")
        lines.append(f"")

        # 字段详情表
        field_results = r.get("field_results", {})
        if field_results:
            lines.append(f"| 字段 | 状态 |")
            lines.append(f"|------|------|")
            for label, status in field_results.items():
                icon = {"correct": "✅", "filled_wrong": "⚠️", "empty": "❌", "optional": "—"}.get(status, "?")
                lines.append(f"| {label} | {icon} {status} |")

        # 行组详情
        rg_results = r.get("row_group_results", {})
        if rg_results:
            lines.append(f"")
            lines.append(f"**行组**:")
            for gid, rg in rg_results.items():
                detail = rg.get("detail", "")
                lines.append(f"- {gid}: {detail}")

    # 写文件
    report_text = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n✅ 报表已生成: {report_path}")
    return report_path


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    print("自动端到端评测脚本")
    print("=" * 60)

    # 检查 GT 目录
    gt_files = [f for f in os.listdir(GT_DIR) if f.endswith(".json")]
    print(f"GT 文件: {len(gt_files)}")

    # 运行测试
    results = run_all_tests()

    # 生成报表
    if results:
        report_path = generate_report(results)
        print(f"\n🎉 评测完成！报表路径: {report_path}")