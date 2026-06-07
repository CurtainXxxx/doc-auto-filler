"""
E2E 测试 - 完整用户链路（上传模板 + 上传知识 + SSE 流式 Agent + 下载 docx）
"""
import requests, json, sys, os, time

BASE_URL = "http://localhost:5000"
PROJECT_DIR = "/workspace/projects/projects"
TEMPLATE = "assets/templates/教材建设申报书.docx"
KNOWLEDGE = "assets/knowledge/教材建设申报/教材建设申报_【简单】.txt"
# TEMPLATE = "assets/templates/考场记录表.docx"
# KNOWLEDGE = "assets/knowledge/考场记录表/考场记录表_【简单】.txt"
SESSION_ID = f"e2e_test_{int(time.time())}"

# 检查服务
r = requests.get(f"{BASE_URL}/web/", timeout=5)
assert r.status_code == 200, f"服务未就绪: {r.status_code}"
print("[OK] 服务运行中")

# 1. 上传模板
tpath = os.path.join(PROJECT_DIR, TEMPLATE)
with open(tpath, "rb") as f:
    r = requests.post(f"{BASE_URL}/upload-template", files={"file": f})
r.raise_for_status()
tmpl_data = r.json()
template_path = tmpl_data["template_path"]
print(f"[1] 上传模板: {template_path}")

# 2. 上传知识文件
kpath = os.path.join(PROJECT_DIR, KNOWLEDGE)
if os.path.exists(kpath):
    with open(kpath, "rb") as f:
        r = requests.post(f"{BASE_URL}/upload", files={"files": ("knowledge.txt", f)})
    r.raise_for_status()
    knowledge_path = r.json().get("file_path", "")
    print(f"[2] 上传知识: {knowledge_path}")
else:
    print(f"[2] 无知识文件, 跳过")

# 3. 通过 SSE 发送消息
msg = f"请分析这个文档模板并填充：{template_path}"
print(f"[3] 发送消息: {msg}")
print("    等待 Agent 响应 (可能需要 2-5 分钟)...")

resp = requests.post(
    f"{BASE_URL}/v1/chat/completions",
    json={
        "model": "agent",
        "messages": [{"role": "user", "content": msg}],
        "stream": True,
        "session_id": SESSION_ID,
    },
    stream=True,
    timeout=600,
)
resp.raise_for_status()

# 读取 SSE 流
full_response = ""
for line in resp.iter_lines():
    if not line:
        continue
    decoded = line.decode("utf-8", errors="replace")
    if decoded.startswith("data: "):
        data_str = decoded[6:]
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            content = delta.get("content", "")
            if content and not delta.get("tool_calls"):
                sys.stdout.write(content)
                sys.stdout.flush()
                full_response += content
        except json.JSONDecodeError:
            pass

print(f"\n[4] Agent 响应长度: {len(full_response)} 字符")

# 5. 检查输出文件
time.sleep(3)
docx_path = os.path.join(PROJECT_DIR, "generated_output.docx")
if os.path.exists(docx_path):
    size = os.path.getsize(docx_path)
    print(f"[5] 生成文档: {docx_path} ({size} 字节)")

# 检查下载端点
r = requests.get(f"{BASE_URL}/download-docx", timeout=5)
if r.status_code == 200:
    print(f"[5] /download-docx ✅ 可下载 ({len(r.content)} 字节)")

print(f"\n✅ 测试完成, session_id: {SESSION_ID}")