"""
教务报告生成工具 - 核心逻辑
"""
import os
import json
import copy
import re
import tempfile
from typing import Callable, Optional

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from langchain.tools import tool
from tools.docx_upload import upload_and_validate

from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from storage.memory.memory_saver import get_memory_saver

from tools.template_analyzer import analyze_template
from tools.docx_validator import strip_inline_formatting, sanitize_fill_text
from tools.form_filling_state import FormFillingState
from tools.docx_upload import upload_and_validate, validate_doc

# ── 填充引擎：从 filling/ 子模块导入 ──
from tools.filling.base import (
    get_unique_cells as _get_unique_cells,
    is_vmerge_continue as _is_vmerge_continue,
    is_vmerge_restart as _is_vmerge_restart,
    clone_rpr as _clone_rpr,
    get_first_run_rpr as _get_first_run_rpr,
    ensure_run_text as _ensure_run_text,
    set_paragraph_text_preserve_runs as _set_paragraph_text_preserve_runs,
    append_paragraph_with_text as _append_paragraph_with_text,
    set_tc_text as _set_tc_text,
    set_cell_text as _set_cell_text,
    add_table_row_after as _add_table_row_after,
)
from tools.filling.token_style import (
    get_run_text as _get_run_text,
    is_blank_run as _is_blank_run,
    is_placeholder_run as _is_placeholder_run,
    find_fill_target_run as _find_fill_target_run,
    write_text_to_run as _write_text_to_run,
    clear_other_runs_text as _clear_other_runs_text,
    resolve_effective_rpr as _resolve_effective_rpr,
    handle_set_mode as _handle_set_mode,
    handle_append_mode as _handle_append_mode,
    handle_replace_mode as _handle_replace_mode,
    fill_paragraph_line as _fill_paragraph_line,
    set_tc_text_v2 as _set_tc_text_v2,
    set_cell_text_v2 as _set_cell_text_v2,
)
from tools.filling.colon_filler import (
    replace_signature_and_date_in_tc as _replace_signature_and_date_in_tc,
    replace_embedded_date_in_tc as _replace_embedded_date_in_tc,
    append_value_to_tc_after_label as _append_value_to_tc_after_label,
    find_label_rPr_in_row as _find_label_rPr_in_row,
    fill_label_fields as _fill_label_fields,
)
from tools.filling.checkbox_filler import (
    OPTION_WORDS_SET as _OPTION_WORDS_SET,
    normalize_option_text as _normalize_option_text,
    find_section_title_above as _find_section_title_above,
    detect_checkbox_row as _detect_checkbox_row,
    fill_checkbox_row as _fill_checkbox_row,
    fill_checkbox_rows_in_table as _fill_checkbox_rows_in_table,
)
from tools.filling.paragraph_filler import fill_paragraph_fields as _fill_paragraph_fields
from tools.filling.row_group_filler import (
    fill_simple_row_groups as _fill_simple_row_groups,
    fill_multi_col_field as _fill_multi_col_field,
)
from tools.filling.data_expander import (
    simplify_generic_fields as _simplify_generic_fields,
    compress_sub_labels as _compress_sub_labels,
    expand_generic_data as _expand_generic_data,
)
from tools.filling.builtin_expander import (
    simplify_fields as _simplify_fields,
    expand_report_data as _expand_report_data,
)
from tools.filling.doc_builder import (
    build_report_docx as _build_report_docx,
    fill_custom_template as _fill_custom_template,
)
from tools.filling.postprocess import fix_merged_cells as _fix_merged_cells


# ── 分析结果缓存 ──
_analysis_cache = {}  # key: template_path, value: analysis_result

def _cache_analysis(template_path: str, analysis: dict):
    """缓存模板分析结果，供生成时复用（避免二次分析导致字段错配）"""
    _analysis_cache[template_path] = analysis

def _get_cached_analysis(template_path: str) -> Optional[dict]:
    """获取缓存的分析结果，无则返回None"""
    return _analysis_cache.get(template_path)


# ── 模板注册表 ──
TEMPLATE_REGISTRY = {
    "评价报告": "assets/2023-2024-2《xxx》 岭南师范学院专业课程目标达成度评价报告模板.docx",
    "试卷分析": "assets/2023-2024-2《xxx》 试卷分析模板.docx",
    "关联矩阵": "assets/2023-2024-2《xxx》岭南师范学院考题与课程目标及毕业要求关联矩阵表模板.docx",
}

