// ── Incrementally parse [FIELDS] block lines and update preview ──
function parseFieldsLines(fieldsText) {
  const lines = fieldsText.split('\n');
  for (const line of lines) {
    const eqIdx = line.indexOf('=');
    if (eqIdx > 0) {
      const key = line.substring(0, eqIdx).trim();
      const val = line.substring(eqIdx + 1).trim();
      if (key && val) {
        // 优先用 field_id 精确匹配
        if (fieldMap[key]) {
          updateFieldById(key, val);
        } else {
          updateFieldByLabel(key, val);
        }
      }
    }
  }
}

// ── Refresh preview from a generated docx (update cell values in existing preview) ──
async function refreshPreviewFromGeneratedDoc(localPath) {
  const requestId = previewRequestId;
  const documentPath = currentDocxPath;
  try {
    const response = await fetch('/generated-preview?local_path=' + encodeURIComponent(localPath));
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (!result.success || requestId !== previewRequestId || documentPath !== currentDocxPath) return;
    // Generated documents may no longer expose already-filled cells as fields.
    // Retain the original field map and update only values actually returned.
    const values = {};
    for (const [id, info] of Object.entries(result.field_map || {})) {
      if (hasFieldValue(info.existing_value)) values[id] = info.existing_value;
      if (fieldMap[id]) fieldMap[id].existing_value = fieldText(info.existing_value);
    }
    applyPreciseFieldValues(values);
  } catch (error) {
    console.warn('Generated preview refresh failed:', error);
  }
}

// ── Apply precise field_id -> value result from tool ──
function applyPreciseFieldValues(fieldValues) {
  if (!fieldValues || typeof fieldValues !== 'object' || Array.isArray(fieldValues)) return;
  for (const [fieldId, value] of Object.entries(fieldValues)) {
    updateFieldById(fieldId, value, false);
  }
  refreshProgressBar();
}

// ── Apply label -> value result from tool (fallback) ──
function applyFilledData(filledData) {
  if (!filledData || typeof filledData !== 'object') return;
  for (const [label, value] of Object.entries(filledData)) {
    updateFieldByLabel(label, fieldText(value));
  }
}

