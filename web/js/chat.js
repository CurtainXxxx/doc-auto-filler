import { state, escapeHtml, renderMarkdown } from './helpers.js';

// ── Send Message ──
// ── Send Message ──
export async function sendMessage(text) {
  if (!state.API_URL) { alert('请通过部署后的 Web 服务 URL 访问。'); return; }
  const input = document.getElementById('userInput');
  const msg = text || input.value.trim();
  if (!msg || state.isStreaming) return;
  if (!text) { input.value = ''; input.style.height = 'auto'; }
  appendMessage('user', msg);

  // 检测用户消息中的模板关键词，立即加载预览
  const tplKeywords = ['评价报告', '试卷分析', '关联矩阵'];
  for (const kw of tplKeywords) {
    if (msg.includes(kw) && state.currentTemplate !== kw) {
      state.currentTemplate = kw;
      state.currentTemplatePath = kw;
      fetchDocPreview(kw);
      break;
    }
  }

  await callAgent(msg);
}

// ── Input Helpers ──
export function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

// ── Append Message ──
// ── Append Message ──
export function appendMessage(role, content, extra) {
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

export function createStreamingMessage() {
  const container = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `<div class="avatar-icon">文</div><div class="bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>`;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

// ── Call Agent (Streaming) ──
// ── Call Agent ──
export async function callAgent(userText) {
  state.isStreaming = true;
  document.getElementById('sendBtn').disabled = true;

  const msgDiv = createStreamingMessage();
  const bubble = msgDiv.querySelector('.bubble');

  try {
    const resp = await fetch(state.API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'agent',
        messages: [{ role: 'user', content: userText }],
        stream: true,
        session_id: state.sessionId,
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let fullText = '';
    let buffer = '';
    let lastFieldCheckLen = 0;
    let lastFieldLineLen = 0;    // 上次解析 [FIELDS] 行时的文本长度

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') break;
        try {
          const json = JSON.parse(data);
          const delta = json.choices?.[0]?.delta;
          if (!delta) continue;

          if (delta.role === 'tool' && delta.content) {
            // 解析工具结果，优先使用精确 field_id 回填，其次再用 label 回填
            try {
              const toolResult = JSON.parse(delta.content);
              console.log('[preview] tool result keys:', Object.keys(toolResult));
              if (toolResult.filled_field_values && typeof toolResult.filled_field_values === 'object') {
                console.log('[preview] applying filled_field_values:', Object.keys(toolResult.filled_field_values).length, 'fields');
                applyPreciseFieldValues(toolResult.filled_field_values);
              }
              if (toolResult.filled_data && typeof toolResult.filled_data === 'object') {
                console.log('[preview] applying filled_data:', Object.keys(toolResult.filled_data).length, 'fields');
                applyFilledData(toolResult.filled_data);
              }
              if (toolResult.local_path) {
                state.currentDocxPath = toolResult.local_path;
              }
              // 工具返回后刷新预览文档（重新获取带值预览）
              if (toolResult.local_path) {
                refreshPreviewFromGeneratedDoc(toolResult.local_path);
              }
            } catch(e) { console.warn('[preview] tool result parse error:', e); }
            continue;
          }

          if (delta.content) {
            fullText += delta.content;

            // 实时增量解析 [FIELDS] 块：每收到一行 key=value 立即更新预览
            if (fullText.includes('[FIELDS]')) {
              const afterFields = fullText.substring(fullText.indexOf('[FIELDS]') + 8);
              const beforeClose = afterFields.includes('[/FIELDS]') ? afterFields.indexOf('[/FIELDS]') : afterFields.length;
              const fieldsText = afterFields.substring(0, beforeClose);
              if (fieldsText.length > lastFieldLineLen) {
                lastFieldLineLen = fieldsText.length;
                parseFieldsLines(fieldsText);
              }
            }

            // 渲染时隐藏 [FIELDS]...[/FIELDS] 块
            const displayText = fullText.replace(/\[FIELDS\][\s\S]*?\[\/FIELDS\]/g, '').trim();
            bubble.innerHTML = renderMarkdown(displayText);
            document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;

            // 定期也检测非 FIELDS 格式的键值对
            if (fullText.length - lastFieldCheckLen > 60) {
              lastFieldCheckLen = fullText.length;
              detectAndUpdateFields(fullText);
            }
          }
        } catch (e) {}
      }
    }

    // 流结束后最终检测
    detectAndUpdateFields(fullText);
    postProcessResponse(fullText);

  } catch (err) {
    bubble.innerHTML = `<span style="color:var(--danger);">请求失败: ${escapeHtml(err.message)}</span>`;
  } finally {
    state.isStreaming = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('userInput').focus();
  }
}

// ── Field Parsing & Detection ──
// ── Incrementally parse [FIELDS] block lines and update preview ──
export function parseFieldsLines(fieldsText) {
  const lines = fieldsText.split('\n');
  for (const line of lines) {
    const eqIdx = line.indexOf('=');
    if (eqIdx > 0) {
      const key = line.substring(0, eqIdx).trim();
      const val = line.substring(eqIdx + 1).trim();
      if (key && val) {
        // 优先用 field_id 精确匹配
        if (state.fieldMap[key]) {
          updateFieldById(key, val);
        } else {
          updateFieldByLabel(key, val);
        }
      }
    }
  }
}

// ── Refresh preview from a generated docx (update cell values in existing preview) ──
export async function refreshPreviewFromGeneratedDoc(localPath) {
  // 从生成的 docx 获取填写后的数据，更新现有预览中的单元格值
  // 不替换整个 HTML，保留 editable 结构和 data-field-id 属性
  try {
    const res = await fetch('/generated-preview?local_path=' + encodeURIComponent(localPath));
    const data = await res.json();
    if (data.success) {
      // 使用 field_map 中的 existing_value 精确更新每个已填单元格
      if (data.field_map) {
        for (const [fieldId, info] of Object.entries(data.field_map)) {
          const existingValue = info.existing_value;
          if (existingValue && String(existingValue).trim()) {
            // 查找当前预览中的对应元素并更新
            const els = document.querySelectorAll('[data-field-id="' + CSS.escape(fieldId) + '"]');
            for (const el of els) {
              if (el.classList.contains('cell-value')) {
                el.textContent = existingValue;
                el.classList.add('filled');
                el.classList.remove('empty');
              } else if (el.classList.contains('editable')) {
                el.textContent = existingValue;
                el.classList.remove('empty');
                el.classList.add('filled');
              }
            }
          }
        }
        // 注意：填充后文档的 field_map 可能不完整（已填值的单元格不再被识别为可填字段），
        // 因此不能直接用 Object.assign 覆盖 fieldMap，只更新已有条目的 existing_value
        for (const [fid, info] of Object.entries(data.field_map)) {
          if (state.fieldMap[fid]) {
            state.fieldMap[fid].existing_value = info.existing_value || '';
          }
        }
      }
      // label_to_fields 同样不覆盖，保留原始模板的完整映射
      console.log('[preview] refreshPreviewFromGeneratedDoc: updated field values from generated doc');
    }
  } catch(e) {
    console.warn('[preview] refreshPreviewFromGeneratedDoc failed:', e);
  }
}

// ── Apply precise field_id -> value result from tool ──
export function applyPreciseFieldValues(fieldValues) {
  if (!fieldValues || typeof fieldValues !== 'object') return;
  let updatedCount = 0;
  for (const [fieldId, value] of Object.entries(fieldValues)) {
    if (value && String(value).trim()) {
      const strVal = String(value);
      // 1. 先用 field_id 精确匹配
      if (state.fieldMap[fieldId]) {
        updateFieldById(fieldId, strVal);
        updatedCount++;
      } else {
        // 2. field_id 不在 state.fieldMap 中，直接尝试 DOM 查找
        updateCellInPreview(fieldId, strVal);
        updatedCount++;
      }
    }
  }
  console.log('[preview] applyPreciseFieldValues: updated', updatedCount, '/', Object.keys(fieldValues).length, 'fields');
}

// ── Apply label -> value result from tool (fallback) ──
export function applyFilledData(filledData) {
  if (!filledData || typeof filledData !== 'object') return;
  for (const [label, value] of Object.entries(filledData)) {
    if (value && String(value).trim()) {
      updateFieldByLabel(label, String(value));
    }
  }
}

// ── Detect field values from agent text & update document preview ──
export function detectAndUpdateFields(text) {
  // 1. 优先解析 [FIELDS]...[/FIELDS] 格式（完整块）
  const fieldsBlock = text.match(/\[FIELDS\]([\s\S]*?)\[\/FIELDS\]/);
  if (fieldsBlock) {
    parseFieldsLines(fieldsBlock[1]);
  } else if (text.includes('[FIELDS]')) {
    // 增量：[FIELDS] 已开始但 [/FIELDS] 还没到
    const afterFields = text.substring(text.indexOf('[FIELDS]') + 8);
    parseFieldsLines(afterFields);
  }

  // 2. 去掉 markdown 加粗标记和 [FIELDS] 块
  const clean = text.replace(/\*\*/g, '').replace(/\[FIELDS\][\s\S]*?\[\/FIELDS\]/g, '');

  // 3. 匹配多种格式：
  // "字段：值" 或 "字段: 值" 或 "- 字段：值" 或 "1. 字段：值"
  const kvMatches = clean.matchAll(/(?:^|\n|[-–·•]\s*|\d+[.、)\]]\s*)([^\n：:]{1,30})[：:]\s*([^\n]+)/g);
  for (const m of kvMatches) {
    const key = m[1].trim().replace(/^[\s\-–·•\d.、)\]]+/,'').trim();
    const val = m[2].trim();
    if (key && val && key.length >= 2 && key.length < 20 && val.length < 200) {
      updateFieldByLabel(key, val);
    }
  }

  // 4. 匹配 key=value 格式
  const eqMatches = clean.matchAll(/(\S{2,15})\s*[=＝]\s*([^,，\n]+)/g);
  for (const m of eqMatches) {
    const key = m[1].trim();
    const val = m[2].trim();
    if (key && val && val.length < 200) {
      updateFieldByLabel(key, val);
    }
  }
}

