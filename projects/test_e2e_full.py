#!/usr/bin/env python3
"""
端到端自动化测试 — 模拟用户真实操作链路：
1. 启动 FastAPI 服务
2. 上传模板文件 → 获取模板路径
3. 发送消息给 Agent（告知模板路径，让 Agent 分析）
4. 上传知识文件 → 获取文件路径
5. 发送消息给 Agent（告知知识文件路径，让 Agent 预填）
6. 等待 Agent 填写完成并生成 docx
7. 下载 docx 验证

用法:
  python test_e2e_full.py --template <模板名/路径> --knowledge <知识文件路径>
"""

import os, sys, json, time, re, argparse, subprocess, signal, atexit, tempfile
import requests
from typing import Optional

BASE_URL = "http://localhost:5000"
SERVER_PROC: Optional[subprocess.Popen] = None
SESSION_ID = f"e2e_test_{int(time.time())}_{os.urandom(3).hex()}"


def start_server():
    """启动 FastAPI 预览服务"""
    global SERVER_PROC
    project_root = "/workspace/projects/projects"
    env = os.environ.copy()
    env["COZE_WORKSPACE_PATH"] = "/workspace/projects"
    SERVER_PROC = subprocess.Popen(
        ["python", "src/main.py", "-m", "http", "-p", "5000"],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # 等待服务启动
    for i in range(30):
        try:
            r = requests.get(f"{BASE_URL}/health", timeout=3)
            if r.status_code == 200:
                print(f"[OK] 服务已启动 (PID={SERVER_PROC.pid})")
                return
        except:
            time.sleep(1)
    raise RuntimeError("服务启动超时")


def stop_server():
    global SERVER_PROC
    if SERVER_PROC:
        print("[INFO] 停止服务...")
        SERVER_PROC.terminate()
        SERVER_PROC.wait(timeout=5)
        SERVER_PROC = None


def upload_template(filepath: str) -> dict:
    """上传模板文件 → /upload-template"""
    with open(filepath, "rb") as f:
        resp = requests.post(f"{BASE_URL}/upload-template", files={"file": f})
    resp.raise_for_status()
    data = resp.json()
    print(f"[上传达模板] {data.get('filename','')} → path: {data.get('template_path','')}")
    return data


def upload_file(filepath: str) -> dict:
    """上传知识文件 → /upload"""
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        resp = requests.post(f"{BASE_URL}/upload", files={"files": (filename, f)})
    resp.raise_for_status()
    data = resp.json()
    fp = data.get("file_path", "")
    print(f"[上传知识] {filename} → path: {fp}")
    return data


def call_agent_stream(messages: list, timeout: int = 300) -> str:
    """
    调用 Agent (SSE流式) → /v1/chat/completions
    解析 SSE 事件，返回 Agent 最终回复文本。
    """
    payload = {
        "model": "agent",
        "messages": messages,
        "stream": True,
        "session_id": SESSION_ID,
    }
    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        json=payload,
        stream=True,
        timeout=timeout,
    )
    resp.raise_for_status()

    full_content = ""
    tool_calls = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break
        try:
            evt = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        # OpenAI 流式格式
        choices = evt.get("choices", [])
        for ch in choices:
            delta = ch.get("delta", {})
            if delta.get("content"):
                full_content += delta["content"]

            # 收集工具调用
            tc = delta.get("tool_calls")
            if tc:
                for t in tc:
                    tool_calls.append(t)

    return full_content


