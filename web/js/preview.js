import { state, escapeHtml } from './helpers.js';
import { sendMessage, appendMessage, callAgent, updateCellInPreview } from './chat.js';
import { showCoFillStep, requestPrefill } from './cofill.js';

// ── Fetch Document Preview ──
// ── Fetch document preview from backend ──
export async function fetchDocPreview(templatePath) {
  if (!state.BASE_URL) {
    console.warn('fetchDocPreview: state.BASE_URL not set, skipping');
    return;
  }
  try {
    const url = `${state.BASE_URL}/template-preview?path=${encodeURIComponent(templatePath)}`;
    console.log('fetchDocPreview: fetching', url);
    const resp = await fetch(url);
    if (!resp.ok) {
      console.warn('fetchDocPreview: HTTP', resp.status, await resp.text());
      return;
    }
    const result = await resp.json();
    console.log('fetchDocPreview: result success=', result.success, 'html_len=', (result.html||'').length);
    if (result.success) {
      state.docPreviewHtml = result.html;
      state.fieldMap = result.field_map || {};
      state.labelToFields = result.label_to_fields || {};
      renderDocPreview();
    }
  } catch (e) {
    console.error('fetchDocPreview failed:', e);
  }
}

// ── Render Document Preview ──
// ── Render document preview ──
export function renderDocPreview() {
  const body = document.getElementById('previewBody');
  if (!state.docPreviewHtml) return;

  // 计算填写进度
  const totalFields = Object.keys(state.fieldMap).length;
  let filledCount = 0;
  for (const fid in state.fieldMap) {
    if (state.fieldData[fid] && state.fieldData[fid].trim()) filledCount++;
  }
  const pct = totalFields > 0 ? Math.round(filledCount / totalFields * 100) : 0;

  // 渲染顶部进度条
  const headerProgress = document.getElementById('headerProgress');
  if (headerProgress && totalFields > 0) {
    headerProgress.innerHTML = `<div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div><span class="bar-text">${filledCount}/${totalFields} 已填写</span>`;
    headerProgress.style.display = 'flex';
  }

  let html = `<div class="doc-page">`;

  // Document HTML
  html += state.docPreviewHtml;

  // Submit button
  html += `<button class="submit-edits-btn" id="submitEditsBtn" ${state.isStreaming ? 'disabled' : ''}>提交手动填写</button>`;
  html += `</div>`;

  body.innerHTML = html;

  // 同步更新协同模式的预览
  if (state.prefillMode) {
    const cofillContainer = document.getElementById('cofillDocContainer');
    if (cofillContainer) cofillContainer.innerHTML = html;
  }
  state.previewRendered = true;
}

// ── Refresh Progress Bar ──
// ── Refresh progress bar only (no cell updates, no recursion) ──
export function refreshProgressBar() {
  const totalFields = Object.keys(state.fieldMap).length;
  let filledCount = 0;
  for (const fid in state.fieldMap) {
    if (state.fieldData[fid] && state.fieldData[fid].trim()) filledCount++;
  }
  const pct = totalFields > 0 ? Math.round(filledCount / totalFields * 100) : 0;
  const barFill = document.querySelector('#headerProgress .bar-fill');
  const barText = document.querySelector('#headerProgress .bar-text');
  if (barFill) barFill.style.width = pct + '%';
  if (barText) barText.textContent = `${filledCount}/${totalFields} 已填写`;
}

// ── Refresh Field Values ──
// ── Refresh field values without re-rendering the whole document ──
export function refreshFieldValues() {
  refreshProgressBar();
  // Update each field cell
  for (const [fid, val] of Object.entries(state.fieldData)) {
    updateCellInPreview(fid, val);
  }
}

