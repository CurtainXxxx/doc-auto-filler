// Parse the existing SPA and check download-link rendering without new npm dependencies.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const scripts = [];
for (const match of source.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
  const src = match[1].match(/src="([^"]+)"/);
  const script = src ? fs.readFileSync(path.join(__dirname, '../web', src[1]), 'utf8') : match[2];
  new Function(script);
  scripts.push(script);
}
assert(scripts.length > 0, 'The page must load application scripts');
assert(source.includes('id="headerProgress"'), 'Preview progress needs a mounted container');
const format = fs.readFileSync(path.join(__dirname, '../web/js/format.js'), 'utf8');
const render = new Function(format + '\nreturn renderMarkdown;')();
const link = render('[下载链接](/download-docx?file_path=test.docx)');
assert(link.includes('href="/download-docx?file_path=test.docx"'));
assert(!render('<img src=x onerror=alert(1)>').includes('<img'));
assert(!render('[x](javascript:alert(1))').includes('href="javascript:'));
console.log('Frontend syntax, local download links, and text escaping passed.');
