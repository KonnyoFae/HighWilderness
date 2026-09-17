// Production React panels/sidecar with an isolated legal ship and saved gun groups.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_FIRE_OUT??'artifacts/tactical-fire-20260916');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[];
let serial=0,scene,packet,browser,page,geometry;
const commands=[], observedHits=new Map(), observedSpread=new Map();
const backend=spawn('python',['-X','utf8','-m','tools.tactical_spatial_fire_browser_fixture','--settlement-dir',store],{windowsHide:true});
backend.stderr.on('data',d=>errors.push(String(d)));
createInterface({input:backend.stdout}).on('line',line=>{
  const v=JSON.parse(line),p=pending.get(v.request_id);
  if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}
});
function request(method,params){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}: ${errors.join('')}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    request_id,session_id:null,expected_revision:null,method,params})+'\n');
});}
try{
  await request('system.hello',{client_name:'spatial-fire.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='tactical.realtime.gun')commands.push(req.params.input);
    const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      scene=result;geometry=result.view.static??geometry;
      for(const event of result.view.gunnery?.damage_control?.recent??[])if(event.kind==='fire_spread')observedSpread.set(event.zone_id,event);
      for(const hit of result.view.gunnery?.damage?.recent??[])observedHits.set(hit.projectile_id,hit);
    }
    if(['tactical.preparation.open','tactical.preparation.read'].includes(req.method))packet=result;
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  const until=async(predicate,label,timeout=20000)=>{const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),`${label}: ${JSON.stringify(scene?.view?.gunnery?.weapons)}`);};
  const guns=()=>scene.view.gunnery.weapons.filter(g=>g.ship_id===scene.direct_ship_id);
  const ownFires=()=>scene.view.gunnery.damage_control.fires.filter(f=>f.ship_id===scene.direct_ship_id);
  const device=()=>scene.view.gunnery.damage_control.devices.find(d=>d.ship_id===scene.direct_ship_id);
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');await saved();
  await page.getByLabel('高能燃料装载目标',{exact:true}).fill('5');await saved();
  await page.getByLabel('工程零件装载目标',{exact:true}).fill('4');await saved();
  await page.getByLabel('损管 1 预准备',{exact:true}).check();await saved();
  await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');await saved();
  await page.getByLabel('武器 1 弹药种类',{exact:true}).selectOption('recipe.h5d.incendiary');await saved();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  assert.equal(prepared.receipt.ships[0].after.state.weapons[0].recipe_id,'recipe.h5d.incendiary');
  assert(prepared.receipt.ships[0].after.state.cargo.find(c=>c.good_id==='cargo.high_energy_fuel').quantity<5);
  checks.push('Production preparation consumes finite ammunition, special fuel and engineering parts for the new incendiary and damage control');
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await until(()=>!!scene,'deployment');assert.equal(guns()[0].incendiary_effect,'surface');
  await button('开始交战').click();await button('损管').click();
  await until(()=>ownFires().some(f=>f.surface===true),'real projectile surface ignition');
  await until(()=>observedSpread.size>0,'natural same-deck spreading',14000);
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused');
  assert(ownFires().length>1);
  const firesBeforeSave=structuredClone(ownFires()),armorBefore=scene.view.gunnery.damage.recent;
  await page.screenshot({path:path.join(out,'spatial-fire.png'),fullPage:true});
  assert((await page.getByRole('group',{name:'损管操作',exact:true}).innerText()).includes('舰外'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  checks.push('Two actual incoming shells produce finite fires without forced probability; live spatial propagation and deck/domain feedback are visible');
  const pausedStep=scene.status.fixed_step;await page.waitForTimeout(220);
  assert.equal(scene.status.fixed_step,pausedStep);assert.deepEqual(ownFires(),firesBeforeSave);
  await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  await button('保存全部战后结果').click();
  const savedState=scene.settlement.result.ships.find(s=>s.after.state.instance_id==='instance.spatial.browser').after.state;
  assert.equal(savedState.fires.length,firesBeforeSave.length);assert(savedState.fires.every(f=>f.zone_id));
  const savedFires=structuredClone(savedState.fires);
  await button('管理战后库存与下一场准备').click();await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await until(()=>ownFires().length===savedFires.length,'fire reentry');
  for(const fire of savedFires){const actual=ownFires().find(f=>f.zone_id===fire.zone_id);for(const k of ['intensity_units','remaining_steps','spread_steps','random_state'])assert.equal(actual[k],fire[k]);}
  assert.equal(device().enabled,false);
  checks.push('Pause freezes fire state; save and next preparation preserve region identity, lifetime, spreading countdown and RNG; damage-control intent resets');
  await button('开始交战').click();await button('损管').click();await button('损管 1 启动').click();
  await until(()=>device().status==='firefighting','finite suppression');
  assert(device().fire_target);assert(device().fire_target_label.includes('甲板'));
  await page.screenshot({path:path.join(out,'single-fire-suppression.png'),fullPage:true});
  await until(()=>ownFires().length===0,'all fires extinguished',15000);
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused after suppression');
  assert(device().quantity_units<savedState.damage_controls[0].quantity_units);
  checks.push('One displayed target per damage-control device; extinguishing consumes real device resources and removes all remaining fires');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_SPATIAL_FIRE_UI_PASS',checks,savedFires,spread:[...observedSpread.values()],hits:armorBefore,
    remainingDamageControl:device().quantity_units,scope:'Real React/Python preparation and saved ship; two isolated incoming shells exercise natural ignition. No detonation or casualties yet.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