// ── Update Preview State ──
// ── Update Preview state ──
export function updatePreview(state) {
  const body = document.getElementById('previewBody');
  const title = document.getElementById('previewTitle');

  switch(state) {
    case 'template-select':
      title.textContent = '选择模板';
      // 如果已加载文档预览，不要覆盖
      if (state.docPreviewHtml) {
        refreshFieldValues();
        break;
      }
      body.innerHTML = `
        <div class="doc-page" style="max-width:520px;padding:36px;">
          <h2>可选教务文档模板</h2>
          <table class="template-table">
            <tr><th style="width:50px;"></th><th>模板名称</th><th style="width:200px;">说明</th></tr>
            <tr><td style="color:var(--text-muted);">1</td><td><span class="tpl-link" data-template="评价报告">评价报告</span></td><td style="color:var(--text-secondary);">专业课程目标达成度评价报告</td></tr>
            <tr><td style="color:var(--text-muted);">2</td><td><span class="tpl-link" data-template="试卷分析">试卷分析</span></td><td style="color:var(--text-secondary);">试卷分析表（考勤+分数分布）</td></tr>
            <tr><td style="color:var(--text-muted);">3</td><td><span class="tpl-link" data-template="关联矩阵">关联矩阵</span></td><td style="color:var(--text-secondary);">考题与课程目标关联矩阵</td></tr>
          </table>
          <p style="font-size:11px;color:var(--text-muted);margin-top:12px;text-align:center;">点击模板名称或回复编号选择</p>
        </div>`;
      break;

    case 'collecting':
      title.textContent = `信息收集${state.currentTemplate ? ' · ' + state.currentTemplate : ''}`;
      if (state.docPreviewHtml) {
        refreshFieldValues();
      }
      break;

    case 'confirm':
      title.textContent = '核对确认';
      if (state.docPreviewHtml) {
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
      if (state.docPreviewHtml && state.currentDownloadUrl) {
        // 保留文档预览，下载提示放在工具栏
        const body = document.getElementById('previewBody');
        // 移除可能残留的旧 download-banner
        body.querySelectorAll('.download-banner').forEach(el => el.remove());
        const actions = document.getElementById('previewActions');
        actions.innerHTML = `
          <div class="download-banner">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;flex-shrink:0;"><path d="M5 13l4 4L19 7"/></svg>
            <span>已完成</span>
            <button class="dl-btn">下载Word</button>
          </div>
          <button class="print-btn">打印</button>
        `;
        actions.style.display = 'flex';
      } else if (state.currentDownloadUrl) {
        // 只有在预览区确实没有文档内容时才显示完成卡片
        const existingDocPage = body.querySelector('.doc-page');
        if (!existingDocPage) {
          body.innerHTML = `
            <div class="doc-done-card">
              <div class="done-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 13l4 4L19 7"/></svg></div>
              <h4>文档生成完成</h4>
              <p>格式与模板一致</p>
              <button class="dl-btn" style="cursor:pointer;border:none;">下载 Word 文档</button>
              <div class="checks">
                · 文档格式与模板一致<br>
                · 所有字段已填入<br>
                · 链接 24 小时内有效
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

// ── Select Template ──
// ── Select a built-in template ──
export async function selectTemplate(name) {
  state.currentTemplate = name;
  state.currentTemplatePath = name;  // 后端支持模板名作为 path
  await fetchDocPreview(name);

  if (state.prefillMode) {
    // 协同模式：模板选好后进入步骤2
    showCoFillStep('upload-knowledge');
  } else {
    sendMessage(name);
  }
}

// ── Auto-fill Mode ──
// ── Function 1: Built-in Templates ──
export async function startAutoFill() {
  if (state.isStreaming) return;
  state.currentMode = 'auto';
  document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('funcAutoFill').classList.add('active');

  if (state.currentStep === 0) {
    appendMessage('user', '启动内置模板模式');
    await callAgent('你好，我要使用内置模板生成教务文档，请展示可用模板');
  } else {
    appendMessage('user', '继续自动填写');
    await callAgent('继续收集信息');
  }
}

// ── Template Upload ──
export function triggerTemplateUpload() {
  document.getElementById('templateFileInput').click();
}

export async function handleTemplateUpload(event) {
  if (!state.UPLOAD_URL) { alert('请通过部署后的 Web 服务 URL 访问。'); return; }
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = '';

  const sizeStr = file.size < 1024*1024 ? (file.size/1024).toFixed(1)+'KB' : (file.size/1024/1024).toFixed(1)+'MB';
  appendMessage('user', `上传了模板文件: ${file.name}（${sizeStr}）`);

  if (!state.prefillMode) {
    document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('funcUploadTpl').classList.add('active');
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    const resp = await fetch(state.UPLOAD_URL.replace('/upload', '/upload-template'), { method: 'POST', body: formData });
    if (!resp.ok) throw new Error(`上传失败 HTTP ${resp.status}`);
    const result = await resp.json();

    if (result.success) {
      const templatePath = result.template_path || '';
      state.currentTemplatePath = templatePath;

      // 获取文档预览
      await fetchDocPreview(templatePath);

      if (state.prefillMode) {
        // 协同模式：模板选好后进入步骤2
        showCoFillStep('upload-knowledge');
      } else {
        // 普通模式：交给Agent分析
        if (result.extracted_text) {
          const agentMsg = `我上传了一个Word模板文件"${file.name}"，模板路径：${templatePath}。请先用analyze_uploaded_template工具分析这个模板路径，识别出所有需要填写的字段。如果工具调用失败，以下是文件内容作为参考：\n\n${result.extracted_text.substring(0, 5000)}`;
          await callAgent(agentMsg);
        } else if (templatePath) {
          const agentMsg = `我上传了一个Word模板文件"${file.name}"，模板路径：${templatePath}，请用analyze_uploaded_template工具分析这个模板，识别出所有需要填写的字段。`;
          await callAgent(agentMsg);
        }
      }
    } else {
      appendMessage('assistant', `模板解析失败：${result.error || '未知错误'}，请确保上传的是.docx格式的Word文件。`);
    }
  } catch (err) {
    appendMessage('assistant', `模板上传失败：${escapeHtml(err.message)}，请检查网络后重试。`);
  }
}

// ── Placeholder ──
export function startSelectFill() {}

// ── Upload Functions ──

export function triggerUpload() {
  document.getElementById('fileInput').click();
}

// ── Handle File Upload ──
export async function handleFileUpload(event) {
  if (!state.UPLOAD_URL) { alert('请通过部署后的 Web 服务 URL 访问。'); return; }
  const files = Array.from(event.target.files);
  if (!files.length) return;
  event.target.value = '';

  // 显示上传消息
  if (files.length === 1) {
    const sizeStr = files[0].size < 1024*1024 ? (files[0].size/1024).toFixed(1)+'KB' : (files[0].size/1024/1024).toFixed(1)+'MB';
    appendMessage('user', `上传了文件: ${files[0].name}（${sizeStr}）`);
  } else {
    const totalSize = files.reduce((s, f) => s + f.size, 0);
    const sizeStr = totalSize < 1024*1024 ? (totalSize/1024).toFixed(1)+'KB' : (totalSize/1024/1024).toFixed(1)+'MB';
    const names = files.map(f => f.name).join('、');
    appendMessage('user', `上传了${files.length}个文件（${sizeStr}）：${names}`);
  }

  // 构建多文件 FormData
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file);
  }

  try {
    const resp = await fetch(state.UPLOAD_URL, { method: 'POST', body: formData });
    if (!resp.ok) throw new Error(`上传失败 HTTP ${resp.status}`);
    const result = await resp.json();

    // 处理多文件响应
    if (result.multiple && result.files) {
      // 收集成功上传的文件路径
      const uploadedPaths = result.files.filter(f => f.success).map(f => f.file_path).filter(Boolean);

      // 人机协同模式：调用预填API
      if (state.prefillMode && uploadedPaths.length > 0) {
        await requestPrefill(uploadedPaths);
        return;
      }

      // 普通模式：合并所有文件内容发给Agent（同时传文件路径，让Agent可以调用工具分析）
      const fileParts = [];
      const filePaths = [];
      for (const f of result.files) {
        if (f.success && f.extracted_text) {
          filePaths.push(f.file_path);
          fileParts.push(`=== 文件：${f.filename} ===\n${f.extracted_text.substring(0, 4000)}`);
        } else if (!f.success) {
          fileParts.push(`=== 文件：${f.filename} ===\n[解析失败: ${f.error || '未知错误'}]`);
        }
      }
      if (fileParts.length > 0) {
        const pathsSection = filePaths.length > 0 ? `\n【文件路径（重要！请用这些路径调用工具）】\n${filePaths.map((p, i) => `文件${i+1}: ${p}`).join('\n')}\n` : '';
        const agentMsg = `我上传了${result.file_count}个文件。${pathsSection}\n请用analyze_uploaded_template或extract_from_old_report工具，传入上面的文件路径来分析。文件文本内容如下仅供参考：\n\n${fileParts.join('\n\n')}`;
        await callAgent(agentMsg);
      } else {
        appendMessage('assistant', `所有文件解析失败，请尝试其他格式或直接输入信息。`);
      }
    } else {
      // 单文件模式（向后兼容）
      const uploadedPath = result.file_path;

      // 人机协同模式：调用预填API
      if (state.prefillMode && uploadedPath) {
        await requestPrefill([uploadedPath]);
        return;
      }

      if (result.success && result.extracted_text) {
        const agentMsg = `我上传了文件"${result.filename}"。\n\n【文件路径（重要！请用这个路径调用工具）】\n${result.file_path}\n\n请用analyze_uploaded_template或extract_from_old_report工具，传入上面的文件路径来分析。文件文本内容如下仅供参考：\n\n${result.extracted_text.substring(0, 3000)}`;
        await callAgent(agentMsg);
      } else {
        appendMessage('assistant', `文件解析失败：${result.error || '未知错误'}，请尝试其他格式或直接输入信息。`);
      }
    }
  } catch (err) {
    appendMessage('assistant', `文件上传失败：${escapeHtml(err.message)}，请检查网络后重试。`);
  }
}

// ── Highlight Upload Button ──

export function highlightUploadBtn() {
  const btn = document.getElementById('funcUpload');
  btn.style.animation = 'pulse 1s ease 3';
  setTimeout(() => btn.style.animation = '', 3000);
}

// ── Download Doc ──

export function downloadDoc() {
  // 优先使用后端代理下载（支持远程URL和本地文件）
  if (state.currentDocxPath) {
    const url = `${state.BASE_URL}/download-docx?file_path=${encodeURIComponent(state.currentDocxPath)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return;
  }
  if (state.currentDownloadUrl) {
    const url = `${state.BASE_URL}/download-docx?file_path=${encodeURIComponent(state.currentDownloadUrl)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
}

// ── Print Doc ──
export function printDoc() {
  // 直接使用 HTML 打印预览，调起系统打印对话框（用户可选打印机/格式）
  _printDocFallback();
}
export function _printDocFallback() {
  const previewBody = document.getElementById('previewBody');
  if (!previewBody || !previewBody.querySelector('.doc-page')) {
    return;
  }
  const docPages = previewBody.querySelectorAll('.doc-page');
  let docHtml = '';
  docPages.forEach(page => {
    const clone = page.cloneNode(true);
    clone.querySelectorAll('#submitManualBtn, .submit-manual-btn').forEach(el => el.remove());
    clone.querySelectorAll('[contenteditable]').forEach(el => el.removeAttribute('contenteditable'));
    clone.querySelectorAll('.placeholder-text').forEach(el => {
      if (!el.textContent.trim() || el.textContent.trim() === '—') {
        el.textContent = '';
      }
    });
    docHtml += clone.outerHTML;
  });
  const printWin = window.open('', '_blank', 'width=900,height=700');
  printWin.document.write(`<!DOCTYPE html><html><head><title>打印预览</title>
    <style>
      @page { size: A4; margin: 15mm 12mm; }
      * { box-sizing: border-box; }
      body { margin: 0; padding: 0; font-family: SimSun, Songti SC, serif; color: #000; }
      .doc-page {
        width: 100%; max-width: 190mm;
        padding: 0; margin: 0 auto;
        font-size: 10.5pt; line-height: 1.8; color: #000;
        background: none; box-shadow: none; border-radius: 0;
      }
      .doc-page h2 {
        text-align: center; font-size: 14pt;
        margin: 0 0 16px 0; font-weight: 600;
        color: #000; letter-spacing: 1px;
      }
      .doc-page p { margin: 0 0 6px 0; font-size: 10.5pt; }
      .doc-table {
        width: 100%; border-collapse: collapse;
        margin-bottom: 10px; font-size: 9pt; line-height: 1.6;
      }
      .doc-table td, .doc-table th {
        border: 1px solid #000 !important;
        padding: 3px 6px; vertical-align: middle;
        text-align: left; word-wrap: break-word;
      }
      .label-cell { font-weight: bold; }
      .value-cell { min-width: 30px; }
      .value-cell.filled { color: #000; }
      .value-cell.empty { color: #999; }
      .colon-label { font-weight: bold; white-space: nowrap; }
      .colon-value { font-weight: normal; }
      .download-banner, .progress-bar, .submit-manual-btn, #submitManualBtn,
      button, .actions, .preview-actions, .placeholder-text { display: none !important; }
      [data-field-id] { border: none !important; background: none !important; outline: none !important; }
      .doc-table { page-break-inside: auto; }
      tr { page-break-inside: avoid; page-break-after: auto; }
      thead { display: table-header-group; }
    </style></head><body>
    ${docHtml}
    <script>window.onload=function(){window.print();}<\/script>
  </body></html>`);
  printWin.document.close();
}

// ── Submit Manual Edits ──
// ── Submit manually edited fields to agent ──
export async function submitManualEdits() {
  // 收集所有可编辑字段的当前值
  const edits = {};
  const els = document.querySelectorAll('[data-field-id][contenteditable]');
  els.forEach(el => {
    const fid = el.dataset.fieldId;
    const val = el.textContent.trim();
    if (val) {
      state.fieldData[fid] = val;
      edits[fid] = val;
      state.manuallyEditedFields[fid] = true;
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
    const label = state.fieldMap[fid]?.label || fid;
    msg += `- ${label}：${val}\n`;
  }
  msg += '\n请记录这些信息，继续下一步。';

  appendMessage('user', `提交了 ${editCount} 个字段的手动填写`);
  await callAgent(msg);
}