def get_download_url(session_id: str) -> Optional[str]:
    """查询生成的 docx 下载链接"""
    resp = requests.get(f"{BASE_URL}/generated-preview", params={"session_id": session_id}, timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        if data.get("success") and data.get("docx_url"):
            return data["docx_url"]
    return None


def download_docx(url: str, save_path: str):
    """下载生成的 docx 文件"""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(resp.content)
    print(f"[下载] docx 已保存到 {save_path} ({len(resp.content)} bytes)")
    return save_path


def extract_field_count_from_docx(filepath: str) -> dict:
    """从 docx 读取字段填写情况"""
    try:
        from docx import Document
        doc = Document(filepath)
        filled = 0
        empty = 0
        total = 0
        for para in doc.paragraphs:
            text = para.text.strip()
            if "{" in text and "}" in text:
                # 可能是残留占位符
                pass
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text and text != "" and not text.startswith("[") and not text.endswith("]"):
                        # 有内容
                        pass
        return {"filled": filled, "empty": empty, "total": total}
    except Exception as e:
        return {"error": str(e)}


def run_test(template_path: str, knowledge_paths: list, output_dir: str = "/tmp/e2e_results"):
    """执行完整的端到端测试"""
    os.makedirs(output_dir, exist_ok=True)

    # === 步骤 1: 上传模板 ===
    print("\n═══════════ 步骤1: 上传模板 ═══════════")
    tmpl = upload_template(template_path)
    template_path_on_server = tmpl.get("template_path", "")
    if not template_path_on_server:
        print("[FAIL] 模板上传失败")
        return False

    # === 步骤 2: Agent 分析模板 ===
    print("\n═══════════ 步骤2: Agent 分析模板 ═══════════")
    msg = (
        f"我上传了一个Word模板文件\"{os.path.basename(template_path)}\"，"
        f"模板路径：{template_path_on_server}。"
        f"请先调用 analyze_uploaded_template 工具分析这个模板路径，识别出所有需要填写的字段。"
    )
    t0 = time.time()
    reply = call_agent_stream([{"role": "user", "content": msg}])
    elapsed = time.time() - t0
    print(f"[Agent回复] ({elapsed:.0f}s) → {reply[:200]}...")

    # 检查 Agent 是否成功分析了模板
    if "analyze_uploaded_template" in reply or "字段" in reply or "field" in reply.lower():
        print("[OK] Agent 已分析模板")
    else:
        print("[WARN] Agent 回复中未确认模板分析")

    # === 步骤 3: 上传知识文件 ===
    print("\n═══════════ 步骤3: 上传知识文件 ═══════════")
    knowledge_paths_on_server = []
    for kp in knowledge_paths:
        result = upload_file(kp)
        fp = result.get("file_path", "")
        if fp:
            knowledge_paths_on_server.append(fp)

    if not knowledge_paths_on_server:
        print("[FAIL] 没有成功上传的知识文件")
        return False

    # === 步骤 4: Agent 预填 ===
    print("\n═══════════ 步骤4: Agent 解析知识文件并预填 ═══════════")
    paths_section = "\n".join(f"文件{i+1}: {p}" for i, p in enumerate(knowledge_paths_on_server))
    msg = (
        f"我已上传知识文件，请用这些路径调用工具来分析：\n{paths_section}\n"
        f"请用 prefill_from_knowledge 或 extract_from_old_report 工具提取字段值，"
        f"然后用 update_form_fields 批量填入。"
    )
    t0 = time.time()
    reply = call_agent_stream([{"role": "assistant", "content": reply}, {"role": "user", "content": msg}])
    elapsed = time.time() - t0
    print(f"[Agent回复] ({elapsed:.0f}s) → {reply[:300]}...")

    # === 步骤 5: 等待并检测生成结果 ===
    print("\n═══════════ 步骤5: 检测生成结果 ═══════════")
    # 如果 Agent 回复包含 [FIELDS] 或 [生成完成]，说明已生成
    if "[生成完成]" in reply or "generate_form" in reply or "generate_from_template" in reply:
        print("[OK] Agent 已生成文档")
    elif "已生成" in reply or "下载" in reply:
        print("[OK] Agent 回复包含生成确认")
    else:
        print("[WARN] Agent 可能尚未生成文档，尝试发送生成指令")

        # 步骤 5b: 如果还未生成，发送生成指令
        msg2 = "请调 generate_form_document 或 generate_from_template 生成文档，并返回下载链接。"
        t0 = time.time()
        reply2 = call_agent_stream([
            {"role": "assistant", "content": reply},
            {"role": "user", "content": msg2}
        ])
        elapsed = time.time() - t0
        print(f"[Agent生成回复] ({elapsed:.0f}s) → {reply2[:200]}...")
        reply = reply2

    # === 步骤 6: 下载 docx ===
    print("\n═══════════ 步骤6: 下载生成的docx ═══════════")
    # 尝试多种方式获取下载链接
    docx_url = get_download_url(SESSION_ID)
    if not docx_url:
        # 从 Agent 回复中提取 URL
        urls = re.findall(r'(https?://[^\s\)]+\.docx[^\s\)]*)', reply)
        if urls:
            docx_url = urls[0]

    if docx_url:
        save_path = os.path.join(output_dir, f"output_{int(time.time())}.docx")
        download_docx(docx_url, save_path)
        print(f"[OK] 文档已下载: {save_path}")
        return True
    else:
        print("[FAIL] 未能获取下载链接")
        return False


def main():
    parser = argparse.ArgumentParser(description="端到端全链路测试")
    parser.add_argument("--template", required=True, help="模板文件路径或内置模板名")
    parser.add_argument("--knowledge", nargs="+", required=True, help="知识文件路径(可多个)")
    parser.add_argument("--no-server", action="store_true", help="跳过启动服务器（使用已运行的服务）")
    args = parser.parse_args()

    if not args.no_server:
        atexit.register(stop_server)
        start_server()

    success = run_test(args.template, args.knowledge)

    if not args.no_server:
        stop_server()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()