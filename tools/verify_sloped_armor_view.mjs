// Real React editor + Python sidecar; file grants substitute the native dialog.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir, writeFile, readFile} from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(path.join(process.env.HW_BROWSER_MODULES, 'package.json'));
const {chromium} = require('playwright');
const out = path.resolve(process.env.HW_ARMOR_OUT ?? `artifacts/hull-armor-a2-${Date.now()}`);
await mkdir(out, {recursive: true});
const file = path.join(out, 'hull.json');
let chosenFile = file;
const original = JSON.parse(await readFile('舰艇数据/船壳蓝图夹具/阶段F常规有人战舰船壳.v1.json', 'utf8'));
const ref = {id:'gtw.material.base_armor.armor_steel',version:1};
original.decks = [25,17.5].map((h,level)=>({id:`deck.${level}`,level,is_base:level===0,structure_material:original.decks[0].structure_material,
 regions:[{id:'region.main',vertices_m:[[-h,-h],[h,-h],[h,h],[-h,h]],edge_armor:Array.from({length:4},()=>({material:ref,thickness_m:.1}))}]}));
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
  await request('system.hello', {client_name: 'hull.a2.browser', client_version: '1', supported_interfaces: ['gaotian.web-bridge/v1alpha1'], required_capabilities: ['tactical.preparation.commit']});
  browser = await chromium.launch({channel: 'msedge', headless: true});
  page = await browser.newPage({viewport: {width: 1480, height: 1100}});
  page.on('pageerror', e => errors.push(e.message));
  await page.exposeFunction('__e3b_request', async req => {
    if (req.method === '__choose_file') return request('editor.bind_file', {host_path: chosenFile, mode: req.params.method ?? 'open'}, req.params.session_id ?? null, req.params.expected_revision ?? null);
    const result = await request(req.method, req.params, req.session_id ?? null, req.expected_revision ?? null);
    if (req.method.startsWith('editor.') && result?.draft) lastEditor = result;
    return result;
  });
  await page.goto(process.env.HW_ARMOR_URL ?? 'http://127.0.0.1:1421/e3b-test.html');
  const button = name => page.getByRole('button', {name, exact: true});
  await button('打开船壳文件').click(); await page.getByLabel('本层结构厚度',{exact:true}).waitFor();
  if(process.env.HW_ARMOR_JOINT){
    await page.getByLabel('本层结构厚度',{exact:true}).fill('50');
    await button('全部甲板统一为此厚度').click();
    await waitFor(()=>lastEditor.draft.decks.every(d=>d.structure_thickness_m===.05));
    await page.getByLabel('当前甲板',{exact:true}).selectOption('deck.1');
    await page.getByLabel('本层结构厚度',{exact:true}).fill('30');
    await button('应用本层厚度').click();
    await waitFor(()=>lastEditor.draft.decks[1].structure_thickness_m===.03);
    checks.push('A5 joint design edits base 50 mm / upper 30 mm before adding sloped armor');
  }
  await page.getByLabel('当前甲板',{exact:true}).selectOption('deck.1');
  await button('◈ 装甲设计').click();
  const angle=page.getByLabel('装甲外飘倾角',{exact:true});
  const edgeSelect=page.getByLabel('选择装甲边',{exact:true});
  await angle.waitFor(); assert.equal(await angle.inputValue(),'0');
  const canvas=page.getByRole('region',{name:'船壳二维画布',exact:true});
  await button('适应船壳').click();
  const box=await canvas.boundingBox();
  const scale=Math.max(.5,Math.min(32,(box.width-80)/50,(box.height-80)/50));
  await canvas.click({position:{x:box.width/2+17.5*scale,y:box.height/2}});
  assert.equal(await edgeSelect.inputValue(),'1');
  await angle.selectOption('45'); await button('应用边装甲').click();
  await waitFor(()=>lastEditor.draft.schema==='gaotian.hull/v4alpha1');
  assert(lastEditor.preview.valid);
  assert.deepEqual(lastEditor.draft.decks[1].regions[0].edge_armor.map(a=>a.flare_angle_deg),[0,45,0,45]);
  assert(lastEditor.preview.model.decks[0].compiled_installation_space.armor_blocked_top_cells.length>0);
  checks.push('direct canvas edge pick; explicit v4 migration; mirrored edge update and authoritative blocked lower cells');
  const withFlare=structuredClone(lastEditor.draft);
  await canvas.hover({position:{x:box.width/2,y:box.height/2}});
  await page.mouse.down(); await page.mouse.move(box.x+box.width/2+30,box.y+box.height/2+15); await page.mouse.up();
  assert.deepEqual(lastEditor.draft,withFlare);
  await button('适应船壳').click();
  assert.equal(lastEditor.preview.model.shape_effects.model,'gaotian.hull-shape/a4-v1');
  await page.getByLabel('外形与探测',{exact:true}).locator('summary').click();
  await page.getByText('雷达反射 · 艏向 / 侧向',{exact:true}).waitFor();
  if(process.env.HW_ARMOR_JOINT){
    await page.getByLabel('外形与探测',{exact:true}).locator('summary').click();
    await page.getByLabel('结构与装甲',{exact:true}).locator('summary').click();
    assert.deepEqual(lastEditor.preview.model.hull_configuration.decks.map(d=>d.thickness_mm),[50,30]);
    assert(lastEditor.preview.model.hull_configuration.blocked_exposed_cells>0);
  }
  checks.push('A4 compiled shape values are exposed by the collapsible shape and detection panel');
  await page.screenshot({path:path.join(out,'armor-editor.png'),fullPage:true});
  await page.getByLabel('边装甲厚度',{exact:true}).fill('0');
  assert(await button('应用边装甲').isDisabled());
  await page.getByLabel('边装甲厚度',{exact:true}).fill('100');
  await button('本层所有边统一为此配置').click();
  await waitFor(()=>lastEditor.draft.decks[1].regions[0].edge_armor.every(a=>a.flare_angle_deg===45));
  assert(lastEditor.preview.valid);
  await angle.selectOption('60'); await button('本层所有边统一为此配置').click();
  await waitFor(()=>!lastEditor.preview.valid);
  assert(lastEditor.preview.diagnostics.some(d=>d.code==='hull.armor_flare_unsupported'));
  await button('撤销').click(); await waitFor(()=>lastEditor.preview.valid);
  await button('重做').click(); await waitFor(()=>!lastEditor.preview.valid);
  await button('撤销').click(); await waitFor(()=>lastEditor.preview.valid);
  checks.push('armor-mode drag only pans; zero thickness disables flare; bulk edit, support diagnostic and undo/redo');
  await page.getByLabel('当前甲板',{exact:true}).selectOption('deck.0');
  await page.screenshot({path:path.join(out,'blocked-deck.png'),fullPage:true});
  await button('保存').click(); await page.getByText('与源资源一致',{exact:false}).waitFor();
  const saved=JSON.parse(await readFile(file,'utf8'));
  assert(saved.decks[1].regions[0].edge_armor.every(a=>a.flare_angle_deg===45));
  await button('返回编辑器入口').click(); await button('打开船壳文件').click();
  await page.getByLabel('当前甲板',{exact:true}).selectOption('deck.1');
  await button('◈ 装甲设计').click(); await angle.waitFor();
  assert.equal(await angle.inputValue(),'45');assert(lastEditor.preview.valid);
  checks.push('save and reopen preserve all edge profiles and compiled surfaces');
  const finalMass=lastEditor.preview.model.derived.hull_mass_kg;
  await button('返回编辑器入口').click(); await button('选择船壳并新建舾装').click();
  const outfitCanvas=page.getByRole('region',{name:'舾装二维画布',exact:true});
  await outfitCanvas.waitFor();
  assert(lastEditor.preview.model.layout.armor_geometry.has_flare);
  assert(await outfitCanvas.locator('title').filter({hasText:'上层外飘覆盖'}).count()>0);
  await button('＋ 添加部件').click();
  const ob=await outfitCanvas.boundingBox();
  await outfitCanvas.click({position:{x:ob.width/2,y:ob.height/2}});
  await waitFor(()=>lastEditor.draft.modules.length===1);
  await page.getByRole('tab',{name:'灵烷贮槽',exact:true}).click();
  const os=Math.max(.5,Math.min(32,(ob.width-80)/50,(ob.height-80)/50));
  await outfitCanvas.click({position:{x:ob.width/2,y:ob.height/2-10*os}});
  await waitFor(()=>lastEditor.draft.modules.length===2);
  if(process.env.HW_ARMOR_JOINT)assert(lastEditor.preview.valid,'thinner combined hull needs only one tank');
  else assert(lastEditor.preview.diagnostics.some(d=>d.code==='outfit.insufficient_lift'));
  await outfitCanvas.click({position:{x:ob.width/2,y:ob.height/2+10*os}});
  await waitFor(()=>lastEditor.draft.modules.length===3);
  assert(lastEditor.preview.valid,JSON.stringify(lastEditor.preview.diagnostics));
  await page.getByLabel('舾装画布甲板',{exact:true}).selectOption('deck.1');
  await page.getByLabel('外形与探测',{exact:true}).locator('summary').click();
  if(process.env.HW_ARMOR_JOINT){
    assert.deepEqual(lastEditor.preview.model.hull_configuration.decks.map(d=>d.thickness_mm),[50,30]);
  }
  assert.equal(lastEditor.preview.model.shape_effects.model,'gaotian.hull-shape/a4-v1');
  await page.screenshot({path:path.join(out,'armor-outfit.png'),fullPage:true});
  chosenFile=path.join(out,'outfit.json');
  await button('另存文件').click(); await page.getByText('与源资源一致',{exact:false}).waitFor();
  await button('返回编辑器入口').click(); await button('打开舾装文件').click();
  await outfitCanvas.waitFor(); assert(lastEditor.preview.valid);
  assert.equal(lastEditor.draft.modules.length,3);
  assert(lastEditor.preview.model.layout.armor_geometry.has_flare);
  checks.push(process.env.HW_ARMOR_JOINT ? 'thin structure and sloped armor persist together; one tank already provides lift, second adds reserve; CIC and tanks save and reopen' : 'real outfit canvas shows blocked cells and slope; actual mass requires two lift tanks; install CIC and tanks, save and reopen portable outfit');
  assert.deepEqual(errors, []);
  await writeFile(path.join(out, 'report.json'), JSON.stringify({status: 'PASS', checks, finalMass, scope: 'real browser React + Python backend; native dialog mocked'}, null, 2));
  console.log(JSON.stringify({status: 'PASS', out, checks}));
} catch (error) {
  if (page) await page.screenshot({path: path.join(out, 'failure.png'), fullPage: true});
  console.error(error); process.exitCode = 1;
} finally {
  await browser?.close(); backend.kill();
  for (const p of pending.values()) clearTimeout(p.timer);
}
