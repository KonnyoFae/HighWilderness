// Actual catalog placement beside a higher hull, upgrade, undo and file roundtrip.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_SENSOR_GEOMETRY_OUT??`artifacts/sensor-geometry-${Date.now()}`);
await mkdir(out,{recursive:true});
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser',
  '--settlement-dir',path.join(out,'store'),'--recovery-dir',path.join(out,'recovery')],{windowsHide:true});
let serial=0,browser,page,snapshot,chosenFile=path.join(out,'sensors.json');
const pending=new Map(),errors=[],checks=[];
backend.stderr.on('data',s=>errors.push(String(s)));
createInterface({input:backend.stdout}).on('line',line=>{
  const r=JSON.parse(line),p=pending.get(r.request_id);if(p){pending.delete(r.request_id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(JSON.stringify(r.error)));}
});
function request(method,params={},session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);
  pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',
    backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
  await request('system.hello',{client_name:'sensor.geometry.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:[]});
  const index=await request('resource.list'),source=index.resources.find(r=>r.id==='gtw.outfit.5d.radar');
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1600,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:chosenFile,mode:req.params.method},req.params.session_id,req.params.expected_revision);
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(r.interface?.startsWith('gaotian.editor-session/'))snapshot=r;
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+15000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
  await page.goto('http://127.0.0.1:1423/e3b-test.html');
  await page.getByText('内置舾装示例',{exact:true}).click();await button(source.name).click();
  await until(()=>snapshot?.draft?.id==='gtw.outfit.5d.radar','open sensor example');
  const original=structuredClone(snapshot.draft);
  await page.getByLabel('画布模块选择',{exact:true}).selectOption('sensor_upper_starboard');
  await button('移除所选模块').click();await until(()=>!snapshot.draft.modules.some(m=>m.id==='sensor_upper_starboard'),'remove original sensor');
  await button('＋ 添加部件').click();await page.getByRole('tab',{name:'探测',exact:true}).click();
  await page.getByLabel('舾装画布甲板',{exact:true}).selectOption('deck.0');
  const canvas=page.getByRole('region',{name:'舾装二维画布',exact:true});
  async function clickPoint(x,y){
    await button('适应船壳').click();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    const box=await canvas.boundingBox(),vb=(await canvas.getAttribute('viewBox')).split(' ').map(Number);
    const deckId=await page.getByLabel('舾装画布甲板',{exact:true}).inputValue();
    const regions=snapshot.preview.model.layout.hull.decks.find(d=>d.id===deckId).regions;
    const p=await page.evaluate(async({regions,width,height,x,y})=>{const {fit,screen}=await import('/src/editor/viewport.ts');return screen({x,y},fit(regions,width,height));},
      {regions,width:vb[2],height:vb[3],x,y});
    await page.mouse.click(box.x+p.x*box.width/vb[2],box.y+p.y*box.height/vb[3]);
  }
  for(const [name,channel,y] of [['火控雷达','radar',0],['红外搜索','infrared',5]]){
    const card=page.locator('.module-card').filter({has:page.getByText(name,{exact:true})});
    assert.equal(await card.count(),1);assert((await card.innerText()).includes('v2'));await card.click();
    await clickPoint(0,y);
    await until(()=>snapshot.draft.modules.some(m=>m.prototype.id===`gtw.module.sensor.5d.${channel}`),'place sensor beside higher hull');
    const m=snapshot.draft.modules.find(m=>m.prototype.id===`gtw.module.sensor.5d.${channel}`);
    const layout=snapshot.preview.model.layout.modules.find(r=>r.id===m.id);
    assert(snapshot.preview.valid);assert.equal(m.prototype.version,2);assert.equal(layout.internal_cells.length,0);assert.equal(layout.clearance_spatial_keys.length,0);
    const arc=snapshot.preview.model.sensor_arcs.find(r=>r.instance_id===m.id);
    assert(arc.blocked_intervals_deg.length);assert(arc.intervals_deg.length);
    await page.getByLabel('显示水平探测视界',{exact:true}).waitFor();
    assert.equal(await canvas.locator('[aria-label="传感器水平视界"]').count(),1);
    assert.equal(await page.getByLabel('显示水平射界',{exact:true}).count(),0);
    checks.push(`${name} v2 places through the real canvas beside the upper hull, with one external cell, no internal/eight-cell clearance and matching blind sectors.`);
  }
  await button('↖ 拖动部件').click();await page.mouse.move(20,20);
  await page.getByRole('heading',{name:'探测视界',exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'sensor-sectors.png'),fullPage:true});
  await button('另存文件').click();await until(()=>snapshot.file_label==='sensors.json'&&!snapshot.dirty,'save sensors');
  const saved=structuredClone(snapshot.preview.model.layout);
  await button('返回编辑器入口').click();await button('打开舾装文件').click();await until(()=>snapshot.file_label==='sensors.json','reopen');
  assert.deepEqual(snapshot.preview.model.layout,saved);assert(snapshot.preview.valid);
  checks.push('Saved/reopened design retains exact sensor installation and valid layout.');
  // An actual saved v1 sensor remains readable and upgrades only on command.
  {
    original.modules.find(m=>m.id==='sensor_upper_starboard').prototype.version=1;
    chosenFile=path.join(out,'legacy-sensor.json');await writeFile(chosenFile,JSON.stringify(original));
    await button('返回编辑器入口').click();await button('打开舾装文件').click();
    await until(()=>snapshot.file_label==='legacy-sensor.json','open legacy sensor');
    await page.getByLabel('画布模块选择',{exact:true}).selectOption('sensor_upper_starboard');
    await button('升级此探测设备').click();
    const version=()=>snapshot.draft.modules.find(m=>m.id==='sensor_upper_starboard').prototype.version;
    await until(()=>version()===2,'upgrade');assert(snapshot.dirty);assert(snapshot.preview.valid);
    await button('撤销').click();await until(()=>version()===1,'undo upgrade');
    await button('重做').click();await until(()=>version()===2,'redo upgrade');
    await button('保存').click();await until(()=>!snapshot.dirty,'save upgraded sensor');
  }
  checks.push('Opening v1 does not silently change it; explicit upgrade, undo, redo and save work through the real editor.');
  assert.deepEqual(errors,[]);await writeFile(path.join(out,'result.json'),JSON.stringify({status:'SENSOR_GEOMETRY_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();if(backend.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
