// Actual React and Python acceptance; all test state is in an isolated store.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_EW_OUT??`artifacts/tactical-ew-5f-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const setup=spawnSync('python',['-X','utf8','-m','tools.ew_browser_fixture',store],{encoding:'utf8',windowsHide:true});
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
async function hello(){await request('system.hello',{client_name:'ew.5f.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.countermeasure']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));let dropDeploy=true;
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.realtime.countermeasure'&&dropDeploy){dropDeploy=false;throw new Error('测试：投放已受理，回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===2,'two real fleets');
  await button('配置双方舰内物资').click();
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='核对资源与预装填'&&!b.disabled));
  await page.getByRole('button',{name:'选择部件 电子对抗测试舰 · player 小型箔条发射器',exact:true}).press('Enter');
  await button('查看本舰此页全部部件').waitFor();
  await button('补满此部件').click();
  await page.getByText(/预计装入 1 发，消耗 0 点弹药资源/).waitFor();
  await page.screenshot({path:path.join(out,'preparation-refill.png'),fullPage:true});
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'});
  const tab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const ew=()=>live?.view.gunnery?.electronic_warfare;
  const own=()=>live?.direct_ship_id;
  await tab('设备');await until(()=>ew()?.devices.filter(d=>d.ship_id===own()).some(d=>d.detected),'radar detects actual enemy missile');
  assert.equal(await inspector.locator('[data-countermeasure]').count(),5);
  await page.screenshot({path:path.join(out,'equipment.png'),fullPage:true});
  await inspector.getByRole('button',{name:'投放小型箔条',exact:true}).click();
  await until(()=>ew()?.effects.some(e=>e.ship_id===own()&&e.kind==='chaff'),'cloud deployed despite lost reply');
  await until(()=>ew()?.devices.find(d=>d.module_id==='countermeasure.chaff.small')?.shots===1,'exactly one discharge');
  const radar=live.view.gunnery.observation.ships.find(s=>s.ship_id===own()).devices.find(d=>d.channel==='radar');
  assert.equal(radar.used,0);
  checks.push('Both-fleet preparation enters real battle; five devices appear; actual chaff blocks radar; lost reply does not double-spend.');
  await inspector.getByLabel('主动诱饵方向',{exact:true}).selectOption('-90');
  await inspector.getByRole('button',{name:'投放主动诱饵',exact:true}).click();
  await until(()=>ew()?.effects.some(e=>e.ship_id===own()&&e.kind==='decoy'),'moving active decoy deployed');
  const decoy=ew().effects.find(e=>e.ship_id===own()&&e.kind==='decoy');assert(decoy.velocity_mps[0]<0);
  await button('聚焦所选舰').click();await page.screenshot({path:path.join(out,'cloud-and-decoy.png'),fullPage:true});
  await tab('导弹');
  const target=live.view.gunnery.observation.ships.find(s=>s.ship_id===own()).contacts.find(c=>c.kind==='ship'&&c.valid);
  assert(target,'IR retains enemy ship contact through chaff');
  await inspector.getByLabel('导弹发射目标',{exact:true}).selectOption(target.id);await button('单发导弹').click();
  await until(()=>live.view.gunnery.projectiles.some(p=>p.ship_id===own()&&p.kind==='missile'),'actual composite missile emerges');
  await until(()=>live.view.gunnery.projectiles.some(p=>p.ship_id===own()&&['memory','rescan'].includes(p.missile?.seeker_state)),'AI dual clouds force memory');
  await inspector.getByLabel('在途导弹制导',{exact:true}).scrollIntoViewIfNeeded();
  await button('适应本层').click();
  await page.screenshot({path:path.join(out,'memory-guidance.png'),fullPage:true});
  assert(await inspector.getByLabel('在途导弹制导',{exact:true}).isVisible());
  assert(ew().recent.some(e=>e.ship_id!==own()&&e.kind==='chaff'));
  checks.push('Direction-selected decoy moves; composite missile actually launches; AI deploys countermeasures and missile enters memory with visible guidance feedback.');
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const saved=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.ew.player').state;
  assert(saved.weapons.find(w=>w.module_id==='countermeasure.chaff.small').ready_rounds<=1);
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.ew.player').state;
  assert.deepEqual(restored,saved);
  checks.push('Both sides settle equipment expenditure, return to preparation and retain exact saved inventory after backend restart.');
  assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_EW_5F_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
