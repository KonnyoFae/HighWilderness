// Actual React and Python acceptance; all test state is in an isolated store.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_DEFENSE_OUT??`artifacts/tactical-defense-5g-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const setup=spawnSync('python',['-X','utf8','-m','tools.defense_browser_fixture',store],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,live,packet;const pending=new Map(),errors=[],checks=[];
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
async function hello(){await request('system.hello',{client_name:'defense.5g.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.countermeasure']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1500,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));let dropMode=true;
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.realtime.fire_control'&&req.params?.input?.kind==='sensor_mode'&&dropMode){dropMode=false;throw new Error('test: acknowledged sensor command reply lost');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===2,'two real prepared fleets');
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'}),tab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const own=()=>live?.direct_ship_id,guns=()=>live?.view.gunnery;
  const ship=()=>guns()?.missiles.ships.find(s=>s.ship_id===own()),observation=()=>guns()?.observation.ships.find(s=>s.ship_id===own());
  await tab('导弹');await page.getByLabel('作战发射器',{exact:true}).waitFor();
  assert(await page.getByLabel('自动发射',{exact:true}).isChecked());
  await page.screenshot({path:path.join(out,'automatic-launcher.png'),fullPage:true});
  await tab('设备');
  const computer=inspector.locator('.observation-card').filter({has:page.getByText('指挥机',{exact:true})});
  await computer.getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>observation()?.devices.find(d=>d.module_id==='fire_control')?.mode==='off','computer off');
  const radar=inspector.locator('.observation-card').filter({has:page.getByText('火控雷达',{exact:true})});
  await radar.getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>observation()?.devices.find(d=>d.module_id==='sensor_upper_starboard')?.mode==='off','standalone radar off');
  assert.equal(observation().devices.filter(d=>d.integrated).length,2);
  await page.screenshot({path:path.join(out,'integrated-devices.png'),fullPage:true});
  await tab('导弹');
  await until(()=>ship()?.launchers.some(l=>l.shots>0),'integrated launcher fires on actual enemy missile without computer');

  await until(()=>guns().projectiles.some(p=>p.ship_id===own()&&p.missile?.interceptor),'interceptor actually flying');
  await page.locator('[data-flight]').first().scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'interceptor-flight.png'),fullPage:true});
  await page.getByLabel('自动发射',{exact:true}).click();await until(()=>ship().state.launchers[0].auto_fire===false,'automatic fire switched off');
  const victim=observation().contacts.find(c=>c.kind==='missile'&&c.valid&&c.defense_weapon_ids?.includes('weapon_upper_port'));
  assert(victim);
  await page.getByLabel('导弹发射目标',{exact:true}).selectOption(String(victim.id));
  await until(()=>ship().launchers[0].target_id===victim.id,'integer manual target assigned');
  await button('单发导弹').click();await until(()=>ship().launchers[0].shots>=2,'manual interceptor fires with automatic disabled');
  assert.equal(ship().state.launchers[0].auto_fire,false);
  checks.push('Manual integer projectile assignment and one-shot firing work without changing the automatic switch.');
  await until(()=>guns().point_defense.recent.some(e=>e.source_ship_id===own()&&e.weapon_id==='weapon_upper_port'),'actual interceptor impact');
  const impact=guns().point_defense.recent.find(e=>e.source_ship_id===own()&&e.weapon_id==='weapon_upper_port');
  assert.equal(impact.durability_before-impact.durability_after,6);
  checks.push('Real both-fleet preparation and automatic interceptor flight; integrated radar/computer works with standalone radar and command computer off; actual impact removes six durability.');
  await tab('设备');
  const integrated=inspector.locator('.observation-card').filter({has:page.getByText('自持火控近程拦截发射器',{exact:true})});
  await integrated.getByRole('button',{name:'关闭内置传感器',exact:true}).click();
  await until(()=>observation().devices.find(d=>d.module_id==='weapon_upper_port').sensor_enabled===false,'independent sensor switch');
  assert.equal(observation().devices.find(d=>d.module_id==='weapon_upper_port').mode,'active');assert.equal(dropMode,false);
  await integrated.getByRole('button',{name:'开启内置传感器',exact:true}).click();
  await until(()=>observation().devices.find(d=>d.module_id==='weapon_upper_port').sensor_enabled===true,'sensor resumed');
  await tab('火控');await page.screenshot({path:path.join(out,'fire-control.png'),fullPage:true});
  checks.push('Integer projectile targets render without errors; dedicated sensor switch is independent from weapon mode.');
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const saved=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.defense.player').state;
  assert(saved.missiles.launchers.find(l=>l.module_id==='weapon_upper_port').ready.length<8);
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.defense.player').state;
  assert.deepEqual(restored,saved);checks.push('Finite spent interceptors and CIWS stocks settle for both fleets, survive return and backend restart.');
  assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_DEFENSE_5G_UI_PASS',checks,impact},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
