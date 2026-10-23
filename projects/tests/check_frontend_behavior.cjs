// Exercise legacy user flows without a framework or network/model calls.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.join(__dirname, '../web');

function element() {
  const classes = new Set();
  const children = new Map();
  return {
    style: {}, dataset: {}, textContent: '', innerHTML: '', value: '', disabled: false,
    classList: {
      add: (...names) => names.forEach(n => classes.add(n)),
      remove: (...names) => names.forEach(n => classes.delete(n)),
      contains: name => classes.has(name),
      toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
    },
    appendChild() {}, remove() {}, focus() {},
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, element());
      return children.get(selector);
    },
    querySelectorAll() { return []; },
  };
}

function loadPage() {
  const nodes = new Map();
  const stored = new Map();
  const document = {
    selections: {}, activeElement: null, head: element(),
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, element());
      return nodes.get(id);
    },
    querySelector: selector => document.getElementById(selector),
    querySelectorAll: selector => document.selections[selector] || [],
    createElement: element, addEventListener() {},
  };
  const context = vm.createContext({
    document, console, CSS: {escape: s => s},
    window: {location: {origin: 'http://localhost:5000'}, addEventListener() {}},
    localStorage: {getItem: k => stored.get(k) || null, setItem: (k, v) => stored.set(k, v), removeItem: k => stored.delete(k)},
    confirm: () => true, alert: message => { throw new Error(`Unexpected alert: ${message}`); },
    setTimeout, clearTimeout, setInterval, clearInterval,
    TextDecoder, AbortController,
    fetch: async () => { throw new Error('Unexpected network call'); },
  });
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  for (const tag of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    const src = tag[1].match(/src="([^"]+)"/);
    const name = src ? src[1] : 'inline.js';
    vm.runInContext(src ? fs.readFileSync(path.join(root, name), 'utf8') : tag[2], context, {filename: name});
  }
  return {context, document, run: code => vm.runInContext(code, context)};
}

const tests = {
  'new chat uses one session and clears document state': async ({run}) => {
    run("fieldData={old:'value'}; currentDownloadUrl='old'; prefillData={fields:[]}; startNewChat()");
    assert(run('sessionId === localStorage.getItem(STORAGE_KEY)'));
    assert(run('Object.keys(fieldData).length === 0 && !currentDownloadUrl && prefillData === null'));
  },
  'field updates preserve zero, false and explicit clearing': async ({run}) => {
    run("fieldMap={zero:{},no:{},clear:{}}; fieldData={clear:'old'}; applyPreciseFieldValues({zero:0,no:false,clear:''})");
    assert.equal(run("JSON.stringify(fieldData)"), '{"clear":"","zero":"0","no":"false"}');
  },
  'manual form keeps distinct IDs with the same label': async ({run}) => {
    run("fieldMap={A:{label:'日期'},B:{label:'日期'}}; renderCoFillManual()");
    assert.equal(run('prefillData.fields.length'), 2);
  },
  'template placeholders are not accepted as manually filled values': async ({run}) => {
    run("fieldMap={A:{label:'比例',existing_value:'%'},B:{label:'签名日期',existing_value:'年 月 日'}}; renderCoFillManual()");
    assert(run("prefillData.fields.every(field => field.value === '' && field.status === 'empty')"));
    assert(run("document.getElementById('cofillSubmitBtn').disabled"));
  },
  'cofill edits update canonical values and preview': async ({run, document}) => {
    const preview = element();
    preview.classList.add('editable');
    document.selections['[data-field-id="A"]'] = [preview];
    run("prefillData={fields:[{field_id:'A',value:'old'}]}; fieldData={A:'old'}");
    const card = element();
    const input = {dataset: {fieldId:'A'}, value:'new', closest: () => card};
    run('onCoFillFieldInput')(input);
    assert.equal(run('fieldData.A'), 'new');
    assert.equal(preview.textContent, 'new');
    input.value = '';
    run('onCoFillFieldInput')(input);
    assert.equal(run('fieldData.A'), '');
    assert.equal(preview.textContent, '');
    assert(preview.classList.contains('empty'));
  },
  'cofill generation submits field IDs without duplicate-label loss': async ({run, context, document}) => {
    document.selections['#cofillFieldsBody input[data-field-id]'] = [
      {dataset:{fieldId:'A',label:'日期'},value:'2026-01-01'},
      {dataset:{fieldId:'B',label:'日期'},value:'2026-02-02'},
    ];
    let payload;
    context.fetch = async (url, options) => {
      payload = JSON.parse(options.body);
      return {ok:true, json: async () => ({success:true,download_url:'/download-docx?file_path=sample.docx'})};
    };
    run("prefillData={fields:[]}; currentTemplatePath='template.docx'");
    await run('submitPrefillReview()');
    assert.deepEqual(payload.field_values, {A:'2026-01-01',B:'2026-02-02'});
    assert.equal(run('currentDownloadUrl'), '/download-docx?file_path=sample.docx');
    assert.equal(run('isStreaming'), false);
  },
  'late preview responses cannot replace the newly selected template': async ({run, context}) => {
    const pending = [];
    context.fetch = () => new Promise(resolve => pending.push(resolve));
    const first = run("fetchDocPreview('first.docx')");
    const second = run("fetchDocPreview('second.docx')");
    pending[1]({ok:true,json:async () => ({success:true,html:'second',field_map:{B:{label:'姓名'}}})});
    await second;
    pending[0]({ok:true,json:async () => ({success:true,html:'first',field_map:{A:{label:'姓名'}}})});
    await first;
    assert.equal(run('docPreviewHtml'), 'second');
    assert.equal(run('Object.keys(fieldMap).join()'), 'B');
  },
  'generated preview uses the same canonical value updater': async ({run, context}) => {
    context.fetch = async () => ({ok:true,json:async () => ({success:true,field_map:{A:{existing_value:0}}})});
    run("fieldMap={A:{label:'成绩'},B:{label:'日期'}}");
    await run("refreshPreviewFromGeneratedDoc('generated.docx')");
    assert.equal(run('fieldData.A'), '0');
    assert.equal(run('Object.keys(fieldMap).length'), 2);
  },
  'SSE field IDs take precedence over label and free-text guesses': async ({run, context}) => {
    const tool = {success:true,filled_field_values:{A:'first',B:'second'},filled_data:{日期:'wrong'}};
    const events = [
      {choices:[{delta:{role:'tool',content:JSON.stringify(tool)}}]},
      {choices:[{delta:{content:'日期：wrong\n'+'正常答复'.repeat(20)}}]},
    ];
    const chunk = new TextEncoder().encode(events.map(e => 'data: '+JSON.stringify(e)+'\n\n').join('')+'data: [DONE]\n\n');
    let sent = false;
    context.fetch = async () => ({ok:true,body:{getReader:() => ({
      read:async () => sent ? {done:true} : (sent=true,{done:false,value:chunk}),
      releaseLock() {},
    })}});
    run("fieldMap={A:{},B:{}}; labelToFields={日期:['A','B']}");
    await run("callAgent('填写两个日期')");
    assert.equal(run('fieldData.A'), 'first');
    assert.equal(run('fieldData.B'), 'second');
  },
};

(async () => {
  let failed = 0;
  for (const [name, test] of Object.entries(tests)) {
    try { await test(loadPage()); console.log(`PASS ${name}`); }
    catch (error) { failed++; console.error(`FAIL ${name}: ${error.message}`); }
  }
  if (failed) process.exitCode = 1;
  else console.log(`${Object.keys(tests).length} frontend behavior checks passed.`);
})();