# 复杂行组最大列数阈值
_MAX_SIMPLE_GROUP_COLS = 15


def _get_template_path(name: str) -> str:
    workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    file = TEMPLATE_REGISTRY.get(name)
    if not file:
        avail = ", ".join(TEMPLATE_REGISTRY.keys())
        raise ValueError(f"未找到模板'{name}'，可用模板: [{avail}]")
    return os.path.join(workspace, file)


def _build_field_id_value_map(label_fields, data):
    """把已填数据映射为精确的 field_id -> value，供前端精确回填。"""
    field_values = {}
    for field in label_fields:
        label = field["label"]
        if label not in data:
            continue
        value = data[label]
        if value is None:
            continue
        value_str = str(value).strip()
        if not value_str:
            continue
        field_values[field["field_id"]] = value_str
    return field_values



# ── 工具函数 ──

@tool
def list_templates() -> str:
    """列出所有可用的教务报告模板。"""
    try:
        templates = []
        for name, path in TEMPLATE_REGISTRY.items():
            templates.append({"name": name, "file": os.path.basename(path)})
        return json.dumps({"success": True, "templates": templates}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "message": f"获取模板列表失败: {e}"}, ensure_ascii=False)


@tool
def analyze_report_template(template_name: str) -> str:
    """解析指定模板，返回用户友好的字段清单和信息收集指引。
    在开始收集用户信息前必须先调用此工具。

    Args:
        template_name: 模板名称，从list_templates返回的名称中选择
    """
    try:
        path = _get_template_path(template_name)
        analysis = analyze_template(path)
        
        # 缓存分析结果，供生成时复用（避免二次分析导致字段错配）
        _cache_analysis(path, analysis)
        
        # 简化字段列表
        user_fields = _simplify_fields(analysis["label_fields"])
        
        # 构建收集指引
        guide_parts = []
        guide_parts.append(f"模板【{template_name}】共需填写 {len(user_fields)} 项信息：\n")
        
        for i, f in enumerate(user_fields, 1):
            if f["fill_mode"] == "group":
                subs = f.get("sub_labels", [])
                if len(subs) <= 5:
                    guide_parts.append(f"  {i}. {f['label']}（{', '.join(subs)}）")
                else:
                    guide_parts.append(f"  {i}. {f['label']}（共{len(subs)}项，可用逗号分隔）")
            else:
                guide_parts.append(f"  {i}. {f['label']}")
        
        guide_parts.append('\n提示：多值字段可用逗号分隔一次性提供，如"及百分比: 15,15,20,20,10,10,5,5,0"')
        
        return json.dumps({
            "success": True,
            "template_name": template_name,
            "total_fields": len(user_fields),
            "user_fields": user_fields,
            "row_groups_count": analysis["summary"]["total_row_groups"],
            "collection_guide": "\n".join(guide_parts),
        }, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({"success": False, "message": f"模板解析失败: {e}"}, ensure_ascii=False)


@tool
def generate_edu_report(template_name: str, report_data: str) -> str:
    """根据预设模板和用户数据生成教务报告文档（仅支持预设的3种模板）。

    Args:
        template_name: 模板名称（评价报告/试卷分析/关联矩阵）
        report_data: JSON格式的报告数据，键为字段名，值为字段值。
                     多值字段用逗号分隔，如 {"及百分比": "15,15,20,20,10,10,5,5,0"}
                     行组数据用二维数组，如 {"T0_G0": [["值1","值2"],...]}
    """
    ctx = request_context.get() or new_context(method="generate_edu_report")
    
    try:
        path = _get_template_path(template_name)
        
        # 解析报告数据
        if isinstance(report_data, str):
            data = json.loads(report_data)
        else:
            data = report_data
        
        # 从缓存加载分析结果（避免二次分析导致字段错配）
        cached_analysis = _get_cached_analysis(path)
        
        # 生成文档（传入已有的analysis_result避免二次分析）
        docx_bytes, analysis, expanded_data, rg_field_values = _build_report_docx(path, data, analysis_result=cached_analysis)
        
        # 上传到对象存储 + 保存本地 + 校验
        upload_result = upload_and_validate(docx_bytes, "edu_report", template_name, path)

        # 提取非空字段数据供前端更新预览
        filled_data = {k: v for k, v in data.items() if v and str(v).strip()}
        filled_field_values = _build_field_id_value_map(analysis["label_fields"], expanded_data)
        filled_field_values.update(rg_field_values)  # 合并行组field_id映射

        result = {
            "success": True,
            "message": "报告已成功生成并上传",
            "file_name": upload_result["file_name"],
            "download_url": upload_result["download_url"],
            "local_path": upload_result["local_path"],
            "filled_data": filled_data,
            "filled_field_values": filled_field_values,
            "validation": upload_result["validation"],
        }

        # 如果校验发现硬伤，标记但不阻断（前端可提示用户）
        if not upload_result["validation"]["valid"]:
            result["message"] = f"报告已生成，但存在结构问题: {'; '.join(upload_result['validation']['errors'])}"

        # 附加diff摘要信息
        if upload_result["validation"].get("diff"):
            result["diff_summary"] = upload_result["validation"]["diff"]["summary"]
            result["diff_filled_count"] = len(upload_result["validation"]["diff"]["filled"])
            result["diff_still_empty_count"] = len(upload_result["validation"]["diff"]["still_empty"])

        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "message": f"报告生成失败: {e}",
        }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# 通用模板工具：支持任意docx文件的自动识别和填充
