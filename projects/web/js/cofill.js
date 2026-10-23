// Collaborative editing keeps field IDs as the identity, labels are display text.
function syncCoFillValues() {
  const values = {};
  for (const field of prefillData?.fields || []) {
    if (field.field_id) {
      // Untouched blank fields keep the template's visible placeholders.
      // An explicitly cleared value already exists in fieldData and must sync.
      if (hasFieldValue(field.value) || Object.hasOwn(fieldData, field.field_id)) {
        values[field.field_id] = fieldText(field.value);
      }
    }
    else updateFieldByLabel(field.label, fieldText(field.value));
  }
  applyPreciseFieldValues(values);
}

// ── Prefill review state ──

async function startCoFill() {
  if (isStreaming) return;
  currentMode = 'cofill';
  prefillMode = true;
  document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('funcCoFill').classList.add('active');

  // 进入全屏协同视图
  document.querySelector('.app').classList.add('cofill-mode');

  if (!currentTemplate && !currentTemplatePath) {
    // 步骤1：选择模板
    showCoFillStep('select-template');
  } else {
    // 已有模板，进入步骤2：上传知识文件
    showCoFillStep('upload-knowledge');
  }
}

function showCoFillStep(step) {
  const body = document.getElementById('cofillFieldsBody');
  const footer = document.getElementById('cofillFooter');
  const stepText = document.getElementById('cofillStepText');
  const previewBody = document.getElementById('cofillDocContainer');

  switch (step) {
    case 'select-template':
      stepText.textContent = '步骤 1/3：选择模板';
      footer.style.display = 'none';
      body.innerHTML = `
        <div class="cofill-step-guide">
          <div class="step-icon">📄</div>
          <h3>选择文档模板</h3>
          <p>请选择要填写的教务文档模板，支持内置模板或上传自定义Word模板</p>
          <div class="step-actions">
            <button onclick="cofillSelectBuiltIn()">📋 选择内置模板</button>
            <button class="primary" onclick="cofillUploadTemplate()">📤 上传自定义模板</button>
          </div>
        </div>`;
      previewBody.innerHTML = `<div class="cofill-step-guide"><div class="step-icon">👀</div><h3>模板预览</h3><p>选择模板后，文档预览将在此显示</p></div>`;
      break;

    case 'upload-knowledge':
      stepText.textContent = '步骤 2/3：上传知识文件';
      footer.style.display = 'none';
      body.innerHTML = `
        <div class="cofill-step-guide">
          <div class="step-icon">📚</div>
          <h3>上传知识文件</h3>
          <p>上传教学大纲、成绩单、课程计划等文件，AI将自动提取信息并预填到模板字段中</p>
          <div style="font-size:12px;color:#888;margin-top:4px">支持 .docx .txt .pdf .csv，可多选</div>
          <div class="step-actions">
            <button class="primary" onclick="triggerUpload()">📎 选择知识文件</button>
            <button onclick="cofillSkipKnowledge()">跳过，手动填写</button>
          </div>
        </div>`;
      // 在右侧显示模板预览
      if (docPreviewHtml) {
        previewBody.innerHTML = docPreviewHtml;
      }
      break;

    case 'prefilling':
      stepText.textContent = '步骤 2/3：AI提取中...';
      footer.style.display = 'none';
      body.innerHTML = `
        <div class="cofill-loading">
          <div class="spinner"></div>
          <p>🤖 AI正在从知识文件中提取字段值...</p>
          <div class="sub">这可能需要10-30秒</div>
        </div>`;
      break;

    case 'review':
      stepText.textContent = '步骤 3/3：审核确认';
      footer.style.display = 'flex';
      renderCoFillReview();
      break;

    case 'manual':
      stepText.textContent = '步骤 3/3：手动填写';
      footer.style.display = 'flex';
      renderCoFillManual();
      break;
  }
}

