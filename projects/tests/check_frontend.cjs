// Parse the existing SPA and check download-link rendering without new npm dependencies.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
for (const match of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  new Function(match[1]);
}
const start = source.indexOf('function renderMarkdown');
const end = source.indexOf('// ── Helpers', start);
const render = new Function('escapeHtml', source.slice(start, end) + '\nreturn renderMarkdown;')(
  text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
);
const link = render('[下载链接](/download-docx?file_path=test.docx)');
assert(link.includes('href="/download-docx?file_path=test.docx"'));
assert(!render('<img src=x onerror=alert(1)>').includes('<img'));
console.log('Frontend syntax, local download links, and text escaping passed.');