# ═══════════════════════════════════════════════════════════════

@tool
def analyze_uploaded_template(file_path: str) -> str:
    """分析用户上传的任意Word模板文件，自动识别待填字段。
    支持识别：冒号字段、标签+空白格、勾选框、占位符、多列数据行、行组等。

    Args:
        file_path: 上传的docx文件路径（通常是上传后的临时文件路径）
    """
    ctx = request_context.get() or new_context(method="analyze_uploaded_template")
    
    try:
        import os
        try:
            full_path = _resolve_template_path(file_path)
        except ValueError:
            workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
            if not os.path.isabs(file_path):
                full_path = os.path.join(workspace, file_path)
            else:
                full_path = file_path
        
        if not os.path.exists(full_path):
            return json.dumps({"success": False, "message": f"文件不存在: {file_path}"}, ensure_ascii=False)
        
        analysis = analyze_template(full_path)
        # 通用模板使用通用简化函数（比_simplify_fields更智能地归组）
        user_fields = _simplify_generic_fields(analysis['label_fields'])
        
        # 构建用户友好的字段描述
        field_descriptions = []
        for f in user_fields:
            if f['fill_mode'] == 'group':
                subs = f['sub_labels']
                field_descriptions.append({
                    "label": f['label'],
                    "type": "group",
                    "sub_items": subs,
                    "hint": f"请提供{len(subs)}个值，用逗号分隔",
                })
            else:
                desc = {"label": f['label'], "type": "single"}
                # 添加提示
                label_lower = f['label']
                if any(k in label_lower for k in ['签字', '签名']):
                    desc['hint'] = "请填写姓名"
                elif any(k in label_lower for k in ['日期', '时间']):
                    desc['hint'] = "请填写日期"
                elif any(k in label_lower for k in ['百分比', '比例', '占比']):
                    desc['hint'] = "请填写数值"
                field_descriptions.append(desc)
        
        # 行组信息
        row_group_info = []
        for g in analysis['row_groups']:
            row_group_info.append({
                "group_id": g['group_id'],
                "num_cols": g['num_cols'],
                "template_row_count": g['template_row_count'],
                "column_labels": g.get('column_labels', []),
            })
        
        return json.dumps({
            "success": True,
            "file_name": os.path.basename(full_path),
            "total_fields": len(user_fields),
            "fields": field_descriptions,
            "row_groups": row_group_info,
            "summary": f"识别到{len(user_fields)}个待填字段和{len(row_group_info)}个数据行组",
        }, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({"success": False, "message": f"模板分析失败: {e}"}, ensure_ascii=False)


@tool
def generate_from_template(file_path: str, report_data: str) -> str:
    """根据用户上传的模板文件和填写数据，生成填充后的文档。
    支持任意docx模板文件的自动填充。

    Args:
        file_path: 模板文件路径（与analyze_uploaded_template使用的路径相同）
        report_data: JSON格式的填写数据，键为字段名，值为字段值。
                     多值字段用逗号分隔，如 {"及百分比": "15,15,20,20,10,10,5,5,0"}
                     行组数据用二维数组，如 {"T0_G0": [["值1","值2"],...]}
    """
    ctx = request_context.get() or new_context(method="generate_from_template")
    
    try:
        import os
        try:
            full_path = _resolve_template_path(file_path)
        except ValueError:
            # fallback: 直接路径检查
            workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
            if not os.path.isabs(file_path):
                full_path = os.path.join(workspace, file_path)
            else:
                full_path = file_path
        
        if not os.path.exists(full_path):
            return json.dumps({"success": False, "message": f"文件不存在: {file_path}"}, ensure_ascii=False)
        
        data = json.loads(report_data)
        
        # 从缓存加载分析结果（避免二次分析导致字段错配）
        cached_analysis = _get_cached_analysis(full_path)
        
        # 通用模板：直接填充，不调用expand（保留原格式），传入analysis_result避免二次分析
        doc_bytes, analysis, expanded_data, rg_field_values = _fill_custom_template(full_path, data, analysis_result=cached_analysis)
        
        # 上传到对象存储 + 保存本地 + 校验
        display_name = os.path.splitext(os.path.basename(full_path))[0]
        upload_result = upload_and_validate(doc_bytes, "custom_report", display_name, full_path)

        # 提取非空字段数据供前端更新预览
        filled_data = {k: v for k, v in data.items() if v and str(v).strip()}
        filled_field_values = _build_field_id_value_map(analysis["label_fields"], expanded_data)
        filled_field_values.update(rg_field_values)  # 合并行组field_id映射

        result = {
            "success": True,
            "message": "文档已成功生成并上传",
            "file_name": upload_result["file_name"],
            "download_url": upload_result["download_url"],
            "local_path": upload_result["local_path"],
            "filled_data": filled_data,
            "filled_field_values": filled_field_values,
            "validation": upload_result["validation"],
        }

        if not upload_result["validation"]["valid"]:
            result["message"] = f"文档已生成，但存在结构问题: {'; '.join(upload_result['validation']['errors'])}"

        # 附加diff摘要信息
        if upload_result["validation"].get("diff"):
            result["diff_summary"] = upload_result["validation"]["diff"]["summary"]
            result["diff_filled_count"] = len(upload_result["validation"]["diff"]["filled"])
            result["diff_still_empty_count"] = len(upload_result["validation"]["diff"]["still_empty"])

        return json.dumps(result, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "message": f"文档生成失败: {e}",
        }, ensure_ascii=False)


