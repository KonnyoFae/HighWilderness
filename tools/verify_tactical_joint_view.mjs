// Real four-ship preparation and combat UI; isolated finite stocks and one declared tank-failure fixture.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_JOINT_OUT??`artifacts/tactical-joint-5h-${Date.now()}`),store=path.join(out,'store');
await mkdir(out,{recursive:true});
const setup=spawnSync('python',['-X','utf8','-m','tools.joint_combat_fixture',store],{encoding:'utf8',windowsHide:true});assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,live,packet;const pending=new Map(),errors=[],checks=[];
const metrics={peak_projectiles:0,peak_missiles:0,peak_response_bytes:0,peak_live_response_bytes:0,overload_seen:false,shots:new Map(),interceptions:new Map(),shells:new Set(),effects:new Map(),shared:false,descent:false};
function start(fixture=true){
  backend=spawn('python',['-X','utf8','-m',...(fixture?['tools.joint_combat_fixture',store,'--serve']:['backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store])],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function stop(){if(backend?.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
async function hello(){await request('system.hello',{client_name:'joint.5h.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.countermeasure']});}
const until=async(fn,label,ms=30000)=>{const end=Date.now()+ms;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),`${label}: ${JSON.stringify(live?.status)}`);};
const report=()=>({...metrics,shots:[...metrics.shots.values()],interceptions:[...metrics.interceptions.values()],shells:[...metrics.shells],effects:[...metrics.effects.values()]});
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1500,height:1050}});page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      live=r;const g=r.view.gunnery;
      metrics.peak_response_bytes=Math.max(metrics.peak_response_bytes,Buffer.byteLength(JSON.stringify(r)));
      if(!r.settlement)metrics.peak_live_response_bytes=Math.max(metrics.peak_live_response_bytes,Buffer.byteLength(JSON.stringify(r)));
      metrics.overload_seen ||= r.status.pause_reason==='overload';
      if(g){
        metrics.peak_projectiles=Math.max(metrics.peak_projectiles,g.projectiles.length);metrics.peak_missiles=Math.max(metrics.peak_missiles,g.projectiles.filter(p=>p.missile).length);
        g.missiles.recent?.filter(e=>e.kind==='fired').forEach(e=>metrics.shots.set(e.projectile_id,e));
        g.point_defense.recent.forEach(e=>metrics.interceptions.set(JSON.stringify(e),e));
        g.weapons.filter(w=>w.shots>0).forEach(w=>metrics.shells.add(w.module_id));
        g.electronic_warfare.effects.forEach(e=>metrics.effects.set(e.id,e));
        metrics.shared ||=g.observation.ships.some(s=>s.contacts.some(c=>c.valid&&c.sources.some(x=>x.includes('/'))));
        metrics.descent ||=r.view.ships.some(s=>s.id==='ship.ew.ally'&&s.descent);
      }
    }
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===4,'four prepared ships');
  assert(packet.scene.sides.every(s=>s.ships.length===2));assert.equal(packet.scene.distance_m,16000);
  await page.screenshot({path:path.join(out,'both-fleets.png'),fullPage:true});
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'}),tab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const fleet=page.getByRole('navigation',{name:'战场舰艇'});
  const ally=()=>live.view.ships.find(s=>s.id==='ship.ew.ally');
  await fleet.getByRole('button',{name:/电子对抗测试舰 · ally/}).click();await tab('火控');
  const target=inspector.locator('.observation-card').filter({has:page.getByText('电子对抗测试舰 · enemy',{exact:true})});
  await target.getByRole('button',{name:'分配导弹',exact:true}).click();
  await page.getByLabel('作战发射器',{exact:true}).waitFor();
  await button('将 电子对抗测试舰 · enemy 分配给此发射器').click();
  await until(()=>live.view.gunnery.missiles.ships.find(s=>s.ship_id==='ship.ew.ally').launchers[0].target_id==='ship.ew.enemy','fire control handoff retains selected target');
  await page.getByText('飞行性能与高度层差异',{exact:true}).click();assert(await page.getByText('同层参考射程',{exact:true}).isVisible());
  await page.screenshot({path:path.join(out,'model-performance.png'),fullPage:true});await button('单发导弹').click();
  await until(()=>live.view.gunnery.projectiles.some(p=>p.ship_id==='ship.ew.ally'&&p.missile),'ally missile flight');
  await inspector.getByRole('button',{name:/在途制导/}).click();await page.locator('[data-flight]').first().waitFor();
  assert.equal(await page.getByLabel('作战发射器',{exact:true}).count(),0);
  await page.screenshot({path:path.join(out,'flight-feedback.png'),fullPage:true});
  checks.push('Both sides have two saved ships; fire-control assignment opens launch controls with the chosen target; performance and flight have separate visible views.');
  await tab('设备');await button('投放小型箔条').click();
  await until(()=>live.view.gunnery.electronic_warfare.devices.some(d=>d.ship_id==='ship.ew.ally'&&d.shots>0),'player electronic countermeasure');
  await until(()=>metrics.descent,'declared ally tank failure starts real descent',60000);
  await tab('损管');await button('损管 1 启动').click();
  await until(()=>live.view.gunnery.damage_control.devices.some(d=>d.ship_id==='ship.ew.ally'&&d.emergency_progress>.1),'real tank repair');
  await page.screenshot({path:path.join(out,'joint-repair.png'),fullPage:true});
  await until(()=>!ally().descent&&ally().modules.find(m=>m.id==='lift_tank').durability>=25,'tank restored while combat continues');
  assert.equal(ally().height_layer,'upper');await button('损管 1 关闭').click();
  await until(()=>metrics.interceptions.size>0,'actual defense contacts',30000);
  await button('暂停交战').click();
  assert(metrics.shots.size>=3);assert(metrics.shells.has('joint.gun'));assert(metrics.shared);
  assert([...metrics.interceptions.values()].some(e=>e.weapon_id==='defense.ciws'));
  assert([...metrics.effects.values()].some(e=>e.ship_id==='ship.ew.enemy'));
  assert([...metrics.effects.values()].some(e=>e.ship_id==='ship.ew.ally'));
  const stockBefore=live.view.gunnery.missiles.ships.map(s=>({ship_id:s.ship_id,state:s.state}));
  checks.push('Real missiles, automatic defense contacts, 50mm gunfire, player and AI EW and data-link sharing coexist; a destroyed tank is rescued by a player order and finite damage-control resources.');
  await page.setViewportSize({width:1280,height:860});await tab('导弹');await inspector.getByRole('button',{name:/在途制导/}).click();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:path.join(out,'compact-flight.png'),fullPage:true});
  await inspector.getByRole('button',{name:'装填与导弹库',exact:true}).click();await page.getByRole('region',{name:'战斗导弹后勤'}).waitFor();
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const saved=(await request('tactical.preparation.scene_read',{})).ships;assert.equal(saved.length,4);
  const restoredAlly=saved.find(s=>s.instance_id==='instance.ew.ally');assert(restoredAlly.state.damage_controls[0].quantity_units<100000);
  await stop();start(false);await hello();assert.deepEqual((await request('tactical.preparation.scene_read',{})).ships,saved);
  checks.push('All four result records and finite missile/EW/repair costs survive save, return to preparation and a normal backend restart.');
  assert.deepEqual(errors,[]);assert.equal(metrics.overload_seen,false);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_JOINT_5H_UI_PASS',checks,scope:'Four ships; production engine and UI. One explicit tank failure at 20 simulated seconds, no synthetic missile/impact or repair completion. This is not a 15–30 ship stress test.',metrics:report(),stockBefore},null,2));console.log(JSON.stringify({out,checks,peak_projectiles:metrics.peak_projectiles,peak_missiles:metrics.peak_missiles,peak_live_response_bytes:metrics.peak_live_response_bytes,overload_seen:metrics.overload_seen}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());await writeFile(path.join(out,'debug.json'),JSON.stringify({metrics:report(),live},null,2));}throw e;}
finally{await browser?.close();await stop();}
