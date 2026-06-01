import { state, checkFileAccess, autoResize } from './helpers.js';
import { sendMessage, handleKeydown, appendMessage, updateStepBar } from './chat.js';
import {
  startAutoFill, triggerTemplateUpload, triggerUpload,
  handleTemplateUpload, handleFileUpload,
  downloadDoc, printDoc, submitManualEdits,
  selectTemplate
} from './preview.js';
import {
  startCoFill, exitCoFill, sendPrefillToAgent, submitPrefillReview,
  cofillSelectBuiltIn, cofillPickTemplate, cofillUploadTemplate, cofillSkipKnowledge
} from './cofill.js';

// ── File Access Check ──
checkFileAccess();

// ── Event Delegation for Contenteditable ──
document.addEventListener('input', function(e) {
  const el = e.target;
  if (el.hasAttribute('data-field-id') && el.hasAttribute('contenteditable')) {
    const fid = el.dataset.fieldId;
    const val = el.textContent.trim();
    state.fieldData[fid] = val;
    state.manuallyEditedFields[fid] = true;

    // Update visual state
    if (el.classList.contains('cell-value')) {
      el.classList.toggle('filled', !!val);
    } else if (el.classList.contains('editable')) {
      el.classList.toggle('empty', !val);
      el.classList.toggle('filled', !!val);
    }

    // Update progress
    const totalFields = Object.keys(state.fieldMap).length;
    let filledCount = 0;
    for (const f in state.fieldMap) {
      if (state.fieldData[f] && state.fieldData[f].trim()) filledCount++;
    }
    const pct = totalFields > 0 ? Math.round(filledCount / totalFields * 100) : 0;
    const barFill = document.querySelector('.bar-fill');
    const barText = document.querySelector('.bar-text');
    if (barFill) barFill.style.width = pct + '%';
    if (barText) barText.textContent = `${filledCount}/${totalFields} 已填写`;
  }
});

// ── Event Delegation for Dynamically-Rendered Buttons ──
document.addEventListener('click', function(e) {
  // Submit manual edits button (rendered in doc preview)
  if (e.target.id === 'submitEditsBtn' || e.target.closest('#submitEditsBtn')) {
    submitManualEdits();
    return;
  }
  // Download/print buttons inside previewActions (rendered in updatePreview)
  const dlBtn = e.target.closest('.dl-btn');
  if (dlBtn) { downloadDoc(); return; }
  const printBtn = e.target.closest('.print-btn');
  if (printBtn) { printDoc(); return; }

  // Template selection links (rendered in updatePreview template-select)
  const tplLink = e.target.closest('.tpl-link');
  if (tplLink && tplLink.dataset.template) {
    selectTemplate(tplLink.dataset.template);
    return;
  }

  // Co-fill template pick cards (rendered in cofillSelectBuiltIn)
  const cofillTpl = e.target.closest('[data-template]');
  if (cofillTpl && cofillTpl.dataset.template) {
    cofillPickTemplate(cofillTpl.dataset.template);
    return;
  }

  // Data-action buttons (co-fill step actions)
  const actionEl = e.target.closest('[data-action]');
  if (actionEl) {
    const actions = {
      cofillSelectBuiltIn,
      cofillUploadTemplate,
      triggerUpload,
      cofillSkipKnowledge,
    };
    if (actions[actionEl.dataset.action]) {
      actions[actionEl.dataset.action]();
      return;
    }
  }
});

// ── Wire Up Static HTML Event Listeners ──
document.addEventListener('DOMContentLoaded', () => {
  // Chat input
  const sendBtn = document.getElementById('sendBtn');
  if (sendBtn) sendBtn.addEventListener('click', () => sendMessage());

  const userInput = document.getElementById('userInput');
  if (userInput) {
    userInput.addEventListener('keydown', (e) => handleKeydown(e));
    userInput.addEventListener('input', (e) => autoResize(e.target));
  }

  // Function buttons
  const autoFillBtn = document.getElementById('funcAutoFill');
  if (autoFillBtn) autoFillBtn.addEventListener('click', startAutoFill);

  const uploadTplBtn = document.getElementById('funcUploadTpl');
  if (uploadTplBtn) uploadTplBtn.addEventListener('click', triggerTemplateUpload);

  const coFillBtn = document.getElementById('funcCoFill');
  if (coFillBtn) coFillBtn.addEventListener('click', startCoFill);

  const uploadBtn = document.getElementById('funcUpload');
  if (uploadBtn) uploadBtn.addEventListener('click', triggerUpload);

  // File inputs
  const templateFileInput = document.getElementById('templateFileInput');
  if (templateFileInput) templateFileInput.addEventListener('change', (e) => handleTemplateUpload(e));

  const fileInput = document.getElementById('fileInput');
  if (fileInput) fileInput.addEventListener('change', (e) => handleFileUpload(e));

  // Co-fill buttons
  const cofillSubmitBtn = document.getElementById('cofillSubmitBtn');
  if (cofillSubmitBtn) cofillSubmitBtn.addEventListener('click', submitPrefillReview);

  // Exit co-fill buttons (both in header and footer)
  document.querySelectorAll('.exit-btn').forEach(btn => {
    btn.addEventListener('click', exitCoFill);
  });

  // Co-fill footer buttons
  const cofillFooter = document.getElementById('cofillFooter');
  if (cofillFooter) {
    cofillFooter.addEventListener('click', (e) => {
      if (e.target.textContent === '交给AI继续') sendPrefillToAgent();
      if (e.target.textContent === '取消') exitCoFill();
    });
  }
});

// ── Init ──
window.addEventListener('load', () => {
  const isNewSession = !localStorage.getItem('doc_session_id_visited');
  if (isNewSession) {
    localStorage.setItem('doc_session_id_visited', '1');
    appendMessage('assistant',
      '您好，请选择操作方式：\n\n' +
      '· 点击下方「内置模板」选择预设模板\n' +
      '· 点击「上传模板」上传Word文档自动识别\n' +
      '· 点击「知识文件上传」批量上传文件提取信息\n' +
      '· 或直接输入消息开始'
    );
  } else {
    appendMessage('assistant', '已恢复上次对话，您可以继续操作。');
  }
  updateStepBar();
});

// ── CSS Animation ──
const style = document.createElement('style');
style.textContent = '@keyframes pulse { 0%,100%{transform:scale(1);box-shadow:var(--shadow)} 50%{transform:scale(1.03);box-shadow:0 0 0 4px rgba(37,99,235,0.1)} }';
document.head.appendChild(style);