function cofillSelectBuiltIn() {
  // 在左侧面板展示内置模板选择
  const body = document.getElementById('cofillFieldsBody');
  body.innerHTML = `
    <div style="padding:12px 0">
      <div class="cofill-field-group-title">内置模板</div>
      <div class="cofill-field-card confirmed" style="cursor:pointer" onclick="cofillPickTemplate('评价报告')">
        <div class="dot"></div>
        <div class="field-body">
          <div class="field-label">📊 课程目标达成度评价报告</div>
          <div style="font-size:12px;color:#888">93个字段 · 含课程目标、成绩统计等</div>
        </div>
      </div>
      <div class="cofill-field-card confirmed" style="cursor:pointer" onclick="cofillPickTemplate('试卷分析')">
        <div class="dot"></div>
        <div class="field-body">
          <div class="field-label">📝 试卷分析报告</div>
          <div style="font-size:12px;color:#888">20个字段 · 含考勤、成绩分布等</div>
        </div>
      </div>
      <div class="cofill-field-card confirmed" style="cursor:pointer" onclick="cofillPickTemplate('关联矩阵')">
        <div class="dot"></div>
        <div class="field-body">
          <div class="field-label">🔗 考题与课程目标关联矩阵</div>
          <div style="font-size:12px;color:#888">含课程目标、考题对应关系等</div>
        </div>
      </div>
    </div>`;
}

async function cofillPickTemplate(name) {
  currentTemplate = name;
  currentTemplatePath = name;

  // 显示加载
  const body = document.getElementById('cofillFieldsBody');
  body.innerHTML = `<div class="cofill-loading"><div class="spinner"></div><p>正在分析模板字段...</p></div>`;

  // 获取文档预览
  await fetchDocPreview(name);

  // 调用分析API获取字段列表
  try {
    const resp = await fetch(BASE_URL + '/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `请使用analyze_report_template工具分析"${name}"模板，返回所有可填写字段。`,
        session_id: sessionId,
      }),
    });
    // 这里不等待完整响应，直接进入步骤2
  } catch (e) {
    // 忽略错误，进入步骤2即可
  }

  // 存储分析结果到 window._lastAnalyzedFields 以便手动填写模式使用
  // 模板选好后进入步骤2
  showCoFillStep('upload-knowledge');
}

function cofillUploadTemplate() {
  document.getElementById('templateFileInput').click();
}

function cofillSkipKnowledge() {
  // 跳过知识文件，直接进入手动填写
  showCoFillStep('manual');
}

function exitCoFill() {
  syncCoFillValues();
  document.querySelector('.app').classList.remove('cofill-mode');
  prefillMode = false;
  prefillData = null;
  document.querySelectorAll('.func-btn').forEach(button => button.classList.remove('active'));
  if (docPreviewHtml) renderDocPreview();
}

