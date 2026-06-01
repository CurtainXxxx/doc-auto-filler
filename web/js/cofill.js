import { state, escapeHtml, escapeAttr } from './helpers.js';
import { appendMessage, callAgent } from './chat.js';
import { fetchDocPreview } from './preview.js';

// ── Co-Fill State ──
// ── Prefill review state ──
state.prefillData = null;      // 预填结果数据
state.prefillMode = false;     // 是否在协同填写模式

// ── Start Co-Fill ──

export async function startCoFill() {
  if (state.isStreaming) return;
  state.currentMode = 'cofill';
  state.prefillMode = true;
  document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('funcCoFill').classList.add('active');

  // 进入全屏协同视图
  document.querySelector('.app').classList.add('cofill-mode');

  if (!state.currentTemplate && !state.currentTemplatePath) {
    // 步骤1：选择模板
    showCoFillStep('select-template');
  } else {
    // 已有模板，进入步骤2：上传知识文件
    showCoFillStep('upload-knowledge');
  }
}

// ── Show Co-Fill Step ──

export function showCoFillStep(step) {
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
            <button data-action="cofillSelectBuiltIn">📋 选择内置模板</button>
            <button class="primary" data-action="cofillUploadTemplate">📤 上传自定义模板</button>
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
            <button class="primary" data-action="triggerUpload">📎 选择知识文件</button>
            <button data-action="cofillSkipKnowledge">跳过，手动填写</button>
          </div>
        </div>`;
      // 在右侧显示模板预览
      if (state.docPreviewHtml) {
        previewBody.innerHTML = state.docPreviewHtml;
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

// ── Co-Fill: Select Built-in ──

export function cofillSelectBuiltIn() {
  // 在左侧面板展示内置模板选择
  const body = document.getElementById('cofillFieldsBody');
  body.innerHTML = `
    <div style="padding:12px 0">
      <div class="cofill-field-group-title">内置模板</div>
      <div class="cofill-field-card confirmed" style="cursor:pointer" data-template="评价报告">
        <div class="dot"></div>
        <div class="field-body">
          <div class="field-label">📊 课程目标达成度评价报告</div>
          <div style="font-size:12px;color:#888">93个字段 · 含课程目标、成绩统计等</div>
        </div>
      </div>
      <div class="cofill-field-card confirmed" style="cursor:pointer" data-template="试卷分析">
        <div class="dot"></div>
        <div class="field-body">
          <div class="field-label">📝 试卷分析报告</div>
          <div style="font-size:12px;color:#888">20个字段 · 含考勤、成绩分布等</div>
        </div>
      </div>
      <div class="cofill-field-card confirmed" style="cursor:pointer" data-template="关联矩阵">
        <div class="dot"></div>
        <div class="field-body">
          <div class="field-label">🔗 考题与课程目标关联矩阵</div>
          <div style="font-size:12px;color:#888">含课程目标、考题对应关系等</div>
        </div>
      </div>
    </div>`;
}

// ── Co-Fill: Pick Template ──

export async function cofillPickTemplate(name) {
  state.currentTemplate = name;
  state.currentTemplatePath = name;

  // 显示加载
  const body = document.getElementById('cofillFieldsBody');
  body.innerHTML = `<div class="cofill-loading"><div class="spinner"></div><p>正在分析模板字段...</p></div>`;

  // 获取文档预览
  await fetchDocPreview(name);

  // 调用分析API获取字段列表
  try {
    const resp = await fetch(state.BASE_URL + '/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `请使用analyze_report_template工具分析"${name}"模板，返回所有可填写字段。`,
        session_id: state.sessionId,
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

// ── Co-Fill: Upload Template ──

export function cofillUploadTemplate() {
  document.getElementById('templateFileInput').click();
}

// ── Co-Fill: Skip Knowledge ──

export function cofillSkipKnowledge() {
  // 跳过知识文件，直接进入手动填写
  showCoFillStep('manual');
}

// ── Exit Co-Fill ──

export function exitCoFill() {
  document.querySelector('.app').classList.remove('cofill-mode');
  state.prefillMode = false;
  state.prefillData = null;
  document.querySelectorAll('.func-btn').forEach(b => b.classList.remove('active'));
  // 恢复预览
  if (state.docPreviewHtml) {
    document.getElementById('previewBody').innerHTML = state.docPreviewHtml;
  }
}

// ── Request Prefill ──

export async function requestPrefill(filePaths) {
  /** 调用 /prefill API 获取预填结果 */
  if (!state.currentTemplate && !state.currentTemplatePath) {
    alert('请先选择模板');
    return;
  }

  const templatePath = state.currentTemplatePath || state.currentTemplate;
  if (!templatePath) {
    alert('未检测到模板，请先选择模板。');
    return;
  }

  // 显示加载态
  showCoFillStep('prefilling');

  try {
    const resp = await fetch(state.BASE_URL + '/prefill', {
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
      state.prefillData = result;
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

// ── Render Co-Fill Review ──

export function renderCoFillReview() {
  /** 在协同视图左侧渲染预填审核字段卡片 */
  const body = document.getElementById('cofillFieldsBody');
  const fields = state.prefillData.fields || [];

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

  // 更新右侧预览
  const previewContainer = document.getElementById('cofillDocContainer');
  if (state.docPreviewHtml) {
    previewContainer.innerHTML = state.docPreviewHtml;
  }

  // 更新提交按钮状态
  document.getElementById('cofillSubmitBtn').disabled = (filled === 0);
}

// ── Render Co-Fill Manual ──

export function renderCoFillManual() {
  /** 在协同视图左侧渲染手动填写表单（无预填数据时） */
  const body = document.getElementById('cofillFieldsBody');

  // 优先使用prefillData的字段，其次从fieldMap构建
  let fields = [];
  if (state.prefillData && state.prefillData.fields && state.prefillData.fields.length > 0) {
    fields = state.prefillData.fields;
  } else if (Object.keys(state.fieldMap).length > 0) {
    // 从预览的fieldMap构建字段列表
    fields = [];
    for (const [fid, info] of Object.entries(state.fieldMap)) {
      const label = info.label || info.raw_label || fid;
      // 避免重复label
      if (!fields.find(f => f.label === label)) {
        fields.push({
          field_id: fid,
          label: label,
          raw_label: label,
          value: '',
          confidence: 0,
          source: '',
          status: 'empty',
        });
      }
    }
    // 去重：同一label可能有多个field_id，只保留第一个
    const seen = new Set();
    fields = fields.filter(f => {
      if (seen.has(f.label)) return false;
      seen.add(f.label);
      return true;
    });
  }

  if (fields.length === 0) {
    body.innerHTML = `<div class="cofill-step-guide"><div class="step-icon">📝</div><h3>暂无字段信息</h3><p>请先选择模板并等待字段分析完成</p></div>`;
    return;
  }

  // 创建prefillData if not exists
  if (!state.prefillData) {
    state.prefillData = { fields: fields, template_fields: fields.length, prefilled: 0, needs_review: 0, still_empty: fields.length, fill_rate: 0, fill_rate_pct: 0 };
  }

  // 更新进度
  document.getElementById('cofillProgress').style.display = 'flex';
  document.getElementById('cofillBarFill').style.width = '0%';
  document.getElementById('cofillPct').textContent = '0%';
  document.getElementById('cofillBadges').style.display = 'flex';
  document.getElementById('cofillBadges').innerHTML = `<span class="badge gray">⬜ 需填写 ${fields.length}</span>`;

  let html = `<div class="cofill-field-group-title">📝 请填写以下字段（${fields.length}）</div>`;
  for (const f of fields) html += renderCoFillFieldCard(f, 'empty');
  body.innerHTML = html;

  // 右侧预览
  const previewContainer = document.getElementById('cofillDocContainer');
  if (state.docPreviewHtml) previewContainer.innerHTML = state.docPreviewHtml;

  document.getElementById('cofillSubmitBtn').disabled = true;
}

// ── Render Co-Fill Field Card ──

export function renderCoFillFieldCard(field, status) {
  /** 渲染单个字段卡片 */
  const value = field.value || '';
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

// ── On Co-Fill Field Input ──

export function onCoFillFieldInput(input) {
  /** 字段值被修改时更新状态 */
  const card = input.closest('.cofill-field-card');
  if (input.value.trim()) {
    card.classList.remove('empty', 'review');
    card.classList.add('confirmed');
  } else {
    card.classList.remove('confirmed', 'review');
    card.classList.add('empty');
  }
  // 更新state.prefillData
  if (state.prefillData && state.prefillData.fields) {
    const fid = input.dataset.fieldId;
    const f = state.prefillData.fields.find(f => f.field_id === fid);
    if (f) {
      f.value = input.value;
      f.status = input.value.trim() ? 'confirmed' : 'empty';
      f.confidence = input.value.trim() ? 1.0 : 0;
      f.source = input.value.trim() ? '用户填写' : '';
    }
  }
  // 更新进度
  updateCoFillProgress();
}

// ── Update Co-Fill Progress ──

export function updateCoFillProgress() {
  /** 更新协同视图的进度条 */
  if (!state.prefillData || !state.prefillData.fields) return;
  const fields = state.prefillData.fields;
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

// ── Legacy Compat ──

// Legacy export function kept for backward compat
export function renderFieldCard(field, status) {
  return renderCoFillFieldCard(field, status);
}

// Legacy export function kept for backward compat
export function onPrefillFieldChange(input) {
  onCoFillFieldInput(input);
}

// ── Submit Prefill Review ──

export async function submitPrefillReview() {
  /** 确认预填结果并生成文档 */
  if (!state.prefillData) return;

  // 收集所有字段的最终值
  const finalData = {};
  const allInputs = document.querySelectorAll('#cofillFieldsBody input[data-field-id]');
  allInputs.forEach(input => {
    const label = input.dataset.label;
    const value = input.value.trim();
    if (value) {
      finalData[label] = value;
    }
  });

  if (Object.keys(finalData).length === 0) {
    alert('请至少填写一个字段');
    return;
  }

  // 构造 [FIELDS] 格式发送给Agent
  let fieldsBlock = '[FIELDS]\n';
  for (const [label, value] of Object.entries(finalData)) {
    fieldsBlock += `${label}=${value}\n`;
  }
  fieldsBlock += '[/FIELDS]';

  const agentMsg = `我已经审核了AI预填结果，以下是最终确认的字段值：\n\n${fieldsBlock}\n\n请根据这些信息生成文档。所有已填字段都已确认，无需再追问。`;

  // 退出协同模式，回到聊天界面
  exitCoFill();

  // 发送给Agent
  appendMessage('user', '确认预填结果，生成文档');
  await callAgent(agentMsg);
}

// ── Send Prefill to Agent ──

export async function sendPrefillToAgent() {
  /** 将预填结果发给Agent继续交互式填写 */
  if (!state.prefillData) return;

  const fields = state.prefillData.fields || [];
  const filled = fields.filter(f => f.value && f.status !== 'empty');
  const emptyFields = fields.filter(f => !f.value || f.status === 'empty');

  let msg = `AI预填结果如下，已填字段请直接使用，未填字段请继续追问：\n\n[FIELDS]\n`;
  for (const f of filled) {
    msg += `${f.label}=${f.value}\n`;
  }
  msg += `[/FIELDS]\n\n`;
  msg += `还需填写：${emptyFields.map(f => f.label).join('、')}`;

  // 退出协同模式，回到聊天界面
  exitCoFill();

  appendMessage('user', '将预填结果交给AI继续');
  await callAgent(msg);
}

// ── Cancel Prefill Review ──
// Legacy: cancelPrefillReview now just calls exitCoFill
export function cancelPrefillReview() { exitCoFill(); }