# ──────────────────────────────────────────────────────
# FormFillingState — 表单填充状态机
# ──────────────────────────────────────────────────────
_active_form_states: dict[str, FormFillingState] = {}

def _get_or_create_state(session_id: str, template_path: str = None) -> FormFillingState:
    """获取或创建表单填充状态"""
    if session_id not in _active_form_states and template_path:
        state = FormFillingState(session_id=session_id)
        analysis = analyze_template(template_path)
        state.init_from_analysis(analysis)
        state.template_path = template_path
        _cache_analysis(template_path, analysis)
        _active_form_states[session_id] = state
    return _active_form_states.get(session_id)


def _resolve_template_path(template_name_or_path: str) -> str:
    """将模板名称或路径解析为实际文件路径"""
    # 先匹配内置模板注册表
    workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    for name, rel_path in TEMPLATE_REGISTRY.items():
        if name == template_name_or_path:
            return os.path.join(workspace, rel_path)
    # 再检查是否为有效文件路径
    if os.path.exists(template_name_or_path):
        return template_name_or_path
    # 尝试在 assets 目录下查找
    assets_path = os.path.join(workspace, "assets", template_name_or_path)
    if os.path.exists(assets_path):
        return assets_path
    raise ValueError(f"找不到模板: {template_name_or_path}，可用模板: [{', '.join(TEMPLATE_REGISTRY.keys())}]")


@tool
def init_form_filling(session_id: str, template_name_or_path: str) -> str:
    """初始化表单填充状态机。在选择模板后调用，后续用 get_form_status / update_form_fields 推进填写。

    Args:
        session_id: 会话ID（可用任意唯一字符串）
        template_name_or_path: 模板名称（如"评价报告"）或模板文件路径
    """
    try:
        template_path = _resolve_template_path(template_name_or_path)

        state = FormFillingState(session_id=session_id, template_path=template_path,
                                  template_name=template_name_or_path)
        analysis = analyze_template(template_path)
        state.init_from_analysis(analysis)
        _cache_analysis(template_path, analysis)
        _active_form_states[session_id] = state

        summary = state.get_summary()
        summary["success"] = True
        summary["message"] = f"表单状态已初始化，共{summary['total_fields']}个字段待填"
        return json.dumps(summary, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "message": f"初始化表单状态失败: {e}"}, ensure_ascii=False)