async function requestPrefill(filePaths) {
  /** 调用 /prefill API 获取预填结果 */
  if (!currentTemplate && !currentTemplatePath) {
    alert('请先选择模板');
    return;
  }

  const templatePath = currentTemplatePath || currentTemplate;
  if (!templatePath) {
    alert('未检测到模板，请先选择模板。');
    return;
  }

  // 显示加载态
  showCoFillStep('prefilling');

  try {
    const resp = await fetch(BASE_URL + '/prefill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_paths: filePaths,
        template_path: templatePath,
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();

    if (result.success) {
      prefillData = result;
      showCoFillStep('review');
      // 在聊天窗口也通知一下
      appendMessage('user', '已上传知识文件，AI正在提取预填');
      appendMessage('assistant', `✅ ${result.summary || 'AI预填完成'}\n\n请在左侧审核面板中确认或修正预填结果，确认后点击「确认并生成」。`);
    } else {
      alert(`预填失败：${result.message || '未知错误'}，将进入手动填写模式`);
      showCoFillStep('manual');
    }
  } catch (err) {
    alert(`预填请求失败：${err.message}，将进入手动填写模式`);
    showCoFillStep('manual');
  }
}

function renderCoFillReview() {
  /** 在协同视图左侧渲染预填审核字段卡片 */
  const body = document.getElementById('cofillFieldsBody');
  const fields = prefillData.fields || [];

  // 分组
  const confirmed = fields.filter(f => f.status === 'confirmed');
  const review = fields.filter(f => f.status === 'review');
  const empty = fields.filter(f => f.status === 'empty');
  const total = fields.length;
  const filled = confirmed.length + review.length;
  const pct = total > 0 ? Math.round(filled / total * 100) : 0;

  // 更新进度
  document.getElementById('cofillProgress').style.display = 'flex';
  document.getElementById('cofillBarFill').style.width = pct + '%';
  document.getElementById('cofillPct').textContent = pct + '%';
  document.getElementById('cofillBadges').style.display = 'flex';
  document.getElementById('cofillBadges').innerHTML = `
    <span class="badge green">✅ 已确认 ${confirmed.length}</span>
    <span class="badge yellow">⚠️ 需审核 ${review.length}</span>
    <span class="badge gray">⬜ 未填写 ${empty.length}</span>
  `;

  let html = '';

  // 需审核字段
  if (review.length > 0) {
    html += `<div class="cofill-field-group-title">⚠️ 需要审核（${review.length}）</div>`;
    for (const f of review) html += renderCoFillFieldCard(f, 'review');
  }

  // 未填写字段
  if (empty.length > 0) {
    html += `<div class="cofill-field-group-title">⬜ 需要填写（${empty.length}）</div>`;
    for (const f of empty) html += renderCoFillFieldCard(f, 'empty');
  }

  // 已确认字段（折叠）
  if (confirmed.length > 0) {
    html += `<div class="cofill-field-group-title" style="cursor:pointer" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.textContent=this.nextElementSibling.style.display==='none'?'✅ 已确认（${confirmed.length}） ▸ 点击展开':'✅ 已确认（${confirmed.length}） ▾ 点击收起'">✅ 已确认（${confirmed.length}） ▸ 点击展开</div>`;
    html += `<div style="display:none">`;
    for (const f of confirmed) html += renderCoFillFieldCard(f, 'confirmed');
    html += `</div>`;
  }

  body.innerHTML = html;

  const previewContainer = document.getElementById('cofillDocContainer');
  if (docPreviewHtml) previewContainer.innerHTML = docPreviewHtml;
  syncCoFillValues();

  // 更新提交按钮状态
  document.getElementById('cofillSubmitBtn').disabled = (filled === 0);
}

function renderCoFillManual() {
  const body = document.getElementById('cofillFieldsBody');
  const fields = prefillData?.fields?.length ? prefillData.fields : manualFields();
  if (!fields.length) {
    body.innerHTML = '<div class="cofill-step-guide"><h3>暂无字段信息</h3><p>请先选择模板并等待字段分析完成</p></div>';
    return;
  }
  prefillData = {...prefillData, fields};
  document.getElementById('cofillProgress').style.display = 'flex';
  document.getElementById('cofillBadges').style.display = 'flex';
  body.innerHTML = `<div class="cofill-field-group-title">请填写以下字段（${fields.length}）</div>`
    + fields.map(field => renderCoFillFieldCard(field, field.status)).join('');
  if (docPreviewHtml) document.getElementById('cofillDocContainer').innerHTML = docPreviewHtml;
  syncCoFillValues();
  updateCoFillProgress();
}

function renderCoFillFieldCard(field, status) {
  /** 渲染单个字段卡片 */
  const value = fieldText(field.value);
  const confidence = field.confidence || 0;
  const source = field.source || '';
  const label = field.raw_label || field.label;

  return `<div class="cofill-field-card ${status}">
    <div class="dot"></div>
    <div class="field-body">
      <div class="field-label">${escapeHtml(label)}${source ? `<span class="source-tag">${escapeHtml(source)}</span>` : ''}</div>
      <input type="text"
        data-field-id="${escapeAttr(field.field_id)}"
        data-label="${escapeAttr(label)}"
        value="${escapeAttr(value)}"
        placeholder="${status === 'empty' ? '请输入...' : ''}"
        oninput="onCoFillFieldInput(this)"
      />
    </div>
    ${confidence > 0 ? `<span class="conf-badge">${Math.round(confidence * 100)}%</span>` : ''}
  </div>`;
}

function onCoFillFieldInput(input) {
  const filled = hasFieldValue(input.value);
  const card = input.closest('.cofill-field-card');
  card.classList.remove('empty', 'review', 'confirmed');
  card.classList.add(filled ? 'confirmed' : 'empty');
  const id = input.dataset.fieldId;
  const field = prefillData?.fields?.find(field => field.field_id === id);
  if (field) Object.assign(field, {
    value: input.value, status: filled ? 'confirmed' : 'empty',
    confidence: filled ? 1 : 0, source: filled ? '用户填写' : '',
  });
  updateFieldById(id, input.value);
  manuallyEditedFields[id] = true;
  updateCoFillProgress();
}

function updateCoFillProgress() {
  /** 更新协同视图的进度条 */
  if (!prefillData || !prefillData.fields) return;
  const fields = prefillData.fields;
  const confirmed = fields.filter(f => f.status === 'confirmed').length;
  const review = fields.filter(f => f.status === 'review').length;
  const total = fields.length;
  const filled = confirmed + review;
  const pct = total > 0 ? Math.round(filled / total * 100) : 0;

  document.getElementById('cofillBarFill').style.width = pct + '%';
  document.getElementById('cofillPct').textContent = pct + '%';
  document.getElementById('cofillBadges').innerHTML = `
    <span class="badge green">✅ 已确认 ${confirmed}</span>
    ${review > 0 ? `<span class="badge yellow">⚠️ 需审核 ${review}</span>` : ''}
    <span class="badge gray">⬜ 未填写 ${total - filled}</span>
  `;

  // 更新提交按钮
  document.getElementById('cofillSubmitBtn').disabled = (filled === 0);
}

async function submitPrefillReview() {
  if (!prefillData || isStreaming) return;
  const values = {};
  for (const input of document.querySelectorAll('#cofillFieldsBody input[data-field-id]')) {
    const key = input.dataset.fieldId || input.dataset.label;
    if (key && hasFieldValue(input.value)) values[key] = input.value.trim();
  }
  if (!Object.keys(values).length) { alert('请至少填写一个字段'); return; }
  exitCoFill();
  isStreaming = true;
  document.getElementById('sendBtn').disabled = true;
  appendMessage('user', '确认预填结果，生成文档');
  const pending = appendMessage('assistant', '正在生成文档...');
  const bubble = pending.querySelector('.bubble');
  try {
    const response = await fetch('/cofill-generate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({template_path: currentTemplatePath, field_values: values}),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (!result.success) throw new Error(result.message || '文档生成失败');
    currentDownloadUrl = result.download_url;
    bubble.innerHTML = renderMarkdown(`文档已生成完成！\n\n[下载链接](${result.download_url})`);
    if (result.local_path) {
      currentDocxPath = result.local_path;
      await refreshPreviewFromGeneratedDoc(result.local_path);
    }
  } catch (error) {
    bubble.textContent = `生成失败：${error.message}`;
  } finally {
    isStreaming = false;
    document.getElementById('sendBtn').disabled = false;
  }
}

async function sendPrefillToAgent() {
  /** 将预填结果发给Agent继续交互式填写 */
  if (!prefillData) return;

  const fields = prefillData.fields || [];
  const filled = fields.filter(f => hasFieldValue(f.value) && f.status !== 'empty');
  const emptyFields = fields.filter(f => !hasFieldValue(f.value) || f.status === 'empty');

  let msg = `AI预填结果如下，已填字段请直接使用，未填字段请继续追问：\n\n[FIELDS]\n`;
  for (const f of filled) {
    msg += `${f.field_id || f.label}=${f.value}\n`;
  }
  msg += `[/FIELDS]\n\n`;
  msg += `还需填写：${emptyFields.map(f => f.label).join('、')}`;

  // 退出协同模式，回到聊天界面
  exitCoFill();

  appendMessage('user', '将预填结果交给AI继续');
  await callAgent(msg);
}
