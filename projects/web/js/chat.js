function autoResize(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 100) + 'px';
}

// ── New Chat ──
function startNewChat() {
  if (isStreaming) { alert('请等待当前对话完成后再新建对话。'); return; }
  if (!confirm('确定要开始新对话吗？当前对话记录将被清除。')) return;
  sessionId = newSessionId();
  localStorage.setItem(STORAGE_KEY, sessionId);
  localStorage.removeItem('chat_session_id');
  resetDocumentState();
  document.getElementById('chatMessages').innerHTML = '';
  document.querySelector('.app').classList.remove('cofill-mode');
  document.querySelectorAll('.func-btn').forEach(button => button.classList.remove('active'));
  const progress = document.getElementById('headerProgress');
  if (progress) progress.style.display = 'none';
  document.getElementById('previewActions').style.display = 'none';
  updateStepBar();
  updatePreview('empty');
  appendMessage('assistant', '已开始新对话。请上传教务模板或知识文件开始使用。');
}

// ── Send Message ──
async function sendMessage(text) {
  if (!API_URL) { alert('请通过部署后的 Web 服务 URL 访问。'); return; }
  const input = document.getElementById('userInput');
  const msg = text || input.value.trim();
  if (!msg || isStreaming) return;
  if (!text) { input.value = ''; input.style.height = 'auto'; }
  appendMessage('user', msg);

  // 检测用户消息中的模板关键词，立即加载预览
  const tplKeywords = ['评价报告', '试卷分析', '关联矩阵'];
  for (const kw of tplKeywords) {
    if (msg.includes(kw) && currentTemplate !== kw) {
      currentTemplate = kw;
      currentTemplatePath = kw;
      fetchDocPreview(kw);
      break;
    }
  }

  await callAgent(msg);
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

// ── Append Message ──
function appendMessage(role, content, extra) {
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  const icon = role === 'assistant' ? '文' : '我';
  let bubbleHtml = escapeHtml(content);
  if (extra) bubbleHtml += extra;
  div.innerHTML = `<div class="avatar-icon">${icon}</div><div class="bubble">${bubbleHtml}</div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

function createStreamingMessage() {
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `<div class="avatar-icon">文</div><div class="bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

// ── Call Agent ──
// P0-3: 思考动画
let thinkingEl = null;
function showThinkingIndicator(show) {
  const chat = document.getElementById('chatMessages');
  if (show) {
    if (!thinkingEl) {
      thinkingEl = document.createElement('div');
      thinkingEl.className = 'thinking-indicator';
      thinkingEl.innerHTML = '<span>思考中</span><span class="thinking-dots"><span></span><span></span><span></span></span>';
      thinkingEl.id = 'thinkingIndicator';
    }
    if (!document.getElementById('thinkingIndicator')) {
      chat.appendChild(thinkingEl);
    }
    chat.scrollTop = chat.scrollHeight;
  } else {
    const el = document.getElementById('thinkingIndicator');
    if (el) el.remove();
    thinkingEl = null;
  }
}

// P0-4: 错误重试
function addRetryButton(msgDiv, userText) {
  const button = document.createElement('button');
  button.className = 'retry-btn';
  button.textContent = '重试';
  button.onclick = () => {
    if (isStreaming) return;
    button.remove();
    callAgent(userText);
  };
  msgDiv.appendChild(button);
}

async function callAgent(userText) {
  isStreaming = true;
  document.getElementById('sendBtn').disabled = true;

  const msgDiv = createStreamingMessage();
  const bubble = msgDiv.querySelector('.bubble');

  showThinkingIndicator(true);

  // ── 超时体系 ──
  // 30s 静默检测：代理可能因空闲而断开连接，30s 无数据即判定断开
  // 120s 硬超时：兜底，Agent 处理时间过长
  const controller = new AbortController();
  let lastDataTime = Date.now();
  let toolCallCount = 0;
  let fullText = '';
  let hasPreciseValues = false;
  let streamDone = false;
  let aborted = false;

  function doAbort(reason) {
    if (aborted) return;
    aborted = true;
    controller.abort();
    showThinkingIndicator(false);
    bubble.innerHTML = `<span style="color:var(--danger);">${escapeHtml(reason)}</span>`;
    addRetryButton(msgDiv, userText);
  }

  // 静默检测：每 5 秒检查，30 秒无新数据 → 判定代理断开
  const SILENCE_MS = 90000;
  const silenceTimer = setInterval(() => {
    if (aborted || streamDone) return;
    const elapsed = Date.now() - lastDataTime;
    if (elapsed > SILENCE_MS) {
      doAbort(`连接已断开（${SILENCE_MS/1000}秒无响应），请重试`);
      clearInterval(silenceTimer);
    } else if (elapsed > 15000 && !fullText) {
      // 15 秒无内容 → 进度提示
      const hint = toolCallCount > 0
        ? `Agent 正在调用工具处理中（已调用 ${toolCallCount} 次），预计还需 30-60 秒...`
        : 'Agent 正在思考中，请稍候...';
      bubble.innerHTML = `<span style="color:#888;font-style:italic;">${hint}</span>`;
      document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
    }
  }, 5000);

  // 120s 硬超时兜底
  const HARD_TIMEOUT_MS = 120000;
  const hardTimeoutId = setTimeout(() => {
    doAbort(`请求超时（${HARD_TIMEOUT_MS/1000}秒），Agent 处理时间过长，请重试或简化输入`);
    clearInterval(silenceTimer);
  }, HARD_TIMEOUT_MS);

  try {
    const resp = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'agent',
        messages: [{ role: 'user', content: userText }],
        stream: true,
        session_id: sessionId,
      }),
      signal: controller.signal,
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let lastFieldCheckLen = 0;
    let lastFieldLineLen = 0;

    while (!streamDone) {
      const { done, value } = await reader.read();
      if (done) break;

      lastDataTime = Date.now(); // 收到任何数据都重置静默计时
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') { streamDone = true; break; }
        try {
          const json = JSON.parse(data);
          if (json.error) {
            fullText += '\n请求失败：' + (json.error.message || '未知错误');
            bubble.innerHTML = renderMarkdown(fullText);
            streamDone = true;
            break;
          }
          const delta = json.choices?.[0]?.delta;
          if (!delta) continue;

          if (delta.role === 'tool' && delta.content) {
            toolCallCount++;
            lastDataTime = Date.now();
            if (!fullText) {
              bubble.innerHTML = `<span style="color:#888;font-style:italic;">Agent 正在调用工具处理中（第 ${toolCallCount} 次）...</span>`;
              document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
            }
            try {
              const toolResult = JSON.parse(delta.content);
              console.log('[preview] tool result keys:', Object.keys(toolResult));
              if (toolResult.filled_field_values && typeof toolResult.filled_field_values === 'object') {
                console.log('[preview] applying filled_field_values:', Object.keys(toolResult.filled_field_values).length, 'fields');
                applyPreciseFieldValues(toolResult.filled_field_values);
                hasPreciseValues = true;
              }
              else if (toolResult.filled_data && typeof toolResult.filled_data === 'object') {
                console.log('[preview] applying filled_data:', Object.keys(toolResult.filled_data).length, 'fields');
                applyFilledData(toolResult.filled_data);
              }
              if (toolResult.local_path) {
                currentDocxPath = toolResult.local_path;
                refreshPreviewFromGeneratedDoc(toolResult.local_path);
              }
              // 生成文档成功：显示下载链接
              if (toolResult.download_url && toolResult.success) {
                currentDownloadUrl = toolResult.download_url;
                if (!fullText) {
                  bubble.innerHTML = `<div style="margin:8px 0;">
                    <strong>✅ 文档已生成！</strong><br>
                    <a href="${toolResult.download_url}" target="_blank" style="color:#4CAF50;font-weight:bold;font-size:16px;">
                      📥 点击下载生成的 Word 文档
                    </a>
                  </div>`;
                } else {
                  fullText += `\n\n✅ 文档已生成！\n下载链接：${toolResult.download_url}\n[生成完成]`;
                }
              }
            } catch(e) { console.warn('[preview] tool result parse error:', e); }
            continue;
          }

          if (delta.content) {
            fullText += delta.content;
            lastDataTime = Date.now();

            if (fullText.includes('[FIELDS]')) {
              const afterFields = fullText.substring(fullText.indexOf('[FIELDS]') + 8);
              const beforeClose = afterFields.includes('[/FIELDS]') ? afterFields.indexOf('[/FIELDS]') : afterFields.length;
              const fieldsText = afterFields.substring(0, beforeClose);
              if (fieldsText.length > lastFieldLineLen) {
                lastFieldLineLen = fieldsText.length;
                parseFieldsLines(fieldsText);
              }
            }

            const displayText = fullText.replace(/\[FIELDS\][\s\S]*?\[\/FIELDS\]/g, '').trim();
            bubble.innerHTML = renderMarkdown(displayText);
            document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;

            if (fullText.length - lastFieldCheckLen > 60) {
              lastFieldCheckLen = fullText.length;
              if (!hasPreciseValues) detectAndUpdateFields(fullText);
            }
          }
        } catch (e) {}
      }
    }

    if (!hasPreciseValues) detectAndUpdateFields(fullText);
    postProcessResponse(fullText);
    showThinkingIndicator(false);
    clearTimeout(hardTimeoutId);
    clearInterval(silenceTimer);

  } catch (err) {
    if (err.name !== 'AbortError') {
      bubble.innerHTML = `<span style="color:var(--danger);">请求失败: ${escapeHtml(err.message)}</span>`;
      addRetryButton(msgDiv, userText);
      showThinkingIndicator(false);
    }
    clearTimeout(hardTimeoutId);
    clearInterval(silenceTimer);
  } finally {
    isStreaming = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('userInput').focus();
  }
}
