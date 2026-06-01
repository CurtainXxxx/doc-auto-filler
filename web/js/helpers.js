// ── State ──
const state = {
  sessionId: (() => {
    let id = localStorage.getItem('doc_session_id');
    if (!id) {
      id = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2,6);
      localStorage.setItem('doc_session_id', id);
    }
    return id;
  })(),
  isStreaming: false,
  currentDownloadUrl: '',
  currentTemplate: '',
  currentTemplatePath: '',  // 当前模板文件路径（用于获取预览）
  currentDocxPath: '',      // 已生成的docx文件路径（用于打印PDF）
  currentStep: 0,
  currentMode: '',
  fieldData: {},            // field_id → value
  labelToFields: {},        // label → [field_id, ...]  (从后端获取)
  fieldMap: {},             // field_id → {label, table_idx, ...} (从后端获取)
  docPreviewHtml: '',       // 后端返回的文档HTML
  previewRendered: false,
  manuallyEditedFields: {}, // 用户手动编辑过的字段
  prefillData: null,        // 预填结果数据
  prefillMode: false,       // 是否在协同填写模式
};

// ── API Config ──
function detectBaseUrl() {
  const origin = window.location.origin;
  if (origin.startsWith('file://')) return null;
  return origin;
}

const BASE_URL = detectBaseUrl();
state.BASE_URL = BASE_URL;
state.API_URL = BASE_URL ? BASE_URL + '/v1/chat/completions' : null;
state.UPLOAD_URL = BASE_URL ? BASE_URL + '/upload' : null;

function checkFileAccess() {
  if (!state.BASE_URL || !state.API_URL) {
    window.addEventListener('load', () => {
      const body = document.getElementById('previewBody');
      body.innerHTML = `
        <div class="doc-done-card" style="border:2px solid #FDE68A;">
          <h4>请通过 Web 服务访问</h4>
          <p style="margin:12px 0;line-height:2;">
            当前为静态文件预览，无法连接后端服务。<br>
            请在平台点击「部署」，通过部署链接打开此页面。
          </p>
        </div>`;
      document.getElementById('userInput').disabled = true;
      document.getElementById('sendBtn').disabled = true;
    });
    return false;
  }
  return true;
}

// ── Utilities ──
function autoResize(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 100) + 'px';
}

function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

function escapeAttr(str) {
  return String(str).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Markdown Render ──
function renderMarkdown(text) {
  // Strip [FIELDS]...[/FIELDS] blocks from display
  let html = escapeHtml(text.replace(/\[FIELDS\][\s\S]*?\[\/FIELDS\]/g, ''));
  html = html.replace(/\[([^\]]*)\]\((https?:\/\/[^\s"<>]+\.docx[^\s"<>]*)\)/g,
    '<a class="download-link" href="$2" target="_blank">下载文档</a>');
  html = html.replace(/(?<!href=")(https?:\/\/[^\s"<>]+\.docx[^\s"<>]*)/g,
    '<a class="download-link" href="$1" target="_blank">下载文档</a>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/^- (.+)$/gm, '<div style="padding-left:12px;opacity:0.9;">· $1</div>');
  html = html.replace(/^\d+\. (.+)$/gm, '<div style="padding-left:12px;">$1</div>');
  html = html.replace(/\n/g, '<br>');
  return html;
}

export { state, detectBaseUrl, checkFileAccess, autoResize, escapeHtml, escapeAttr, renderMarkdown };
