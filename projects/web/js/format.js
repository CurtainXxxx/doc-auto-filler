// ── Markdown Render ──
function renderMarkdown(text) {
  // Strip [FIELDS]...[/FIELDS] blocks from display
  let html = escapeHtml(String(text ?? '').replace(/\[FIELDS\][\s\S]*?\[\/FIELDS\]/g, ''));
  // Same-origin links are returned by the default local storage backend.
  html = html.replace(/\[([^\]]*)\]\((\/download-docx\?[^\s"<>]+)\)/g,
    '<a class="download-link" href="$2" target="_blank">下载文档</a>');
  html = html.replace(/(?<!href=")(\/download-docx\?[^\s"<>]+)/g,
    '<a class="download-link" href="$1" target="_blank">下载文档</a>');
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

// ── Helpers ──
function escapeHtml(text) {
  return String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function escapeAttr(str) {
  return escapeHtml(str);
}
