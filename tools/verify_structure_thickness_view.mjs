// Real React editor + Python sidecar; file grants substitute the native dialog.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir, writeFile, readFile} from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(path.join(process.env.HW_BROWSER_MODULES, 'package.json'));
const {chromium} = require('playwright');
const out = path.resolve(process.env.HW_STRUCTURE_OUT ?? `artifacts/hull-structure-a0-${Date.now()}`);
await mkdir(out, {recursive: true});
const file = path.join(out, 'hull.json');
const original = JSON.parse(await readFile('舰艇数据/船壳蓝图夹具/阶段F常规有人战舰船壳.v1.json', 'utf8'));
await writeFile(file, JSON.stringify(original));
const backend = spawn('python', ['-X', 'utf8', '-m', 'backend.high_wilderness_sidecar', '--instance-id', 'backend.e3bbrowser',
  '--settlement-dir', path.join(out, 'store')], {windowsHide: true});
let serial = 0, lastEditor, browser, page;
const pending = new Map(), errors = [], checks = [];
backend.stderr.on('data', d => errors.push(String(d)));
createInterface({input: backend.stdout}).on('line', line => {
  const value = JSON.parse(line), p = pending.get(value.request_id);
  if (p) {pending.delete(value.request_id); clearTimeout(p.timer); value.ok ? p.resolve(value.result) : p.reject(new Error(JSON.stringify(value.error)));}
});
function request(method, params, session_id = null, expected_revision = null) {
  return new Promise((resolve, reject) => {
    const request_id = `req.${++serial}`, timer = setTimeout(() => {pending.delete(request_id); reject(new Error(`Timeout: ${method}`));}, 20000);
    pending.set(request_id, {resolve, reject, timer});
    backend.stdin.write(JSON.stringify({interface: 'gaotian.web-bridge/v1alpha1', kind: 'request', backend_instance_id: 'backend.e3bbrowser',
      request_id, session_id, expected_revision, method, params}) + '\n');
  });
}
async function waitFor(check) {
  const until = Date.now() + 15000;
  while (!await check()) {if (Date.now() > until) throw new Error('Editor state timeout'); await new Promise(r => setTimeout(r, 50));}
}
try {
  await request('system.hello', {client_name: 'hull.a0.browser', client_version: '1', supported_interfaces: ['gaotian.web-bridge/v1alpha1'], required_capabilities: ['tactical.preparation.commit']});
  browser = await chromium.launch({channel: 'msedge', headless: true});
  page = await browser.newPage({viewport: {width: 1480, height: 1100}});
  page.on('pageerror', e => errors.push(e.message));
  await page.exposeFunction('__e3b_request', async req => {
    if (req.method === '__choose_file') return request('editor.bind_file', {host_path: file, mode: req.params.method ?? 'open'}, req.params.session_id ?? null, req.params.expected_revision ?? null);
    const result = await request(req.method, req.params, req.session_id ?? null, req.expected_revision ?? null);
    if (req.method.startsWith('editor.') && result?.draft) lastEditor = result;
    return result;
  });
  await page.goto(process.env.HW_STRUCTURE_URL ?? 'http://127.0.0.1:1421/e3b-test.html');
  const button = name => page.getByRole('button', {name, exact: true});
  const input = page.getByLabel('本层结构厚度', {exact: true});
  await button('打开船壳文件').click(); await input.waitFor();
  assert.equal(await input.inputValue(), '100');
  const oldMass = lastEditor.preview.model.derived.hull_mass_kg;
  await input.fill('50');
  assert(await button('应用本层厚度').isDisabled());
  await page.getByText('以下甲板超过新基底层厚度：', {exact: false}).waitFor();
  await button('全部甲板统一为此厚度').click();
  await waitFor(() => lastEditor.draft.decks.every(d => d.structure_thickness_m === .05));
  assert(lastEditor.preview.valid);
  assert(lastEditor.preview.model.derived.hull_mass_kg < oldMass);
  checks.push('old 100 mm default; base conflict shown and single edit blocked; explicit bulk edit updates authoritative mass');
  await page.getByLabel('当前甲板', {exact: true}).selectOption(original.decks[1].id);
  await waitFor(async () => await input.inputValue() === '50');
  await input.fill('55'); assert(await button('应用本层厚度').isDisabled());
  await input.fill('17'); assert(await button('应用本层厚度').isDisabled());
  await input.fill('30'); await button('应用本层厚度').click();
  await waitFor(() => lastEditor.draft.decks[1].structure_thickness_m === .03);
  assert.equal(lastEditor.draft.decks[0].structure_thickness_m, .05);
  await button('撤销').click(); await waitFor(async () => await input.inputValue() === '50');
  await button('重做').click(); await waitFor(async () => await input.inputValue() === '30');
  checks.push('per-deck input enforces 5 mm steps and base limit; undo/redo preserves independent choices');
  const slider = page.getByLabel('结构厚度滑块', {exact: true});
  await slider.focus(); await slider.press('Home');
  await waitFor(async () => await input.inputValue() === '15');
  await button('应用本层厚度').click(); await waitFor(() => lastEditor.draft.decks[1].structure_thickness_m === .015);
  await page.getByLabel('本层边缘填充', {exact: true}).selectOption('gtw.filling.rack');
  await waitFor(() => lastEditor.draft.decks[1].filling.id === 'gtw.filling.rack');
  assert.equal(lastEditor.draft.schema, 'gaotian.hull/v3alpha1');
  assert.equal(lastEditor.draft.decks[1].structure_thickness_m, .015);
  await page.screenshot({path: path.join(out, 'thickness-editor.png'), fullPage: true});
  await button('保存').click(); await page.getByText('与源资源一致', {exact: false}).waitFor();
  const saved = JSON.parse(await readFile(file, 'utf8'));
  assert.deepEqual(saved.decks.map(d => d.structure_thickness_m), [.05, .015]);
  await button('返回编辑器入口').click(); await button('打开船壳文件').click(); await input.waitFor();
  assert.equal(await input.inputValue(), '50');
  await page.getByLabel('当前甲板', {exact: true}).selectOption(original.decks[1].id);
  await waitFor(async () => await input.inputValue() === '15');
  assert.equal(await page.getByLabel('本层边缘填充', {exact: true}).inputValue(), 'gtw.filling.rack');
  assert(lastEditor.preview.valid);
  checks.push('slider reaches 15 mm; filling keeps v3 thickness; save and reopen retain both layers and filling');
  assert.deepEqual(errors, []);
  await writeFile(path.join(out, 'report.json'), JSON.stringify({status: 'PASS', checks, oldMass, finalMass: lastEditor.preview.model.derived.hull_mass_kg, scope: 'real browser React + Python backend; native dialog mocked'}, null, 2));
  console.log(JSON.stringify({status: 'PASS', out, checks}));
} catch (error) {
  if (page) await page.screenshot({path: path.join(out, 'failure.png'), fullPage: true});
  console.error(error); process.exitCode = 1;
} finally {
  await browser?.close(); backend.kill();
  for (const p of pending.values()) clearTimeout(p.timer);
}