@tool
def get_form_status(session_id: str) -> str:
    """查询当前表单填写状态：已填/未填/待确认字段，以及建议的下一批问题。

    Args:
        session_id: 会话ID
    """
    try:
        state = _active_form_states.get(session_id)
        if not state:
            return json.dumps({"success": False, "message": "表单状态不存在，请先调用 init_form_filling"}, ensure_ascii=False)

        summary = state.get_summary()
        next_batch = state.get_next_batch(5)
        missing_important = state.get_missing_important()

        result = {
            "success": True,
            "progress_pct": round(summary["filled_count"] / max(summary["total_fields"], 1) * 100, 1),
            "summary": summary,
            "next_batch_questions": [
                {"field_id": f["field_id"], "label": f.get("label", ""), "fill_mode": f["fill_mode"]}
                for f in next_batch
            ],
            "missing_important": missing_important[:10],
        }
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "message": f"查询状态失败: {e}"}, ensure_ascii=False)


@tool
def update_form_fields(session_id: str, field_values: str) -> str:
    """批量更新表单字段值。支持一次性填入多个字段，自动识别标签名映射到field_id。

    Args:
        session_id: 会话ID
        field_values: JSON字符串，格式为 {"标签名或field_id": "值", ...}，如 {"课程名称": "数据结构", "T0_R3_C1": "张三"}
    """
    try:
        state = _active_form_states.get(session_id)
        if not state:
            return json.dumps({"success": False, "message": "表单状态不存在，请先调用 init_form_filling"}, ensure_ascii=False)

        values = json.loads(field_values) if isinstance(field_values, str) else field_values
        matched, unmatched = state.bulk_fill(values)

        summary = state.get_summary()
        result = {
            "success": True,
            "matched_count": len(matched),
            "unmatched_labels": unmatched,
            "progress_pct": round(summary["filled_count"] / max(summary["total_fields"], 1) * 100, 1),
            "still_missing": summary["empty_count"],
            "next_batch": [
                {"field_id": f["field_id"], "label": f.get("label", ""), "fill_mode": f["fill_mode"]}
                for f in state.get_next_batch(5)
            ],
        }
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "message": f"更新字段失败: {e}"}, ensure_ascii=False)


def generate_form_document(session_id: str) -> str:
    """根据当前表单状态生成Word文档。所有已填字段会被写入文档，未填字段保持空白。

    Args:
        session_id: 会话ID
    """
    try:
        state = _active_form_states.get(session_id)
        if not state:
            return json.dumps({"success": False, "message": "表单状态不存在"}, ensure_ascii=False)

        # 获取已填字段值（label→value 映射，兼容生成引擎）
        filled_values = state.get_label_value_map()
        template_path = state.template_path

        if not filled_values:
            return json.dumps({"success": False, "message": "尚未填写任何字段"}, ensure_ascii=False)

        # 获取缓存的分析结果
        analysis = _get_cached_analysis(template_path)
        if not analysis:
            analysis = analyze_template(template_path)

        # 判断是内置模板还是自定义模板
        is_builtin = any(name in template_path for name in TEMPLATE_REGISTRY.keys())

        if is_builtin:
            docx_bytes, _, _, _ = _build_report_docx(template_path, filled_values, analysis_result=analysis)
        else:
            docx_bytes, _, _, _ = _fill_custom_template(template_path, filled_values, analysis_result=analysis)

        # 上传到对象存储 + 保存本地 + 校验
        display_name = state.template_name or "form"
        upload_result = upload_and_validate(docx_bytes, "edu_form", display_name, template_path)

        summary = state.get_summary()
        result = {
            "success": True,
            "message": f"文档已生成，填写率{round(summary['filled_count']/max(summary['total_fields'],1)*100, 1)}%",
            "download_url": upload_result["download_url"],
            "local_path": upload_result["local_path"],
            "progress": summary,
            "validation": upload_result["validation"],
        }

        if upload_result["validation"].get("diff"):
            result["diff_summary"] = upload_result["validation"]["diff"]["summary"]
            result["diff_filled_count"] = len(upload_result["validation"]["diff"]["filled"])
            result["diff_still_empty_count"] = len(upload_result["validation"]["diff"]["still_empty"])

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"success": False, "message": f"生成文档失败: {e}"}, ensure_ascii=False)
