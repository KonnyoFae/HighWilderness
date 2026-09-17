// 5d real three-ship React/Python fire-control acceptance.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_SENSOR_OUT??`artifacts/tactical-sensors-5d-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const checkCalibers=process.env.HW_SENSOR_AMMUNITION_CALIBERS==='1';
const setup=spawnSync('python',['-X','utf8','-m','tools.observation_browser_fixture',store,...(checkCalibers?['--calibers']:[])],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,packet,form,receipt,live,loseAction=true,loseCommit=true;
const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function stop(){const b=backend;if(b.exitCode===null)await new Promise(resolve=>{b.once('exit',resolve);b.kill();});}
async function hello(){await request('system.hello',{client_name:'missiles.5c.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.missile','tactical.realtime.fire_control']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));let dropMode=true;
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(req.method==='tactical.preparation.preview')form=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.realtime.fire_control'&&req.params.input.kind==='mode'&&dropMode){dropMode=false;throw new Error('测试：设备命令已接受但回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===3,'three ships');
  await button('配置双方舰内物资').click();
  const prep=page.getByRole('complementary',{name:'战前部件面板'});
  await prep.getByRole('button',{name:'火炮',exact:true}).click();
  await prep.getByRole('button',{name:'补满本舰全部火炮',exact:true}).first().click();
  await button('核对资源与预装填').click();
  await until(()=>form?.can_commit,'check new ammunition preparation');
  const prepared=form.result.ships.find(s=>s.after.state.instance_id==='instance.observation.player').after;
  assert.equal(prepared.state.weapons.find(w=>w.module_id==='weapon_upper_port').ready_rounds,2000);
  assert.equal(form.result.supply_before.ammunition_resources-form.result.supply_after.ammunition_resources,checkCalibers?43:40);
  assert.equal(prepared.resources.magazines[0].capacity_resources,10000);
  assert.equal(prepared.resources.ammunition_resource_liters,10);
  await prep.getByText(/预计装入 2000 发，消耗 40 点弹药资源/).scrollIntoViewIfNeeded();
  if(checkCalibers){
    for(const [caliber,rounds] of [[50,30],[75,8],[120,1]]){
      assert.equal(prepared.state.weapons.find(w=>w.module_id===`gun.caliber.${caliber}`).ready_rounds,rounds);
      await prep.getByText(`预计装入 ${rounds} 发，消耗 1 点弹药资源。`,{exact:true}).waitFor();
    }
    checks.push('Actual preparation fills 50 mm / 75 mm / 120 mm magazines with 30 / 8 / 1 rounds for one point each; all four guns cost 43 points together.');
    await prep.getByText('预计装入 30 发，消耗 1 点弹药资源。',{exact:true}).scrollIntoViewIfNeeded();
  }
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='保存准备'&&!b.disabled));
  await page.screenshot({path:path.join(out,'ammunition-preparation.png'),fullPage:true});
  checks.push('Actual preparation UI fills the 2,000-round 30 mm magazine for 40 resource points; the standard ammunition store is 10,000 points at 10 litres per point.');
  await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'});
  const selectTab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const player=()=>live?.view.gunnery.observation.ships.find(s=>s.ship_id===live.direct_ship_id);
  await selectTab('火控');await until(()=>player()?.contacts.some(c=>c.valid&&c.kind==='ship'),'live enemy track');
  await inspector.getByRole('button',{name:'锁定此目标',exact:true}).first().click();
  await until(()=>player()?.lock_status==='locked','manual target acquired');
  await page.screenshot({path:path.join(out,'fire-control.png'),fullPage:true});
  checks.push('Three real prepared ships deploy at 10 km. Fire-control page acquires the selected enemy track.');
  await selectTab('设备');
  const radar=inspector.locator('.observation-card').filter({has:page.getByText('火控雷达',{exact:true})});
  const before=live.view.gunnery.observation.command_sequence;
  await radar.getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>player().devices.find(d=>d.module_id==='sensor_upper_starboard').mode==='off','local radar off');
  assert.equal(live.view.gunnery.observation.command_sequence,before+1);
  await until(()=>player().contacts.some(c=>c.valid&&c.sources.some(s=>s.includes('/'))),'shared IR substitutes local radar');
  const links=inspector.locator('.observation-card').filter({has:page.getByText('数据链',{exact:true})});
  await links.nth(0).getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>player().devices.find(d=>d.module_id==='datalink.0').mode==='off','first link disabled');
  assert(player().contacts.some(c=>c.valid));
  await page.screenshot({path:path.join(out,'devices.png'),fullPage:true});
  await links.nth(1).getByRole('button',{name:'关闭',exact:true}).click();
  await until(()=>player().contacts.some(c=>!c.valid),'last source lost');
  await selectTab('火控');await page.screenshot({path:path.join(out,'lost-contact.png'),fullPage:true});
  assert(!player().contacts.some(c=>c.valid));
  checks.push('Lost mode-command reply is resolved by fresh reads without a second command. Friendly IR replaces the disabled local radar; one data-link backup suffices; losing both immediately leaves only stale contacts.');
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const settled=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.observation.player').state;
  assert.equal(settled.modules.find(m=>m.module_id==='sensor_upper_starboard').operating_mode,'off');
  assert(settled.modules.filter(m=>m.module_id.startsWith('datalink.')).every(m=>m.operating_mode==='off'));
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.observation.player').state;
  assert.deepEqual(restored,settled);
  checks.push('Battle result saves all device modes; returning to preparation and restarting the backend preserves them.');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_SENSORS_5D_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
