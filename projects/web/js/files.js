// ── Select a built-in template ──
async function selectTemplate(name) {
  currentTemplate = name;
  currentTemplatePath = name;  // 后端支持模板名作为 path
  await fetchDocPreview(name);

  if (prefillMode) {
    // 协同模式：模板选好后进入步骤2
    showCoFillStep('upload-knowledge');
  } else {
    sendMessage(name);
  }
}

// ── Function 1: Built-in Templates ──
async function startAutoFill() {
  if (isStreaming) return;
  currentMode = 'auto';
  document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('funcAutoFill').classList.add('active');

  if (currentStep === 0) {
    appendMessage('user', '启动内置模板模式');
    await callAgent('你好，我要使用内置模板生成教务文档，请展示可用模板');
  } else {
    appendMessage('user', '继续自动填写');
    await callAgent('继续收集信息');
  }
}

function triggerTemplateUpload() {
  document.getElementById('templateFileInput').click();
}

async function handleTemplateUpload(event) {
  if (!UPLOAD_URL) { alert('请通过部署后的 Web 服务 URL 访问。'); return; }
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = '';

  const sizeStr = file.size < 1024*1024 ? (file.size/1024).toFixed(1)+'KB' : (file.size/1024/1024).toFixed(1)+'MB';
  appendMessage('user', `上传了模板文件: ${file.name}（${sizeStr}）`);

  if (!prefillMode) {
    document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('funcUploadTpl').classList.add('active');
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    const resp = await fetch(UPLOAD_URL.replace('/upload', '/upload-template'), { method: 'POST', body: formData });
    if (!resp.ok) throw new Error(`上传失败 HTTP ${resp.status}`);
    const result = await resp.json();

    if (result.success) {
      const templatePath = result.template_path || '';
      currentTemplatePath = templatePath;

      // 获取文档预览
      await fetchDocPreview(templatePath);

      if (prefillMode) {
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

function triggerUpload() {
  document.getElementById('fileInput').click();
}

async function handleFileUpload(event) {
  if (!UPLOAD_URL) { alert('请通过部署后的 Web 服务 URL 访问。'); return; }
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
    const resp = await fetch(UPLOAD_URL, { method: 'POST', body: formData });
    if (!resp.ok) throw new Error(`上传失败 HTTP ${resp.status}`);
    const result = await resp.json();

    // 处理多文件响应
    if (result.multiple && result.files) {
      // 收集成功上传的文件路径
      const uploadedPaths = result.files.filter(f => f.success).map(f => f.file_path).filter(Boolean);

      // 人机协同模式：调用预填API
      if (prefillMode && uploadedPaths.length > 0) {
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
      if (prefillMode && uploadedPath) {
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

function highlightUploadBtn() {
  const btn = document.getElementById('funcUpload');
  btn.style.animation = 'pulse 1s ease 3';
  setTimeout(() => btn.style.animation = '', 3000);
}

function downloadDoc() {
  // 优先使用后端代理下载（支持远程URL和本地文件）
  if (currentDocxPath) {
    const url = `${BASE_URL}/download-docx?file_path=${encodeURIComponent(currentDocxPath)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    return;
  }
  if (currentDownloadUrl) {
    const url = `${BASE_URL}/download-docx?file_path=${encodeURIComponent(currentDownloadUrl)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
}

function printDoc() {
  // 直接使用 HTML 打印预览，调起系统打印对话框（用户可选打印机/格式）
  _printDocFallback();
}
function _printDocFallback() {
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
      .doc-table td { display: table-cell !important; }
      .colon-field-inner {
        display: flex; flex-wrap: wrap; align-items: center; gap: 2px;
      }
      .doc-cell.colon-field.multi-line .colon-field-inner {
        flex-direction: column; align-items: stretch;
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