// ── Update Field by Label ──
// ── Update a field in the document preview by label name ──
export function updateFieldByLabel(label, value) {
  const normalizedLabel = label.replace(/\s+/g, '');

  // 1. 精确匹配
  let fids = state.labelToFields[label];
  if (!fids || fids.length === 0) {
    for (const [key, ids] of Object.entries(state.labelToFields)) {
      if (key.replace(/\s+/g, '') === normalizedLabel) {
        fids = ids;
        break;
      }
    }
  }
  if (fids && fids.length > 0) {
    for (const fid of fids) {
      updateFieldById(fid, value);
    }
    return;
  }

  // 2. 前缀匹配：如 label="课程目标1"，匹配 "课程目标1_1", "课程目标1_2" 等
  // 值可能是逗号分隔的多个子字段值
  const prefixKey = label + '_';
  const subFids = [];
  for (const [k, v] of Object.entries(state.labelToFields)) {
    if (k.startsWith(prefixKey)) {
      subFids.push(...v);
    }
  }
  if (subFids.length > 0) {
    // 按字段名排序（数字顺序）
    const subLabels = Object.entries(state.labelToFields)
      .filter(([k]) => k.startsWith(prefixKey))
      .sort((a, b) => {
        const na = parseInt(a[0].replace(prefixKey, '').replace('第','').replace('列',''));
        const nb = parseInt(b[0].replace(prefixKey, '').replace('第','').replace('列',''));
        return (isNaN(na) ? 999 : na) - (isNaN(nb) ? 999 : nb);
      });
    
    // 拆分值：支持逗号、中文逗号、空格分隔
    const values = value.split(/[,，\s]+/).filter(v => v.trim());
    
    for (let i = 0; i < subLabels.length; i++) {
      const val = i < values.length ? values[i].trim() : '';
      for (const fid of subLabels[i][1]) {
        updateFieldById(fid, val);
      }
    }
    return;
  }
}

