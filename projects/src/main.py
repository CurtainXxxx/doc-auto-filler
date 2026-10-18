"""Standalone HTTP service for chat, uploads, filling, previews and downloads."""
import argparse
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from starlette.concurrency import run_in_threadpool
from starlette.background import BackgroundTask
from document_access import resolve_document_path, validate_download_url, save_upload, MAX_FILE_BYTES, MAX_UPLOAD_FILES
from http_limits import RequestSizeLimit
from typing import Any, Dict
import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
from runtime import WORKSPACE, PROJECT_ROOT, DATA_DIR, new_context
from service import GraphService, SessionBusyError
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
service = GraphService()
app = FastAPI(title="Document Auto Filler")
app.add_middleware(RequestSizeLimit)
app.mount("/web", StaticFiles(directory=str(PROJECT_ROOT / "web"), html=True), name="web")

def _safe_path(user_path: str) -> str:
    """Check resolved directory boundaries on Windows and Linux."""
    try:
        return resolve_document_path(user_path)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(403, str(exc)) from exc

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse("/web/")

async def _json_body(request: Request) -> dict:
    """Reject invalid JSON before accessing payload fields."""
    try:
        body = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "请求体必须是有效JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是JSON对象")
    return body

async def _run(payload, ctx):
    if ctx.run_id in service.running_tasks:
        raise HTTPException(409, "run_id正在使用中")
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
        result = await _run(await _json_body(request), ctx)
        return jsonable_encoder({**result, "run_id": ctx.run_id})
    except SessionBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post("/stream_run")
async def http_stream_run(request: Request):
    ctx = new_context("stream_run", request.headers)
    return StreamingResponse(service.stream_sse(await _json_body(request), ctx), media_type="text/event-stream", headers={"X-Run-ID": ctx.run_id})

@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    ctx = new_context("chat", request.headers)
    payload = await _json_body(request)
    if payload.get("stream"):
        return StreamingResponse(service.stream_sse(payload, ctx, openai=True), media_type="text/event-stream", headers={"X-Run-ID": ctx.run_id, "X-Accel-Buffering": "no"})
    try:
        result = await _run(payload, ctx)
    except SessionBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
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
        return jsonable_encoder(await service.run_node(node_id, await _json_body(request), new_context("node")))
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
    """Store up to ten knowledge files; each read is bounded to 10MB."""
    from tools.knowledge_tool import extract_text_from_upload
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(413, "一次最多上传10个文件")
    results = []
    for file in files:
        if not file.filename:
            continue
        try:
            content = await file.read(MAX_FILE_BYTES + 1)
            path = await run_in_threadpool(save_upload, content, Path(file.filename).suffix)
            result = await run_in_threadpool(extract_text_from_upload, path)
            result.update(filename=file.filename, file_path=path)
        except (ValueError, OSError) as exc:
            result = {"success": False, "filename": file.filename, "error": str(exc)}
        results.append(result)
    if not results:
        raise HTTPException(400, "No valid files provided")
    if len(results) == 1:
        return results[0]
    return {"success": True, "multiple": True, "file_count": len(results), "files": results}


@app.post("/upload-template")
async def upload_template_file(file: UploadFile = File(...)):
    """Validate and save an OOXML Word template in the managed upload directory."""
    from tools.knowledge_tool import _extract_text_from_file
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(400, "Only .docx files are supported; convert legacy .doc files first")
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "File too large (max 10MB)")
    path = None
    try:
        path = await run_in_threadpool(save_upload, content, ".docx")
        text = await run_in_threadpool(_extract_text_from_file, path)
        if text.startswith("[docx解析失败"):
            raise ValueError("无法解析Word文档")
    except Exception as exc:
        if path:
            Path(path).unlink(missing_ok=True)
        raise HTTPException(422, "模板不是有效的docx文件，或已超过存储限制") from exc
    return {"success": True, "filename": file.filename, "template_path": path,
            "extracted_text": text, "message": f"模板文件已上传：{file.filename}，请告诉Agent分析此模板"}


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
        body = await _json_body(request)
        file_paths = body.get("file_paths", [])
        template_path = body.get("template_path", "")

        if not isinstance(file_paths, list) or len(file_paths) > MAX_UPLOAD_FILES:
            raise HTTPException(400, "file_paths必须是最多10项的数组")
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
        result = await run_in_threadpool(prefill_from_file_paths, full_file_paths, full_template_path)

        return JSONResponse(content=result)

    except HTTPException:
        raise
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
        result = await run_in_threadpool(docx_to_html, full_path)
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
        result = await run_in_threadpool(docx_to_html, safe_local_path)
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
        body = await _json_body(request)
    except Exception:
        return JSONResponse(content={"success": False, "message": "请求体必须是 JSON"})

    template_path = body.get("template_path", "")
    field_values = body.get("field_values", {})

    if not isinstance(field_values, dict):
        raise HTTPException(400, "field_values必须是对象")
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

        analysis = await run_in_threadpool(analyze_template, safe_tpl)
        label_fields = analysis.get("label_fields", [])

        # 用 label_fields 构建 field_id→value 映射
        filled = {}
        for lf in label_fields:
            fid = lf.get("field_id", "")
            label = lf.get("label", "")
            raw_label = lf.get("raw_label", "")
            # 按 label / raw_label 匹配用户确认的值
            val = next((field_values[k] for k in (fid, label, raw_label) if k in field_values and field_values[k] is not None), "")
            if val is not None and val != "":
                filled[fid] = val

        # 余下的其他字段（可能不是 label_fields 里有的）
        matched_labels = {lf.get("label", "") for lf in label_fields} | {lf.get("raw_label", "") for lf in label_fields}
        for key, val in field_values.items():
            if key not in matched_labels and val is not None and val != "":
                filled[key] = val

        # 生成文档
        docx_bytes, _, _, _ = await run_in_threadpool(fill_custom_template, safe_tpl, filled, analysis)

        # 上传到对象存储
        upload_result = await run_in_threadpool(upload_and_validate,
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
            validate_download_url(file_path)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(403, str(exc)) from exc
        tmp_path = None
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                async with client.stream("GET", file_path) as response:
                    response.raise_for_status()
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx", dir=DATA_DIR) as output:
                        tmp_path = output.name
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_FILE_BYTES:
                                raise ValueError("远程文档超过10MB限制")
                            output.write(chunk)
            return FileResponse(tmp_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename="document.docx", background=BackgroundTask(os.unlink, tmp_path))
        except Exception as exc:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)
            logger.warning("Remote download failed: %s", type(exc).__name__)
            return JSONResponse(content={"success": False, "message": "下载失败，请检查存储配置和文档大小"})

    # 本地文件路径 — 安全校验
    full_path = _safe_path(file_path)
    if not os.path.isabs(file_path):
        from tools.docx_preview import _resolve_template_path
        full_path = _safe_path(_resolve_template_path(file_path))
    if not os.path.isfile(full_path):
        return JSONResponse(content={"success": False, "message": f"文件不存在: {file_path}"})
    if Path(full_path).suffix.lower() != ".docx":
        raise HTTPException(403, "只能下载Word文档")
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
        result = await run_in_threadpool(subprocess.run,
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