// ── Detect field values from agent text & update document preview ──
function detectAndUpdateFields(text) {
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

// ── Update a field in the document preview by label name ──
function updateFieldByLabel(label, value) {
  const normalizedLabel = label.replace(/\s+/g, '');

  // 1. 精确匹配
  let fids = labelToFields[label];
  if (!fids || fids.length === 0) {
    for (const [key, ids] of Object.entries(labelToFields)) {
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
  for (const [k, v] of Object.entries(labelToFields)) {
    if (k.startsWith(prefixKey)) {
      subFids.push(...v);
    }
  }
  if (subFids.length > 0) {
    // 按字段名排序（数字顺序）
    const subLabels = Object.entries(labelToFields)
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

// ── Update a field in the document preview by exact field_id ──
function updateFieldById(fieldId, value, refresh = true) {
  fieldData[fieldId] = fieldText(value);
  updateCellInPreview(fieldId, fieldData[fieldId], refresh);
}

// ── Update a cell in the document preview by field_id ──
function updateCellInPreview(fieldId, value, refresh = true) {
  value = fieldText(value);
  const filled = hasFieldValue(value);
  const elements = document.querySelectorAll(`[data-field-id="${CSS.escape(fieldId)}"]`);
  for (const element of elements) {
    if (document.activeElement === element) continue;
    if (!element.classList.contains('cell-value') && !element.classList.contains('editable')) continue;
    element.textContent = value;
    element.classList.toggle('filled', filled);
    element.classList.toggle('empty', !filled);
    if (filled) {
      element.classList.remove('field-flash');
      void element.offsetWidth;
      element.classList.add('field-flash');
    }
  }
  if (refresh) refreshProgressBar();
}

// ── Post-process Response ──
function postProcessResponse(text) {
  // P0-1: 检测 Agent 身份标签并更新状态栏
  const agentTags = [
    { tag: '[知识提取Agent]', label: '知识提取 Agent', desc: '正在从文件中提取关键信息...', step: 0 },
    { tag: '[填充Agent]', label: '字段填充 Agent', desc: '正在将信息匹配到模板字段...', step: 1 },
    { tag: '[生成Agent]', label: '文档生成 Agent', desc: '正在生成 Word 文档...', step: 3 }
  ];
  for (const at of agentTags) {
    if (text.includes(at.tag)) {
      updateAgentStatus(at.label, at.desc, 'active');
      break;
    }
  }

  // P0-2: 检测阶段完成标记（与 Router 架构对齐）
  if (text.includes('[知识提取完成]')) {
    currentStep = Math.max(currentStep, 1);
    updateAgentStatus('知识提取 Agent', '事实提取完成', 'done');
    updateStepBar();
  }
  if (text.includes('[填充完成]')) {
    currentStep = Math.max(currentStep, 2);
    updateAgentStatus('字段填充 Agent', '字段匹配完成', 'done');
    updateStepBar();
  }
  if (text.includes('[生成完成]')) {
    currentStep = Math.max(currentStep, 4);
    updateAgentStatus('文档生成 Agent', '文档已生成', 'done');
    updateStepBar();
  }

  // 检测 [FACTS] 标记 — 知识提取完成
  if (text.includes('[FACTS]') && text.includes('[/FACTS]')) {
    if (currentStep < 1) currentStep = 1;
    updateAgentStatus('知识提取 Agent', '事实提取完成', 'done');
    updateStepBar();
  }

  // 检测 [FIELDS] 标记 — 字段填充中
  if (text.includes('[FIELDS]') && text.includes('[/FIELDS]')) {
    if (currentStep < 2) currentStep = 2;
    updateStepBar();
  }

  // 下载链接检测
  const urlMatch = text.match(/(?:https?:\/\/[^\s"<>]+\.docx[^\s"<>]*|\/download-docx\?[^\s"<>）)]+)/);
  if (urlMatch) {
    currentDownloadUrl = urlMatch[0];
    currentStep = 5; updateStepBar();
    updatePreview('done');
    document.getElementById('previewActions').style.display = 'flex';
    updateAgentStatus('', '文档已生成，可下载', 'done');
    return;
  }

  // 兼容旧版检测（保留作为兜底）
  if (text.includes('识别到') && text.includes('字段') && currentStep === 0) {
    currentStep = 1; updateStepBar();
    const tplFileMatch = text.match(/模板[：:]?\s*(\S+\.docx?)/);
    if (tplFileMatch) currentTemplate = tplFileMatch[1];
  }

  if (text.includes('正在') && (text.includes('生成') || text.includes('渲染'))) {
    if (currentStep < 4) currentStep = 4;
    updatePreview('generating');
  }

  if (text.includes('缺少') || text.includes('缺失') || text.includes('未提供')) {
    highlightUploadBtn();
  }

  // 模板检测（扩展关键词）
  const tplKeywords = ['评价报告', '试卷分析', '关联矩阵', '考场记录表', '教材建设申报书', '教务数据申请'];
  for (const kw of tplKeywords) {
    if (text.includes(kw) && currentTemplate !== kw) {
      currentTemplate = kw;
      currentTemplatePath = kw;
      fetchDocPreview(kw);
      break;
    }
  }

  updateStepBar();
}

// P0-1: Agent 状态栏更新
function updateAgentStatus(label, desc, state) {
  const bar = document.getElementById('agentStatusBar');
  const dot = document.getElementById('agentDot');
  const labelEl = document.getElementById('agentLabel');
  const descEl = document.getElementById('agentDesc');
  if (!bar) return;

  bar.style.display = 'flex';
  dot.className = 'agent-dot ' + state;
  labelEl.className = 'agent-label ' + state;
  labelEl.textContent = label || '';
  descEl.textContent = desc || '';

  if (state === 'done') {
    setTimeout(() => { if (bar) bar.style.display = 'none'; }, 4000);
  }
}


function updateStepBar() {
  const bar = document.getElementById('stepsBar');
  const items = bar.querySelectorAll('.step');
  items.forEach((el, i) => {
    el.className = 'step';
    if (i < currentStep) el.classList.add('done');
    else if (i === currentStep) el.classList.add('active');
  });
}

// ── Fetch document preview from backend ──
async function fetchDocPreview(templatePath) {
  if (!BASE_URL) {
    console.warn('fetchDocPreview: BASE_URL not set, skipping');
    return;
  }
  const requestId = ++previewRequestId;
  try {
    const url = `${BASE_URL}/template-preview?path=${encodeURIComponent(templatePath)}`;
    console.log('fetchDocPreview: fetching', url);
    const resp = await fetch(url);
    if (!resp.ok) {
      console.warn('fetchDocPreview: HTTP', resp.status, await resp.text());
      return;
    }
    const result = await resp.json();
    console.log('fetchDocPreview: result success=', result.success, 'html_len=', (result.html||'').length);
    if (requestId !== previewRequestId) return;
    if (result.success) {
      if (previewTemplatePath !== templatePath) {
        fieldData = {};
        manuallyEditedFields = {};
        prefillData = null;
        currentDownloadUrl = currentDocxPath = '';
      }
      previewTemplatePath = templatePath;
      docPreviewHtml = result.html;
      fieldMap = result.field_map || {};
      labelToFields = result.label_to_fields || {};
      renderDocPreview();
    }
  } catch (e) {
    console.error('fetchDocPreview failed:', e);
  }
}

// ── Render document preview ──
function renderDocPreview() {
  const body = document.getElementById('previewBody');
  if (!docPreviewHtml) return;

  // 计算填写进度
  const {total: totalFields, filled: filledCount, percent: pct} = previewProgress();

  // 渲染顶部进度条
  const headerProgress = document.getElementById('headerProgress');
  if (headerProgress && totalFields > 0) {
    headerProgress.innerHTML = `<div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="bar-text">${filledCount}/${totalFields} 已填写</span>`;
    headerProgress.style.display = 'flex';
  }

  let html = `<div class="doc-page">`;

  // Document HTML
  html += docPreviewHtml;

  // Submit button
  html += `<button class="submit-edits-btn" id="submitEditsBtn" onclick="submitManualEdits()" ${isStreaming ? 'disabled' : ''}>提交手动填写</button>`;
  html += `</div>`;

  body.innerHTML = html;

  // 同步更新协同模式的预览
  if (prefillMode) {
    const cofillContainer = document.getElementById('cofillDocContainer');
    if (cofillContainer) cofillContainer.innerHTML = html;
  }
  previewRendered = true;
  refreshFieldValues();
}

// ── Refresh progress bar only (no cell updates, no recursion) ──
function refreshProgressBar() {
  const {total, filled, percent} = previewProgress();
  const barFill = document.querySelector('#headerProgress .bar-fill');
  const barText = document.querySelector('#headerProgress .bar-text');
  if (barFill) barFill.style.width = percent + '%';
  if (barText) barText.textContent = `${filled}/${total} 已填写`;
}

// ── Refresh field values without re-rendering the whole document ──
function refreshFieldValues() {
  for (const [id, value] of Object.entries(fieldData)) updateCellInPreview(id, value, false);
  refreshProgressBar();
}

// ── Update Preview state ──
function updatePreview(state) {
  const body = document.getElementById('previewBody');
  const title = document.getElementById('previewTitle');

  switch(state) {
    case 'template-select':
      title.textContent = '选择模板';
      // 如果已加载文档预览，不要覆盖
      if (docPreviewHtml) {
        refreshFieldValues();
        break;
      }
      body.innerHTML = `
        <div class="doc-page" style="max-width:520px;padding:36px;">
          <h2>可选教务文档模板</h2>
          <table class="template-table">
            <tr><th style="width:50px;"></th><th>模板名称</th><th style="width:200px;">说明</th></tr>
            <tr><td style="color:var(--text-muted);">1</td><td><span class="tpl-link" onclick="selectTemplate('评价报告')">评价报告</span></td><td style="color:var(--text-secondary);">专业课程目标达成度评价报告</td></tr>
            <tr><td style="color:var(--text-muted);">2</td><td><span class="tpl-link" onclick="selectTemplate('试卷分析')">试卷分析</span></td><td style="color:var(--text-secondary);">试卷分析表（考勤+分数分布）</td></tr>
            <tr><td style="color:var(--text-muted);">3</td><td><span class="tpl-link" onclick="selectTemplate('关联矩阵')">关联矩阵</span></td><td style="color:var(--text-secondary);">考题与课程目标关联矩阵</td></tr>
          </table>
          <p style="font-size:11px;color:var(--text-muted);margin-top:12px;text-align:center;">点击模板名称或回复编号选择</p>
        </div>`;
      break;

    case 'collecting':
      title.textContent = `信息收集${currentTemplate ? ' · ' + currentTemplate : ''}`;
      if (docPreviewHtml) {
        refreshFieldValues();
      }
      break;

    case 'confirm':
      title.textContent = '核对确认';
      if (docPreviewHtml) {
        refreshFieldValues();
      }
      break;

    case 'generating':
      title.textContent = '生成中...';
      // 不替换预览内容，仅叠加半透明遮罩+进度提示，保留已有字段填写状态
      if (!body.querySelector('.generating-overlay')) {
        const overlay = document.createElement('div');
        overlay.className = 'generating-overlay';
        overlay.innerHTML = '<div class="spinner"></div><p>正在生成文档...</p>';
        const docPage = body.querySelector('.doc-page');
        if (docPage) {
          docPage.style.position = 'relative';
          docPage.appendChild(overlay);
        } else {
          body.appendChild(overlay);
        }
      }
      break;

    case 'done':
      title.textContent = '文档已生成';
      // 移除生成中的遮罩
      body.querySelectorAll('.generating-overlay').forEach(el => el.remove());
      if (docPreviewHtml && currentDownloadUrl) {
        // 保留文档预览，下载提示放在工具栏
        const body = document.getElementById('previewBody');
        // 移除可能残留的旧 download-banner
        body.querySelectorAll('.download-banner').forEach(el => el.remove());
        const actions = document.getElementById('previewActions');
        actions.innerHTML = `
          <div class="download-banner">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0;"><path d="M5 13l4 4L19 7"/></svg>
            <span>已完成</span>
            <button onclick="downloadDoc()" class="dl-btn">下载Word</button>
          </div>
          <button onclick="printDoc()" class="print-btn">打印</button>
        `;
        actions.style.display = 'flex';
      } else if (currentDownloadUrl) {
        // 只有在预览区确实没有文档内容时才显示完成卡片
        const existingDocPage = body.querySelector('.doc-page');
        if (!existingDocPage) {
          body.innerHTML = `
            <div class="doc-done-card">
              <div class="done-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 13l4 4L19 7"/></svg></div>
              <h4>文档生成完成</h4>
              <p>可下载 Word 文档</p>
              <button onclick="downloadDoc()" class="dl-btn" style="cursor:pointer;border:none;">下载 Word 文档</button>
              <div class="checks">
                · 请核对填写内容与原始资料<br>
                · 未提供的信息可能仍为空白<br>
                · 请下载后保存到本机
              </div>
            </div>`;
        }
        document.getElementById('previewActions').style.display = 'flex';
      } else {
        // 无下载链接时，保留已有预览内容
        const existingDocPage = body.querySelector('.doc-page');
        if (!existingDocPage) {
          body.innerHTML = `
            <div class="doc-done-card">
              <div class="done-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 13l4 4L19 7"/></svg></div>
              <h4>文档生成完成</h4>
              <p>文档正在上传，请稍候...</p>
            </div>`;
        }
        document.getElementById('previewActions').style.display = 'none';
      }
      break;

    default:
      title.textContent = '文档预览';
      body.innerHTML = `<div class="empty-state"><div class="icon-wrap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg></div><h4>文档预览区</h4><p>选择模板后，原始文档将在此显示，您可以在此直接填写信息。</p></div>`;
  }
}

// ── Submit manually edited fields to agent ──
async function submitManualEdits() {
  // 收集所有可编辑字段的当前值
  const edits = {};
  const els = document.querySelectorAll('[data-field-id][contenteditable]');
  els.forEach(el => {
    const fid = el.dataset.fieldId;
    const val = el.textContent.trim();
    if (val) {
      fieldData[fid] = val;
      edits[fid] = val;
      manuallyEditedFields[fid] = true;
    }
  });

  const editCount = Object.keys(edits).length;
  if (editCount === 0) {
    alert('没有填写任何字段');
    return;
  }

  // 按 label 分组输出
  let msg = '我手动填写了以下信息：\n';
  for (const [fid, val] of Object.entries(edits)) {
    const label = fieldMap[fid]?.label || fid;
    msg += `- ${fid}（${label}）：${val}\n`;
  }
  msg += '\n请记录这些信息，继续下一步。';

  appendMessage('user', `提交了 ${editCount} 个字段的手动填写`);
  await callAgent(msg);
}

// ── Event delegation for contenteditable changes ──
document.addEventListener('input', function(e) {
  const el = e.target;
  if (el.hasAttribute('data-field-id') && el.hasAttribute('contenteditable')) {
    const fid = el.dataset.fieldId;
    const val = el.textContent.trim();
    fieldData[fid] = val;
    manuallyEditedFields[fid] = true;

    // Update visual state
    if (el.classList.contains('cell-value')) {
      el.classList.toggle('filled', !!val);
    } else if (el.classList.contains('editable')) {
      el.classList.toggle('empty', !val);
      el.classList.toggle('filled', !!val);
    }

    refreshProgressBar();
  }
});
