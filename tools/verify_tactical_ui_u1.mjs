// U1 runs the actual React workspace against an isolated three-ship Python scene.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_UI_U1_OUT??`artifacts/tactical-ui-u1-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const fixture=spawnSync('python',['-X','utf8','-m','tools.observation_browser_fixture',store],{encoding:'utf8',windowsHide:true});
assert.equal(fixture.status,0,fixture.stderr);
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store],{windowsHide:true});
let serial=0,live,geometry,page,browser;const pending=new Map(),errors=[],checks=[],commands=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.request_id);if(p){pending.delete(r.request_id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(JSON.stringify(r.error)));}});
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
 const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout: ${method}`));},30000);
 pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
 await request('system.hello',{client_name:'ui.u1',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.fire_control']});
 browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1366,height:900}});page.on('pageerror',e=>errors.push(e.message));
 await page.exposeFunction('__e3b_request',async req=>{const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
  if(req.method==='tactical.realtime.fire_control'||req.method==='tactical.realtime.gun')commands.push(req);
  if(r.interface==='gaotian.realtime-view/e3b-v1alpha1'){live=r;if(r.view.static)geometry=r.view.static;}return r;
 });
 const button=name=>page.getByRole('button',{name,exact:true}),until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(60);assert(fn(),label);};
 await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();await button('按当前编队进入交战').click();await button('开始交战').click();
 const own=()=>live.view.gunnery.observation.ships.find(s=>s.ship_id===live.direct_ship_id);
 await until(()=>own()?.contacts.some(c=>c.kind==='ship'&&c.valid),'live fire-control target');
 const fire=page.getByRole('complementary',{name:'火控',exact:true}),service=page.getByRole('complementary',{name:'舰务',exact:true});
 await fire.locator('.fire-contact').first().click();await until(()=>own().lock_status==='locked','real lock command');
 const lockCount=commands.filter(r=>r.method==='tactical.realtime.fire_control').length;
 assert(!commands.some(r=>r.method==='tactical.realtime.gun'),'selecting fire-control contact must not command every weapon');
 await button('暂停交战').click();await page.waitForTimeout(300);
 const canvas=page.locator('.tactical-canvas');await canvas.locator('canvas').waitFor();
 const snapshot=()=>canvas.evaluate(el=>({box:el.getBoundingClientRect().toJSON(),camera:JSON.parse(el.dataset.camera),ships:[...el.querySelectorAll('.tactical-ship-marker')].map(m=>({id:m.dataset.shipId,position:m.style.transform}))}));
 const first=await snapshot();assert.equal(await page.locator('.battle-fleet button').count(),2);assert.equal(await page.locator('.tactical-ship-label').count(),0);
 for(const name of ['舰队','火控','舰务']){await button('收起'+name).click();assert.deepEqual(await snapshot(),first,`${name} close changes canvas`);await button('展开'+name).click();assert.deepEqual(await snapshot(),first,`${name} open changes canvas`);}
 await button('展开旗舰操纵').click();assert.deepEqual(await snapshot(),first);await button('收起旗舰操纵').click();
 await fire.getByRole('button',{name:'导弹',exact:true}).click();assert.deepEqual(await snapshot(),first);assert(await fire.getByRole('region',{name:'火控目标列表'}).isVisible());
 await service.getByRole('button',{name:'货舱',exact:true}).click();const flagship=live.direct_ship_id,ally=geometry.ships.find(s=>s.id!==flagship&&s.side_id===geometry.ships.find(s=>s.id===flagship).side_id).id;
 const row=id=>page.locator(`[data-fleet-ship="${id}"]`);
 await row(ally).click();assert.deepEqual(await snapshot(),first);await fire.getByRole('button',{name:'火炮',exact:true}).click();await service.getByRole('button',{name:'损管',exact:true}).click();
 await row(flagship).click();assert.equal(await fire.getByRole('button',{name:'导弹',exact:true}).getAttribute('aria-pressed'),'true');assert.equal(await service.getByRole('button',{name:'货舱',exact:true}).getAttribute('aria-pressed'),'true');
 await fire.locator('.fire-contact').first().dblclick();const targetFocus=await snapshot();assert.equal(targetFocus.camera.scale,first.camera.scale);assert.equal(await row(flagship).getAttribute('aria-pressed'),'true');assert.equal(commands.filter(r=>r.method==='tactical.realtime.fire_control').length,lockCount);
 await row(ally).dblclick();const allyFocus=await snapshot();assert.equal(allyFocus.camera.scale,first.camera.scale);await row(ally).dblclick();const allyDetail=await snapshot();assert(allyDetail.camera.scale>allyFocus.camera.scale);
 checks.push('Actual fire-control lock works; inspecting/focusing targets retains the own operating ship without implicit gun commands.');
 checks.push('Panel open/close and tabs preserve canvas bounds, camera and all stationary marker coordinates; per-ship service/weapon pages restore. Fleet double-click pans first and zooms only on repetition.');
 for(const [width,height] of [[1920,1080],[1366,768]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(150);const before=await snapshot();
  for(const name of ['舰队','火控','舰务']){await button('收起'+name).click();assert.deepEqual(await snapshot(),before);await button('展开'+name).click();assert.deepEqual(await snapshot(),before);}
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,`workspace-${width}.png`),fullPage:true});
 }
 await button('结束本场交战').click();await button('保存全部战后结果').click();await button('结算已保存').waitFor();await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
 checks.push('1920×1080 and 1366×768 geometry checks pass; battle settlement saves and returns to preparation.');
 assert.deepEqual(errors,[]);await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_UI_U1_PASS',checks},null,2));console.log(JSON.stringify({out,checks},null,2));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();for(const p of pending.values())clearTimeout(p.timer);if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