// ── Update Field by ID ──
// ── Update a field in the document preview by exact field_id ──
export function updateFieldById(fieldId, value) {
  state.fieldData[fieldId] = value;
  updateCellInPreview(fieldId, value);
}

// ── Update Cell in Preview ──
// ── Update a cell in the document preview by field_id ──
export function updateCellInPreview(fieldId, value) {
  // 查找带 data-field-id 的元素
  const els = document.querySelectorAll(`[data-field-id="${CSS.escape(fieldId)}"]`);
  for (const el of els) {
    // 如果用户正在编辑这个元素，跳过
    if (document.activeElement === el) continue;

    if (el.classList.contains('cell-value')) {
      // 冒号字段的可编辑 span —— 直接设置文本
      el.textContent = value;
      el.classList.toggle('filled', !!value.trim());
    } else if (el.classList.contains('editable')) {
      // 整格可编辑的 td 直接更新可见文本，避免预览层二次猜测 Word 内部结构
      el.textContent = value;
      el.classList.remove('empty');
      el.classList.toggle('filled', !!value.trim());
    }

    // 高亮动画：字段被填充时闪烁
    if (value.trim()) {
      el.classList.remove('field-flash');
      // 触发 reflow 以重新启动动画
      void el.offsetWidth;
      el.classList.add('field-flash');
    }
  }
  // 更新进度条
  refreshProgressBar();
}

