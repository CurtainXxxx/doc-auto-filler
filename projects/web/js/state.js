// ── State ──
// 会话 ID 持久化到 localStorage，刷新/重开页面后复用同一会话
// 用户可通过"新建对话"按钮清除旧会话
const STORAGE_KEY = 'edu_form_session_id';
let sessionId = localStorage.getItem(STORAGE_KEY);
if (!sessionId) {
    sessionId = newSessionId();
    localStorage.setItem(STORAGE_KEY, sessionId);
}
let isStreaming = false;
let currentDownloadUrl = '';
let currentTemplate = '';
let currentTemplatePath = '';  // 当前模板文件路径（用于获取预览）
let currentDocxPath = '';      // 已生成的docx文件路径（用于打印PDF）
let currentStep = 0;
let currentMode = '';
let fieldData = {};            // field_id → value
let labelToFields = {};        // label → [field_id, ...]  (从后端获取)
let fieldMap = {};             // field_id → {label, table_idx, ...} (从后端获取)
let docPreviewHtml = '';       // 后端返回的文档HTML
let previewRendered = false;
let manuallyEditedFields = {}; // 用户手动编辑过的字段

// ── API ──
function detectBaseUrl() {
  const origin = window.location.origin;
  if (origin.startsWith('file://')) return null;
  return origin;
}
const BASE_URL = detectBaseUrl();
const API_URL = BASE_URL ? BASE_URL + '/v1/chat/completions' : null;
const UPLOAD_URL = BASE_URL ? BASE_URL + '/upload' : null;

if (!BASE_URL || !API_URL) {
  window.addEventListener('load', () => {
    const body = document.getElementById('previewBody');
    body.innerHTML = `
      <div class="doc-done-card" style="border:2px solid #FDE68A;">
        <h4>请通过 Web 服务访问</h4>
        <p style="margin:12px 0;line-height:2;">
          当前为静态文件预览，无法连接后端服务。<br>
          请启动本地服务，通过 http://localhost:5000/web/ 打开此页面。
        </p>
      </div>`;
    document.getElementById('userInput').disabled = true;
    document.getElementById('sendBtn').disabled = true;
  });
}

let prefillData = null;      // 预填结果数据
let prefillMode = false;     // 是否在协同填写模式

let previewRequestId = 0;
let previewTemplatePath = '';

function newSessionId() {
  return 'session_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

function fieldText(value) {
  return value == null ? '' : String(value);
}

function hasFieldValue(value) {
  return fieldText(value).trim().length > 0;
}

function previewProgress() {
  const ids = Object.keys(fieldMap);
  const filled = ids.filter(id => hasFieldValue(fieldData[id])).length;
  return {total: ids.length, filled, percent: ids.length ? Math.round(filled / ids.length * 100) : 0};
}

function manualFields() {
  return Object.entries(fieldMap).map(([id, info]) => {
    // Template existing_value may be a signature/date/percent placeholder.
    // Only accepted form data belongs in editable inputs.
    const value = fieldText(fieldData[id]);
    return {
      field_id: id, label: info.label || info.raw_label || id,
      raw_label: info.raw_label || info.label || id, value,
      confidence: hasFieldValue(value) ? 1 : 0, source: '',
      status: hasFieldValue(value) ? 'confirmed' : 'empty',
    };
  });
}

function resetDocumentState() {
  currentDownloadUrl = currentTemplate = currentTemplatePath = currentDocxPath = '';
  currentStep = 0;
  currentMode = '';
  fieldData = {};
  fieldMap = {};
  labelToFields = {};
  manuallyEditedFields = {};
  docPreviewHtml = '';
  previewRendered = false;
  previewTemplatePath = '';
  previewRequestId++;
  prefillData = null;
  prefillMode = false;
}
