// Browser upload queue regression without a DOM/browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
async function run(files, responses) {
  let submit;
  const button = {disabled: false}, progress = {}, result = {};
  const form = {
    querySelector: selector => selector === 'button' ? button : {files},
    addEventListener: (_, callback) => { submit = callback; },
  };
  const sent = [];
  let active = 0;
  const sandbox = {
    document: {querySelector: selector => ({form, '#progress': progress, '#result': result})[selector]},
    crypto: {randomUUID: () => String(sent.length)},
    FormData: class {
      constructor() { this.pdfs = [...files]; }
      delete(key) { assert.equal(key, 'pdf'); this.pdfs = []; }
      append(key, file) { assert.equal(key, 'pdf'); this.pdfs.push(file); }
    },
    fetch: async (_, options) => {
      assert.equal(active++, 0, 'PDFs must run sequentially');
      assert.equal(options.body.pdfs.length, 1, 'Each request must contain exactly one PDF');
      sent.push(options.body.pdfs[0].name);
      await Promise.resolve();
      active--;
      const reply = responses.shift();
      if (reply instanceof Error) throw reply;
      return {ok: true, json: async () => reply};
    },
    setInterval: () => 1, clearInterval: () => {},
  };
  vm.runInNewContext(fs.readFileSync('app.js', 'utf8'), sandbox);
  await submit({preventDefault() {}});
  assert.equal(button.disabled, false);
  return {sent, result: JSON.parse(result.textContent), progress: progress.textContent};
}
(async () => {
  const multiple = await run([{name:'a.pdf'}, {name:'bad.pdf'}, {name:'c.pdf'}],
    [{status:'needs_review'}, new Error('offline'), {status:'extracted'}]);
  assert.deepEqual(multiple.sent, ['a.pdf', 'bad.pdf', 'c.pdf']);
  assert.equal(multiple.result[1].result.status, 'error');
  assert.equal(multiple.result[2].filename, 'c.pdf');
  assert.match(multiple.progress, /2\/3 processed, 1 failed/);
  const single = await run([{name:'a.pdf'}], [{status:'extracted', data:{}}]);
  assert.equal(single.result.status, 'extracted');
  console.log('Upload queue: sequential requests, failure isolation, single-PDF compatibility passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
