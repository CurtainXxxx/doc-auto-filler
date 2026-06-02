"""简单知识文件端到端测试"""
import os, sys, types, json, tempfile
from unittest.mock import MagicMock
from contextvars import ContextVar

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

# ── Mock Coze ──
_cozu = types.ModuleType("coze_coding_utils")
_cozu_log = types.ModuleType("coze_coding_utils.log")
_cozu_log_write = types.ModuleType("coze_coding_utils.log.write_log")
_cozu_log_write.request_context = ContextVar("request_context", default=None)
_cozu_ctx = types.ModuleType("coze_coding_utils.runtime_ctx")
_cozu_ctx_context = types.ModuleType("coze_coding_utils.runtime_ctx.context")
_cozu_ctx_context.default_headers = lambda ctx=None: {}
_cozu_ctx_context.new_context = lambda method="", headers=None: MagicMock()
_cozu_helper = types.ModuleType("coze_coding_utils.helper")
_cozu_helper_stream = types.ModuleType("coze_coding_utils.helper.stream_runner")
_cozu_helper_stream.AgentStreamRunner = MagicMock()
_cozu_helper_stream.WorkflowStreamRunner = MagicMock()
_cozu_error = types.ModuleType("coze_coding_utils.error")
_cozu_error_classifier = types.ModuleType("coze_coding_utils.error.classifier")
_cozu_error_classifier.ErrorClassifier = MagicMock()
_cozu_log_node = types.ModuleType("coze_coding_utils.log.node_log")
_cozu_log_node.LOG_FILE = os.path.join(tempfile.gettempdir(), "agent.log")
_cozu_log_config = types.ModuleType("coze_coding_utils.log.config")
_cozu_log_config.LOG_LEVEL = "INFO"
_sdk = types.ModuleType("coze_coding_dev_sdk")
_sdk_s3 = types.ModuleType("coze_coding_dev_sdk.s3")

class MockS3:
    def __init__(self, **kwargs): pass
    def upload_file(self, content, name, content_type=""):
        p = os.path.join(tempfile.gettempdir(), os.path.basename(name))
        with open(p, "wb") as f: f.write(content)
        return name

_sdk_s3.S3SyncStorage = MockS3
class MockLLM:
    def __init__(self, **kwargs): pass
_sdk.LLMClient = MockLLM
_cozeloop = types.ModuleType("cozeloop")
from langgraph.checkpoint.memory import MemorySaver
_storage = types.ModuleType("storage.memory.memory_saver")
_storage.get_memory_saver = lambda: MemorySaver()

sys.modules.update({
    "coze_coding_utils": _cozu,
    "coze_coding_utils.log": _cozu_log,
    "coze_coding_utils.log.write_log": _cozu_log_write,
    "coze_coding_utils.log.node_log": _cozu_log_node,
    "coze_coding_utils.log.config": _cozu_log_config,
    "coze_coding_utils.runtime_ctx": _cozu_ctx,
    "coze_coding_utils.runtime_ctx.context": _cozu_ctx_context,
    "coze_coding_utils.helper": _cozu_helper,
    "coze_coding_utils.helper.stream_runner": _cozu_helper_stream,
    "coze_coding_utils.error": _cozu_error,
    "coze_coding_utils.error.classifier": _cozu_error_classifier,
    "coze_coding_dev_sdk": _sdk,
    "coze_coding_dev_sdk.s3": _sdk_s3,
    "cozeloop": _cozeloop,
    "storage.memory.memory_saver": _storage,
})

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)
os.environ.setdefault("COZE_WORKSPACE_PATH", PROJECT_ROOT)

from tools.prefill_tool import prefill_from_file_paths

TESTS = [
    ("考场记录表",   "assets/templates/考场记录表.docx",   "assets/knowledge/考场记录表/考场记录表_【简单】.txt"),
    ("评价报告",     "assets/templates/评价报告模板.docx", "assets/knowledge/评价报告/评价报告_【中等】.txt"),
    ("试卷分析",     "assets/templates/试卷分析模板.docx", "assets/knowledge/试卷分析/试卷分析_【简单】.txt"),
    ("关联矩阵",     "assets/templates/关联矩阵模板.docx", "assets/knowledge/关联矩阵/关联矩阵_【中等】.txt"),
    ("教材建设申报", "assets/templates/教材建设申报书.docx", "assets/knowledge/教材建设申报/教材建设申报_【简单】.txt"),
    ("教务数据申请", "assets/templates/教务数据申请表.docx", "assets/knowledge/教务数据申请/教务数据申请_【简单】.txt"),
]

def main():
    print("=" * 65)
    print("  V1 端到端测试：每个模板 x 最简单知识文件")
    print("=" * 65)

    total_pct = 0
    count = 0
    results = []

    for name, tpl_rel, kf_rel in TESTS:
        tpl = os.path.join(PROJECT_ROOT, tpl_rel)
        kf = os.path.join(PROJECT_ROOT, kf_rel)

        print(f"\n{'─' * 55}")
        print(f"  📄 {name}")

        if not os.path.exists(tpl):
            print(f"     ❌ 模板不存在")
            continue
        if not os.path.exists(kf):
            print(f"     ❌ 知识文件不存在")
            continue

        print(f"     ⏳ 运行中...", end=" ", flush=True)
        try:
            result = prefill_from_file_paths([kf], tpl)
            if result.get("success"):
                total = result["template_fields"]
                prefilled = result["prefilled"]
                review = result["needs_review"]
                empty = result["still_empty"]
                pct = result["fill_rate_pct"]
                confirmed = prefilled - review

                status = "🟢" if pct >= 80 else ("🟡" if pct >= 50 else "🔴")
                print(f"\r     {status} 填表率: {pct}% ({prefilled}/{total})")
                print(f"        已确认: {confirmed} | 需审核: {review} | 未填: {empty}")

                total_pct += pct
                count += 1
                results.append({"template": name, "fill_rate_pct": pct, "prefilled": prefilled, "total": total, "confirmed": confirmed, "review": review, "empty": empty})
            else:
                print(f"\r     ❌ 失败: {result.get('message', 'unknown')}")
                results.append({"template": name, "error": result.get("message", "unknown")})
        except Exception as e:
            print(f"\r     ❌ 异常: {e}")
            results.append({"template": name, "error": str(e)})

    print(f"\n{'=' * 65}")
    if count > 0:
        print(f"  🏆 平均填表率: {total_pct/count:.1f}% ({count}个模板)")
    print()

    # Save results
    out = os.path.join(PROJECT_ROOT, "test_simple_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"avg_fill_rate": round(total_pct/count, 1) if count else 0, "count": count, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"  结果已保存: {out}")

if __name__ == "__main__":
    main()
