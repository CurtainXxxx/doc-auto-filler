"""Standalone HTTP service for chat, uploads, filling, previews and downloads."""
import argparse
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from runtime import WORKSPACE, PROJECT_ROOT, DATA_DIR, new_context
from service import GraphService
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
service = GraphService()
app = FastAPI(title="Document Auto Filler")
app.mount("/web", StaticFiles(directory=str(PROJECT_ROOT / "web"), html=True), name="web")

def _safe_path(user_path: str) -> str:
    """Check resolved directory boundaries on Windows and Linux."""
    if not user_path:
        raise HTTPException(400, "路径不能为空")
    if user_path.startswith(("https://", "http://")):
        return user_path
    candidate = Path(user_path)
    candidate = (candidate if candidate.is_absolute() else WORKSPACE / candidate).resolve()
    if not any(candidate.is_relative_to(base) for base in [WORKSPACE, DATA_DIR, Path(tempfile.gettempdir()).resolve()]):
        raise HTTPException(403, "非法路径：禁止访问指定目录外的文件")
    return str(candidate)

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse("/web/")

async def _run(payload, ctx):
    task = asyncio.create_task(service.run(payload, ctx))
    service.running_tasks[ctx.run_id] = task
    try:
        return await asyncio.wait_for(task, timeout=900)
    finally:
        service.running_tasks.pop(ctx.run_id, None)

@app.post("/run")
async def http_run(request: Request):
    ctx = new_context("run", request.headers)
    try:
        result = await _run(await request.json(), ctx)
        return jsonable_encoder({**result, "run_id": ctx.run_id})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post("/stream_run")
async def http_stream_run(request: Request):
    ctx = new_context("stream_run", request.headers)
    return StreamingResponse(service.stream_sse(await request.json(), ctx), media_type="text/event-stream", headers={"X-Run-ID": ctx.run_id})

@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    ctx = new_context("chat", request.headers)
    payload = await request.json()
    if payload.get("stream"):
        return StreamingResponse(service.stream_sse(payload, ctx, openai=True), media_type="text/event-stream", headers={"X-Run-ID": ctx.run_id, "X-Accel-Buffering": "no"})
    try:
        result = await _run(payload, ctx)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    from langchain_core.messages import AIMessage
    content = next((m.content for m in reversed(result.get("messages", [])) if isinstance(m, AIMessage)), "")
    return {"id": "chatcmpl-" + ctx.run_id, "object": "chat.completion", "model": "agent", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}

@app.post("/cancel/{run_id}")
async def http_cancel(run_id: str):
    return service.cancel_run(run_id)

@app.post("/node_run/{node_id}")
async def http_node_run(node_id: str, request: Request):
    try:
        return jsonable_encoder(await service.run_node(node_id, await request.json(), new_context("node")))
    except KeyError as exc:
        raise HTTPException(404, "Unknown node") from exc

