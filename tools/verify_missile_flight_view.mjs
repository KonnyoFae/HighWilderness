// Real React + Python 5e launch, delayed emergence, hit and saved expenditure.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_MISSILE_FLIGHT_OUT??`artifacts/tactical-missile-flight-5e-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const checkArcs=process.env.HW_MISSILE_ARCS==='1';
const setup=spawnSync('python',['-X','utf8','-m','tools.missile_flight_browser_fixture',store,...(checkArcs?['--arcs']:[])],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,packet,live;const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function stop(){if(backend?.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
async function hello(){await request('system.hello',{client_name:'missiles.5e.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.missile','tactical.realtime.fire_control']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));let dropFire=true;
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.realtime.missile'&&req.params.input.order.kind==='fire'&&dropFire){dropFire=false;throw new Error('测试：发射指令已接受但回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===3,'three prepared ships');
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();
  await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'});
  const tab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const missileShip=()=>live?.view.gunnery.missiles.ships.find(s=>s.ship_id===live.direct_ship_id);
  const launcher=()=>missileShip()?.launchers.find(l=>l.module_id==='weapon_upper_port');
  const loadedVls=()=>missileShip()?.state.launchers.find(l=>l.module_id==='weapon_upper_port');
  if(checkArcs){
    await tab('导弹');await button('暂停交战').click();
    await until(()=>!live.status.running,'paused for arc inspection');
    const selection=inspector.getByRole('combobox',{name:'作战发射器',exact:true});
    assert(await selection.isEnabled(),'selection remains available while paused');
    await selection.selectOption('arc.blocked');await button('聚焦所选舰').click();
    const key=page.getByLabel('所选发射器射界',{exact:true});
    await page.waitForFunction(()=>document.querySelector('[aria-label="所选发射器射界"]')?.getAttribute('data-launcher-id')==='arc.blocked');
    assert((await key.innerText()).includes('舰体遮挡'));
    assert(missileShip().launchers.find(l=>l.module_id==='arc.blocked').fire_arc.sectors.some(s=>s.kind==='hull_blocked'));
    await page.screenshot({path:path.join(out,'launcher-blocked.png'),fullPage:true});
    await selection.selectOption('arc.clear');
    await page.waitForFunction(()=>document.querySelector('[aria-label="所选发射器射界"]')?.getAttribute('data-launcher-id')==='arc.clear');
    assert((await key.innerText()).includes('水平可射 360.0°'));
    await selection.selectOption('weapon_upper_port');
    await page.waitForFunction(()=>document.querySelector('[aria-label="所选发射器射界"]')?.textContent?.includes('垂直发射：360°'));
    await page.screenshot({path:path.join(out,'launcher-vls.png'),fullPage:true});
    await button('观察云层').click();assert.equal(await key.getAttribute('data-visible'),'false');
    await button('查看发射器所在层').click();assert.equal(await key.getAttribute('data-visible'),'true');
    await tab('舰况');assert.equal(await key.count(),0);
    await tab('导弹');await selection.selectOption('arc.blocked');
    const initialHeading=live.view.ships.find(s=>s.id===live.direct_ship_id).heading_rad;
    await button('开始交战').click();
    const brake=page.getByRole('checkbox',{name:/自动.*制动/});
    if(await brake.count()&&await brake.first().isChecked())await brake.first().uncheck();
    await button('持续右转').click();
    await until(()=>Math.abs(live.view.ships.find(s=>s.id===live.direct_ship_id).heading_rad-initialHeading)>1e-5,'ship turns with selected launcher');
    await button('执行车钟 / 停止转向').click();await button('暂停交战').click();
    await page.screenshot({path:path.join(out,'launcher-turned.png'),fullPage:true});
    await selection.selectOption('weapon_upper_port');await button('开始交战').click();
    checks.push('Paused launcher selection highlights real partial hull occlusion, a full-clear turret, and 360-degree VLS; layer/tab switches clear the overlay, and hull rotation keeps the selected launcher anchored.');
  }
  await tab('火控');await inspector.getByRole('button',{name:'分配导弹',exact:true}).first().click();
  await inspector.getByRole('button',{name:/将 .* 分配给此发射器/}).click();
  await until(()=>launcher()?.target_id,'target assignment');
  await tab('导弹');assert.equal(await inspector.getByRole('checkbox',{name:'自动发射',exact:true}).isChecked(),false);
  const before=loadedVls().ready.length;
  await inspector.getByRole('button',{name:'单发导弹',exact:true}).click();
  await until(()=>live.view.gunnery.missiles.pending.length===1,'VLS spent and pending');
  assert.equal(loadedVls().ready.length,before-1);
  await page.screenshot({path:path.join(out,'vls-delay.png'),fullPage:true});
  await until(()=>live.view.gunnery.projectiles.some(p=>p.kind==='missile'),'actual missile emergence');
  assert.equal(launcher().shots,1);
  const first=live.view.gunnery.projectiles.find(p=>p.kind==='missile');
  assert(first.trajectory.length);assert(first.missile.age_s<1);
  await page.screenshot({path:path.join(out,'powered-flight.png'),fullPage:true});
  await until(()=>live.view.gunnery.damage.hits>0,'real missile ship impact');
  assert(live.view.gunnery.damage.recent.some(h=>h.projectile_type.includes('missile')));
  checks.push('Fire-control target assignment carries into missile page. Default auto remains off. A lost single-fire receipt does not duplicate a VLS departure; exactly one round is spent, appears after delay and hits the enemy.');
  await inspector.getByRole('button',{name:'在画布指定发射地点',exact:true}).click();
  const canvas=page.getByLabel('战术画布，点击指定导弹发射地点',{exact:true});
  await canvas.click({position:{x:100,y:100}});
  await until(()=>launcher().point_m,'manual canvas coordinate');
  assert.equal(launcher().target_id,null);
  await inspector.getByLabel('导弹作用层',{exact:true}).selectOption('cloud');
  await until(()=>launcher().attack_layer==='cloud','adjacent attack layer');
  await page.screenshot({path:path.join(out,'manual-launch.png'),fullPage:true});
  checks.push('Canvas point assignment clears target assignment; adjacent-layer selection is accepted by the real backend.');
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const saved=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.missile-flight.player').state;
  assert.equal(saved.missiles.launchers.find(l=>l.module_id==='weapon_upper_port').ready.length,before-1);
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.missile-flight.player').state;
  assert.deepEqual(restored,saved);
  checks.push('Battle results save missile expenditure once, return to preparation, and survive a backend restart.');
  assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_MISSILE_FLIGHT_5E_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