// ── Post-process Response ──
// ── Post-process Response ──
export function postProcessResponse(text) {
  const urlMatch = text.match(/https?:\/\/[^\s"<>]+\.docx[^\s"<>]*/);
  if (urlMatch) {
    state.currentDownloadUrl = urlMatch[0];
    state.currentStep = 5; updateStepBar();
    updatePreview('done');
    document.getElementById('previewActions').style.display = 'flex';
    return;
  }

  if (text.includes('评价报告') && (text.includes('试卷分析') || text.includes('关联矩阵')) && state.currentStep === 0) {
    state.currentStep = 1; updatePreview('template-select');
  }

  // 检测模板选择，自动加载文档预览
  const tplKeywords = ['评价报告', '试卷分析', '关联矩阵'];
  for (const kw of tplKeywords) {
    if (text.includes(kw) && state.currentTemplate !== kw) {
      state.currentTemplate = kw;
      state.currentTemplatePath = kw;
      fetchDocPreview(kw);  // 自动加载右侧文档预览
      break;
    }
  }
  if ((text.includes('评价报告') || text.includes('试卷分析') || text.includes('关联矩阵')) && state.currentStep === 1) {
    const tplMatch = text.match(/(评价报告|试卷分析|关联矩阵)/);
    if (tplMatch) state.currentTemplate = tplMatch[1];
  }

  if (text.includes('识别到') && text.includes('字段') && state.currentStep === 0) {
    state.currentStep = 1; updateStepBar();
    const tplFileMatch = text.match(/模板[：:]?\s*(\S+\.docx?)/);
    if (tplFileMatch) state.currentTemplate = tplFileMatch[1];
  }

  if (text.includes('请填写') || text.includes('请提供') || text.includes('请输入')) {
    if (state.currentStep < 2) state.currentStep = 2;
    updatePreview('collecting');
  }

  if (text.includes('核对') || text.includes('确认')) {
    if (state.currentStep < 3) state.currentStep = 3;
    updatePreview('confirm');
  }

  if (text.includes('正在') && (text.includes('生成') || text.includes('渲染'))) {
    state.currentStep = 4; updatePreview('generating');
  }

  if (text.includes('缺少') || text.includes('缺失') || text.includes('未提供')) {
    highlightUploadBtn();
  }

  updateStepBar();
}

// ── Update Step Bar ──
export function updateStepBar() {
  const bar = document.getElementById('stepsBar');
  const items = bar.querySelectorAll('.step');
  items.forEach((el, i) => {
    el.className = 'step';
    if (i < state.currentStep) el.classList.add('done');
    else if (i === state.currentStep) el.classList.add('active');
  });
}
