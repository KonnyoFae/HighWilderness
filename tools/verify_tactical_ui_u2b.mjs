// Real editor, preparation, sensing and tactical rendering in isolated storage.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(`artifacts/tactical-ui-u2b-${Date.now()}`),store=path.join(out,'store');
await mkdir(out,{recursive:true});
const fixture=spawnSync('python',['-X','utf8','-m','tools.observation_browser_fixture',store,'--icons'],{encoding:'utf8',windowsHide:true});
assert.equal(fixture.status,0,fixture.stderr);
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store],{windowsHide:true});
let serial=0,live,geometry,editor,packet,page,browser;const pending=new Map(),errors=[],checks=[],commands=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.request_id);if(p){pending.delete(r.request_id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(JSON.stringify(r.error)));}});
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
 const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout: ${method}`));},30000);
 pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
 await request('system.hello',{client_name:'ui.u2b',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.fire_control']});
 browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1366,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('__e3b_request',async req=>{
  if(req.method==='__choose_file')return request('editor.bind_file',{host_path:path.join(store,'player.outfit.json'),mode:req.params.method});
  const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
  if(req.method.startsWith('tactical.realtime.')&&req.method!=='tactical.realtime.read')commands.push(req);
  if(r.interface==='gaotian.realtime-view/e3b-v1alpha1'){live=r;if(r.view.static)geometry=r.view.static;}
  if(r.draft)editor=r;if(req.method==='tactical.preparation.scene_read')packet=r;return r;
 });
 const button=name=>page.getByRole('button',{name,exact:true}),until=async(fn,label,ms=30000)=>{const end=Date.now()+ms;while(!fn()&&Date.now()<end)await page.waitForTimeout(60);assert(fn(),label);};
 await page.goto('http://127.0.0.1:1423/e3b-test.html');await button('打开舾装文件').click();
 const selector=page.getByRole('combobox',{name:'舰艇分类图标',exact:true});await selector.waitFor();assert.equal(await selector.inputValue(),'diamond');
 await selector.selectOption('star');await until(()=>editor?.draft.classification_icon==='star','editor icon command');
 await button('撤销').click();await until(()=>editor.draft.classification_icon==='diamond','icon undo');
 await button('重做').click();await until(()=>editor.draft.classification_icon==='star','icon redo');
 await button('保存').click();await until(()=>!editor.dirty,'icon saved');
 const saved=JSON.parse(await readFile(path.join(store,'player.outfit.json'),'utf8'));assert.equal(saved.outfit.classification_icon,'star');
 await page.screenshot({path:path.join(out,'editor-icon.png'),fullPage:true});
 await button('返回编辑器入口').click();await button('打开舾装文件').click();await selector.waitFor();assert.equal(await selector.inputValue(),'star');
 await button('返回编辑器入口').click();checks.push('Actual editor selects, undoes, redoes, saves and reopens a versioned icon.');
 await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('配置双方舰内物资').waitFor();
 await until(()=>!!packet?.geometry,'preparation geometry');
 assert.equal(packet.geometry.ships.find(s=>s.id==='instance.observation.player').classification_icon,'diamond');
 assert.equal(packet.geometry.ships.find(s=>s.id==='instance.observation.partner').classification_icon,'triangle');
 await page.screenshot({path:path.join(out,'preparation-icons.png'),fullPage:true});
 await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();await button('按当前编队进入交战').click();
 await until(()=>!!live,'deployed');
 const flagship=live.direct_ship_id,ally=geometry.ships.find(s=>s.id!==flagship&&s.side_id===geometry.ships.find(s=>s.id===flagship).side_id).id;
 const enemy=geometry.ships.find(s=>s.side_id!==geometry.ships.find(s=>s.id===flagship).side_id).id;
 const row=id=>page.locator(`[data-fleet-ship="${id}"]`),marker=id=>page.locator(`.tactical-ship-marker[data-ship-id="${id}"]`);
 const canvas=page.locator('.tactical-canvas'), own=()=>live.view.gunnery.observation.ships.find(s=>s.ship_id===flagship);
 await canvas.locator('canvas').waitFor();assert.equal(await marker(enemy).count(),0,'undiscovered enemy marker');
 assert((await marker(flagship).innerText()).includes('◆'));assert((await marker(ally).innerText()).includes('▲'));
 await marker(ally).waitFor({state:'visible'});assert.equal(await marker(ally).getAttribute('data-other-layer'),'true');
 const a=await marker(flagship).boundingBox(),b=await marker(ally).boundingBox();assert(Math.hypot(a.x-b.x,a.y-b.y)>=39,'dense mixed-layer markers overlap');
 await button('开始交战').click();await until(()=>own()?.contacts.some(c=>c.id===enemy&&c.valid),'enemy observed');await marker(enemy).waitFor({state:'visible'});
 const fire=page.getByRole('complementary',{name:'火控',exact:true});await fire.locator('.fire-contact').first().click();await until(()=>own().lock_status==='locked','lock');
 await button('暂停交战').click();await fire.locator('.fire-contact').first().dblclick();await page.waitForTimeout(150);
 const worldPosition=await marker(enemy).getAttribute('data-world-position');assert.deepEqual(JSON.parse(worldPosition),own().contacts.find(c=>c.id===enemy).position_m);
 assert.equal(await row(flagship).getAttribute('aria-pressed'),'true');
 const point=()=>canvas.evaluate(el=>({camera:el.dataset.camera,box:el.getBoundingClientRect().toJSON(),markers:[...el.querySelectorAll('.tactical-ship-marker')].map(m=>m.style.transform)}));
 for(const [width,height] of [[1920,1080],[1366,768]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(150);const before=await point();
  for(const name of ['舰队','火控','舰务']){await button('收起'+name).click();assert.deepEqual(await point(),before);await button('展开'+name).click();assert.deepEqual(await point(),before);}
  await page.screenshot({path:path.join(out,`workspace-${width}.png`),fullPage:true});
 }
 checks.push('Prepared icons reach the battle; overlapping friendly ships on different layers have distinct markers and badges. Unknown enemies stay hidden; two window sizes preserve canvas geometry on panel changes.');
 await button('开始交战').click();
 async function sensorOff(id){
  await row(id).click();const section=fire.locator('.battle-equipment').first();if(await section.getAttribute('open')===null)await section.locator('summary').click();
  const sensor=live.view.gunnery.observation.ships.find(s=>s.ship_id===id).devices.find(d=>d.kind==='sensor');
  await section.locator('.observation-card').filter({has:page.getByText(sensor.name,{exact:true})}).getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>live.view.gunnery.observation.ships.find(s=>s.ship_id===id).devices.find(d=>d.module_id===sensor.module_id).mode==='off','sensor off');
 }
 await sensorOff(flagship);await until(()=>own().contacts.some(c=>c.id===enemy&&c.valid&&c.sources.some(s=>s.includes('/'))),'shared source survives local loss');
 await sensorOff(ally);await row(flagship).click();await until(()=>own().contacts.some(c=>c.id===enemy&&!c.valid),'all sources lost');
 await button('暂停交战').click();await page.waitForTimeout(150);assert.equal(await marker(enemy).getAttribute('data-contact-state'),'lost');
 assert(!JSON.parse(await canvas.getAttribute('data-visible-ships')).includes(enemy),'lost enemy hull still rendered');
 const ghost=await marker(enemy).getAttribute('data-world-position'),fireCount=commands.filter(r=>r.method==='tactical.realtime.gun').length;
 await fire.locator('.fire-contact.lost').first().dblclick();assert.equal(await marker(enemy).getAttribute('data-world-position'),ghost);
 assert.equal(await row(flagship).getAttribute('aria-pressed'),'true');assert.equal(commands.filter(r=>r.method==='tactical.realtime.gun').length,fireCount);
 await page.screenshot({path:path.join(out,'lost-contact.png'),fullPage:true});
 await button('开始交战').click();await until(()=>!own().contacts.some(c=>c.id===enemy),'memory expires',30000);
 await page.waitForTimeout(200);assert.equal(await marker(enemy).count(),0);
 checks.push('Friendly datalink observation survives local sensor loss. Losing every source removes the live hull, keeps a non-targetable ghost at the last sample, and removes the ghost when memory expires.');
 await button('结束本场交战').click();await button('保存全部战后结果').click();await button('结算已保存').waitFor();await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
 assert.equal(packet.geometry.ships.find(s=>s.id==='instance.observation.partner').classification_icon,'triangle');
 checks.push('All-side settlement saves and returns to preparation with icons preserved.');
 assert.deepEqual(errors,[]);await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_UI_U2B_PASS',checks},null,2));console.log(JSON.stringify({out,checks},null,2));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();for(const p of pending.values())clearTimeout(p.timer);if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
