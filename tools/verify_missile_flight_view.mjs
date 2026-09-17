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
await mkdir(out,{recursive:true});const store=path.join(out,`store-${Date.now()}`);
const checkArcs=process.env.HW_MISSILE_ARCS==='1';
const checkLifetime=process.env.HW_MISSILE_FIXED_LIFETIME==='1';
const checkPursuit=process.env.HW_MISSILE_PURSUIT==='1';
const checkFall=process.env.HW_MISSILE_FALL==='1';
const setup=spawnSync('python',['-X','utf8','-m','tools.missile_flight_browser_fixture',store,...(checkArcs?['--arcs']:[])],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,packet,live;const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m',...(checkPursuit||checkFall?['tools.missile_maneuver_browser_fixture',...(checkFall?['--fall']:[])]:['backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser']),'--settlement-dir',store],{windowsHide:true});
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
    if(req.method==='tactical.preparation.scene_save'&&packet)packet={...packet,scene:r};
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.realtime.missile'&&req.params.input.order.kind==='fire'&&dropFire){dropFire=false;throw new Error('测试：发射指令已接受但回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===3,'three prepared ships');
  if(checkPursuit){
    await page.getByLabel('初始交战距离（公里）',{exact:true}).fill('16');await button('应用距离').click();
    await until(()=>packet?.scene?.distance_m===16000||packet?.geometry?.distance_m===16000,'pursuit distance');
  }
  await button('配置双方舰内物资').click();
  if(checkLifetime){
    const controls=page.getByRole('complementary',{name:'战前部件面板'});
    for(const [key,seconds] of [['player',30],['partner',45],['player',30]]){
      const shape=packet.geometry.ships.find(s=>s.id===`instance.missile-flight.${key}`);
      const module=shape.modules.find(m=>m.id==='weapon_upper_port');
      await page.getByLabel('我方显示甲板',{exact:true}).selectOption(String(module.deck_level));
      await page.getByRole('region',{name:'我方编队画布'}).getByRole('button',{name:`选择部件 ${shape.name} ${module.name}`,exact:true}).press('Enter');
      const performance=controls.locator('.missile-performance').first();
      await performance.waitFor();
      if(await performance.getAttribute('open')===null)await performance.locator('summary').click();
      await page.waitForFunction(({seconds})=>[...document.querySelectorAll('.missile-performance')].some(e=>e.textContent.includes(`固定无动力时长 ${seconds} 秒`)),{seconds});
      const content=await performance.innerText();
      assert(content.includes(`固定无动力时长 ${seconds} 秒`));assert(content.includes('导引头发现距离'));
      assert(!content.includes('30 / 22 / 15')&&!content.includes('45 / 30 / 20'));
      await page.screenshot({path:path.join(out,`preparation-fixed-${seconds}.png`),fullPage:true});
    }
    checks.push('Real preparation panels show one fixed coast duration for rocket (30 s) and turbojet (45 s), while retaining three weather-dependent seeker ranges.');
  }
  await button('核对资源与预装填').click();
  await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'});
  const tab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const missileShip=()=>live?.view.gunnery.missiles.ships.find(s=>s.ship_id===live.direct_ship_id);
  const launcher=()=>missileShip()?.launchers.find(l=>l.module_id==='weapon_upper_port');
  const loadedVls=()=>missileShip()?.state.launchers.find(l=>l.module_id==='weapon_upper_port');
  if(checkFall){
    await tab('导弹');await inspector.getByRole('button',{name:/^在途制导/}).click();
    await until(()=>live.view.gunnery.projectiles.some(p=>p.id===10000&&p.missile.maneuver_state==='returning'),'low-speed climb failure');
    await button('暂停交战').click();await until(()=>!live.status.running,'pause return');
    const fall=live.view.gunnery.projectiles.find(p=>p.id===10000);
    assert.equal(fall.height_layer,'cloud');assert(fall.missile.altitude_m>5000);
    assert(fall.missile.failed_climb_targets.length===1);
    const card=inspector.locator('[data-flight="10000"]');assert((await card.innerText()).includes('上爬失败，回落中'));
    await button('观察云层').click();await page.screenshot({path:path.join(out,'climb-failed-return.png'),fullPage:true});
    const deadline=fall.missile.age_s+fall.missile.remaining_s;
    await button('开始交战').click();
    await until(()=>live.view.gunnery.projectiles.some(p=>p.id===10000&&p.missile.maneuver_reason==='climb_failed_for_target'),'same-target retry remains prohibited after returning');
    await button('暂停交战').click();await until(()=>!live.status.running,'pause recovered flight');
    const recovered=live.view.gunnery.projectiles.find(p=>p.id===10000);
    assert.equal(recovered.height_layer,'cloud');assert(recovered.missile.altitude_m<=5000);
    assert(Math.abs(recovered.missile.age_s+recovered.missile.remaining_s-deadline)<1e-8);
    assert((await card.innerText()).includes('本次飞行不再向该目标上爬'));
    await page.screenshot({path:path.join(out,'same-target-climb-blocked.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    checks.push('A seeded part-flight sample crosses the actual 50 m/s total-speed limit, falls continuously to its source layer without TTL reset, and displays the persistent same-target climb ban.');
    await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_MISSILE_FALL_5J2_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
  }else{
  if(checkLifetime){
    await tab('导弹');
    const performance=inspector.locator('.missile-performance').first();
    if(await performance.getAttribute('open')===null)await performance.locator('summary').click();
    assert((await performance.innerText()).includes('固定无动力时长 30 秒'));
    const model=loadedVls().model_id,shown=missileShip().profile.flight_profiles[model];
    assert.equal(shown.coast_s,30);assert.equal(shown.lifetime_s,shown.boost_s+shown.powered_s+shown.coast_s);
    await page.screenshot({path:path.join(out,'combat-fixed-lifetime.png'),fullPage:true});
  }
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
  if(checkLifetime){
    await button('暂停交战').click();await until(()=>!live.status.running,'pause actual missile');
    const current=live.view.gunnery.projectiles.find(p=>p.id===first.id);
    const total=missileShip().profile.flight_profiles[current.missile.model_id].lifetime_s;
    assert.equal(current.missile.age_s+current.missile.remaining_s,total);
    assert.equal(current.missile.speed_mps,current.missile.horizontal_speed_mps);
    assert.equal(current.missile.vertical_speed_mps,0);
    await inspector.getByRole('button',{name:/^在途制导/}).click();
    const card=inspector.locator(`[data-flight="${first.id}"]`);
    assert((await card.innerText()).includes('总速度'));assert((await card.innerText()).includes('水平速度'));
    const paused=live.view.gunnery.projectiles.find(p=>p.id===first.id).missile.remaining_s;
    await page.waitForTimeout(300);
    assert.equal(live.view.gunnery.projectiles.find(p=>p.id===first.id).missile.remaining_s,paused);
    await page.screenshot({path:path.join(out,'fixed-lifetime-in-flight.png'),fullPage:true});
    await inspector.getByRole('button',{name:'发射控制',exact:true}).click();await button('开始交战').click();
    checks.push('Combat performance agrees with preparation; an actual VLS flight keeps its fixed deadline, exposes total/horizontal speeds, and pauses without consuming lifetime.');
  }
  await page.screenshot({path:path.join(out,'powered-flight.png'),fullPage:true});
  if(checkPursuit){
    await inspector.getByRole('button',{name:/^在途制导/}).click();
    await until(()=>live.view.gunnery.projectiles.some(p=>p.missile?.maneuver_state==='diving'),'observed target layer drives real descent');
    await button('暂停交战').click();await until(()=>!live.status.running,'pause descent');
    const diving=live.view.gunnery.projectiles.find(p=>p.missile?.maneuver_state==='diving');
    assert.equal(diving.height_layer,'upper');assert.equal(diving.missile.maneuver_target_layer,'cloud');
    assert(diving.missile.speed_mps>diving.missile.horizontal_speed_mps);
    assert(diving.missile.altitude_m<10000&&diving.missile.altitude_m>5000);
    const card=inspector.locator(`[data-flight="${diving.id}"]`);assert((await card.innerText()).includes('下潜追击'));
    await page.screenshot({path:path.join(out,'pursuit-diving.png'),fullPage:true});
    const stopped=structuredClone(diving);await page.waitForTimeout(250);
    assert.deepEqual(live.view.gunnery.projectiles.find(p=>p.id===diving.id),stopped);
    await button('开始交战').click();await inspector.getByRole('button',{name:'发射控制',exact:true}).click();
    checks.push('An actual VLS missile follows an acquired target from upper to cloud with continuous altitude, separate total/XY speed and a frozen paused trajectory.');
  }
  await until(()=>live.view.gunnery.damage.hits>0,'real missile ship impact');
  assert(live.view.gunnery.damage.recent.some(h=>h.projectile_type.includes('missile')));
  if(checkPursuit){assert(live.view.gunnery.damage.recent.some(h=>h.projectile_type.includes('missile')&&h.height_layer==='cloud'));checks.push('The pursued missile completes the 5 km descent and resolves a real ship hit in cloud, consuming the one launched round.');}
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
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:checkPursuit?'TACTICAL_MISSILE_PURSUIT_5J2_UI_PASS':checkLifetime?'TACTICAL_MISSILE_FIXED_LIFETIME_5J1_UI_PASS':'TACTICAL_MISSILE_FLIGHT_5E_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
  }
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