@app.get("/health")
async def health_check():
    try:
        # 这里可以添加更多的健康检查逻辑
        return {
            "status": "ok",
            "message": "Service is running",
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/upload")
async def upload_file(files: list[UploadFile] = File(...)):
    """上传知识文件（txt/csv/docx/pdf/图片），提取文本后返回给前端和Agent。

    支持单文件和多文件上传。
    文件大小上限 10MB。
    提取的文本缓存到临时文件，供后续 prefill 调用使用。
    """
    import tempfile
    from tools.knowledge_tool import extract_text_from_upload

    results = []
    total_size = 0

    for file in files:
        if not file.filename:
            continue

        # 文件大小限制 10MB
        content = await file.read()
        total_size += len(content)
        if len(content) > 10 * 1024 * 1024:
            results.append({
                "success": False,
                "filename": file.filename,
                "error": f"File too large ({len(content)/1024/1024:.1f}MB, max 10MB)",
            })
            continue

        # 保存到临时文件
        suffix = os.path.splitext(file.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            result = extract_text_from_upload(tmp_path)
            result["filename"] = file.filename
            result["file_path"] = tmp_path  # 保留临时文件路径供预填使用
            results.append(result)
        except Exception as e:
            # 即使文本提取失败，也保留文件路径
            results.append({
                "success": False,
                "filename": file.filename,
                "error": str(e),
                "file_path": tmp_path,
            })

    if not results:
        raise HTTPException(status_code=400, detail="No valid files provided")

    # 单文件：保持向后兼容，返回扁平结构
    if len(results) == 1:
        return JSONResponse(content=results[0])

    # 多文件：返回列表结构
    return JSONResponse(content={
        "success": True,
        "multiple": True,
        "file_count": len(results),
        "files": results,
    })


@app.post("/upload-template")
async def upload_template_file(file: UploadFile = File(...)):
    """上传用户自定义的 docx 模板，替换内置模板进行填充。

    处理流程：
    1. 保存到 assets/uploads/ 目录
    2. 调用 template_analyzer 解析字段结构
    3. 调用 old_report_extractor 反向提取可继承数据
    4. 返回字段清单 + 预填建议
    """
    import tempfile
    from docx import Document

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not file.filename.lower().endswith(('.docx', '.doc')):
        raise HTTPException(status_code=400, detail="Only .docx/.doc files are supported")

    # 文件大小限制 10MB
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    # 保存到临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # 提取文档文本内容（包括表格）
    extracted_text = ""
    try:
        doc = Document(tmp_path)
        # 提取段落
        for para in doc.paragraphs:
            if para.text.strip():
                extracted_text += para.text.strip() + "\n"
        # 提取表格
        for t_idx, table in enumerate(doc.tables):
            extracted_text += f"\n--- 表格{t_idx+1} ---\n"
            for row in table.rows:
                cells_text = []
                for cell in row.cells:
                    cells_text.append(cell.text.strip())
                extracted_text += " | ".join(cells_text) + "\n"
    except Exception as e:
        logger.warning(f"Failed to extract text from template: {e}")
        extracted_text = ""

    return JSONResponse(content={
        "success": True,
        "filename": file.filename,
        "template_path": tmp_path,
        "extracted_text": extracted_text,
        "message": f"模板文件已上传：{file.filename}，请告诉Agent分析此模板"
    })


@app.post("/prefill")
async def prefill_template(request: Request):
    """AI预填：从知识文件自动提取字段值，返回带置信度的预填结果。

    请求体：
    {
        "file_paths": ["path/to/file1.docx", "path/to/file2.pdf"],
        "template_path": "path/to/template.docx" 或 "评价报告"
    }
    """
    try:
        body = await request.json()
        file_paths = body.get("file_paths", [])
        template_path = body.get("template_path", "")

        if not file_paths:
            return JSONResponse(content={"success": False, "message": "未提供知识文件路径"})
        if not template_path:
            return JSONResponse(content={"success": False, "message": "未提供模板路径"})

        # 解析模板路径
        workspace = str(WORKSPACE)
        template_map = {
            "评价报告": "评价报告模板.docx",
            "试卷分析": "试卷分析模板.docx",
            "关联矩阵": "关联矩阵模板.docx",
        }
        if template_path in template_map:
            full_template_path = os.path.join(workspace, "assets", "templates", template_map[template_path])
        else:
            # 非内置模板名，走路径安全校验
            full_template_path = _safe_path(template_path)

        if not os.path.exists(full_template_path):
            return JSONResponse(content={"success": False, "message": f"模板文件不存在: {template_path}"})

        # 解析知识文件路径（全部走安全校验）
        full_file_paths = []
        for fp in file_paths:
            full_file_paths.append(_safe_path(fp))

        # 调用预填工具
        from tools.prefill_tool import prefill_from_file_paths
        result = prefill_from_file_paths(full_file_paths, full_template_path)

        return JSONResponse(content=result)

    except Exception as e:
        logger.exception("prefill failed")
        return JSONResponse(content={"success": False, "message": f"预填失败: {e}"})


@app.get("/template-preview")
async def template_preview(path: str = ""):
    """将 docx 模板转为 HTML 预览，返回 HTML + 字段映射"""
    if not path:
        return JSONResponse(content={"success": False, "message": "缺少 path 参数"})

    # 支持 path 是模板名（评价报告/试卷分析/关联矩阵）或文件路径
    workspace = str(WORKSPACE)
    full_path = path

    # 内置模板名
    template_map = {
        "评价报告": "评价报告模板.docx",
        "试卷分析": "试卷分析模板.docx",
        "关联矩阵": "关联矩阵模板.docx",
    }
    if path in template_map:
        full_path = os.path.join(workspace, "assets", "templates", template_map[path])
    else:
        # 非内置模板名，走路径安全校验
        full_path = _safe_path(path)

    if not os.path.exists(full_path):
        return JSONResponse(content={"success": False, "message": f"文件不存在: {path}"})

    try:
        from tools.docx_preview import docx_to_html
        result = docx_to_html(full_path)
        return JSONResponse(content={
            "success": True,
            "html": result["html"],
            "field_map": result["field_map"],
            "label_to_fields": result["label_to_fields"],
        })
    except Exception as e:
        logger.exception("template-preview failed")
        return JSONResponse(content={"success": False, "message": f"预览生成失败: {e}"})


@app.get("/generated-preview")
async def generated_preview(local_path: str = ""):
    """将生成的 docx 文件转为 HTML 预览（含填写后的内容）"""
    if not local_path:
        return JSONResponse(content={"success": False, "message": "缺少 local_path 参数"})

    # 安全校验路径
    safe_local_path = _safe_path(local_path)

    if not os.path.exists(safe_local_path):
        return JSONResponse(content={"success": False, "message": f"文件不存在: {local_path}"})

    try:
        from tools.docx_preview import docx_to_html
        result = docx_to_html(safe_local_path)
        return JSONResponse(content={
            "success": True,
            "html": result["html"],
            "field_map": result["field_map"],
            "label_to_fields": result["label_to_fields"],
        })
    except Exception as e:
        logger.exception("generated-preview failed")
        return JSONResponse(content={"success": False, "message": f"预览生成失败: {e}"})


@app.post("/cofill-generate")
async def cofill_generate(request: Request):
    """人机协同模式直接生成文档（绕过 LangGraph Agent），
    接受已确认的 label→value 对，直接生成 docx 并返回下载链接"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"success": False, "message": "请求体必须是 JSON"})

    template_path = body.get("template_path", "")
    field_values = body.get("field_values", {})

    if not template_path or not field_values:
        return JSONResponse(content={"success": False, "message": "缺少 template_path 或 field_values"})

    from tools.docx_preview import _resolve_template_path
    safe_tpl = _safe_path(_resolve_template_path(template_path))
    if not os.path.exists(safe_tpl):
        return JSONResponse(content={"success": False, "message": f"模板文件不存在: {template_path}"})

    try:
        from tools.template_analyzer import analyze_template
        from tools.form_filling_state import FormFillingState
        from tools.filling.doc_builder import fill_custom_template
        from tools.docx_upload import upload_and_validate

        analysis = analyze_template(safe_tpl)
        label_fields = analysis.get("label_fields", [])

        # 用 label_fields 构建 field_id→value 映射
        filled = {}
        for lf in label_fields:
            fid = lf.get("field_id", "")
            label = lf.get("label", "")
            raw_label = lf.get("raw_label", "")
            # 按 label / raw_label 匹配用户确认的值
            val = field_values.get(label) or field_values.get(raw_label) or ""
            if val:
                filled[fid] = val

        # 余下的其他字段（可能不是 label_fields 里有的）
        matched_labels = {lf.get("label", "") for lf in label_fields} | {lf.get("raw_label", "") for lf in label_fields}
        for key, val in field_values.items():
            if key not in matched_labels and val:
                filled[key] = val

        # 生成文档
        docx_bytes, _, _, _ = fill_custom_template(safe_tpl, filled)

        # 上传到对象存储
        upload_result = upload_and_validate(
            docx_bytes=docx_bytes,
            name_prefix="custom_report",
            display_name="cofill_doc",
            template_path=safe_tpl,
            validate=False,
        )
        download_url = upload_result.get("download_url", "")
        local_path = upload_result.get("local_path", "")

        return JSONResponse(content={
            "success": True,
            "message": "文档已生成",
            "download_url": download_url,
            "local_path": local_path,
        })
    except Exception as e:
        logger.exception("cofill-generate failed")
        return JSONResponse(content={"success": False, "message": f"生成失败: {e}"})


@app.get(path="/download-docx")
async def http_download_docx(file_path: str = ""):
    """代理下载 docx 文件，统一处理本地路径和远程对象存储 URL。

    两种模式：
    - 远程 URL（http/https）：下载到 /tmp 后以 FileResponse 返回
    - 本地路径：直接通过 FileResponse 流式返回（含路径注入安全校验）
    """
    if not file_path:
        return JSONResponse(content={"success": False, "message": "缺少 file_path"})

    # 如果是远程URL（对象存储），先下载到 /tmp 再返回
    if file_path.startswith("http://") or file_path.startswith("https://"):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(file_path)
                resp.raise_for_status()
                # 从URL提取文件名
                from urllib.parse import urlparse, unquote
                url_path = unquote(urlparse(file_path).path)
                fname = os.path.basename(url_path).split("?")[0]
                if not fname.endswith(".docx"):
                    fname = "document.docx"
                tmp_path = os.path.join(tempfile.gettempdir(), fname)
                with open(tmp_path, "wb") as f:
                    f.write(resp.content)
                return FileResponse(tmp_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=fname)
        except Exception as e:
            return JSONResponse(content={"success": False, "message": f"下载失败: {e}"})

    # 本地文件路径 — 安全校验
    full_path = _safe_path(file_path)
    if not os.path.isabs(file_path):
        from tools.docx_preview import _resolve_template_path
        full_path = _safe_path(_resolve_template_path(file_path))
    if not os.path.isfile(full_path):
        return JSONResponse(content={"success": False, "message": f"文件不存在: {file_path}"})
    fname = os.path.basename(full_path)
    return FileResponse(full_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=fname)


@app.get(path="/convert-pdf")
async def http_convert_pdf(file_path: str = ""):
    """将 docx 转为 PDF（用 LibreOffice），保留原格式"""
    import subprocess
    if not file_path:
        return JSONResponse(content={"success": False, "message": "缺少 file_path"})
    # 支持模板名解析（如"试卷分析"）
    from tools.docx_preview import _resolve_template_path
    full_path = _safe_path(_resolve_template_path(file_path))
    if not os.path.isfile(full_path):
        return JSONResponse(content={"success": False, "message": f"文件不存在: {file_path}"})
    try:
        out_dir = str(DATA_DIR / "pdf_output")
        os.makedirs(out_dir, exist_ok=True)
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, full_path],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return JSONResponse(content={"success": False, "message": f"转换失败: {result.stderr}"})
        pdf_name = os.path.splitext(os.path.basename(full_path))[0] + ".pdf"
        pdf_path = os.path.join(out_dir, pdf_name)
        if not os.path.isfile(pdf_path):
            return JSONResponse(content={"success": False, "message": "PDF 文件未生成"})
        return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_name)
    except Exception as e:
        return JSONResponse(content={"success": False, "message": f"转换出错: {e}"})


@app.get(path="/graph_parameter")
async def http_graph_inout_parameter(request: Request):
    return service.graph_inout_schema()


def main():
    """Start HTTP server or invoke the graph from the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", choices=["http", "flow", "node"], default="http")
    parser.add_argument("-p", type=int, default=5000)
    parser.add_argument("-n", default="")
    parser.add_argument("-i", default='{"text": "你好"}')
    args = parser.parse_args()
    if args.m == "http":
        uvicorn.run(app, host="0.0.0.0", port=args.p)
    else:
        payload = json.loads(args.i)
        result = asyncio.run(service.run_node(args.n, payload) if args.m == "node" else service.run(payload))
        print(json.dumps(jsonable_encoder(result), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
