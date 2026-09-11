// Real React and production sidecar, with a saved fireproof design in an isolated store.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_H5D_OUT??'artifacts/h5d-ignition-browser-v1');
let backend,browser,page,serial=0,scene,packet;
const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store')],{windowsHide:true});
  backend.stderr.on('data',d=>errors.push(String(d)));
  createInterface({input:backend.stdout}).on('line',line=>{
    const value=JSON.parse(line),p=pending.get(value.request_id);
    if(p){pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));}
  });
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,session_id,expected_revision,method,params})+'\n');
});}
async function hello(){await request('system.hello',{client_name:'h5d.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.commit','tactical.realtime.deploy_prepared']});}
async function stop(){const child=backend;await new Promise(resolve=>{child.once('exit',resolve);child.kill();});}
const url=process.env.HW_H5D_URL??'http://127.0.0.1:1423/e3b-test.html';
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(['tactical.preparation.open','tactical.preparation.read'].includes(req.method))packet=result;
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  const until=async(predicate,message)=>{const end=Date.now()+30000;while(!predicate()&&Date.now()<end)await page.waitForTimeout(80);assert(predicate(),message);};
  await page.goto(url);await button('战前准备').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  const protection=page.getByRole('region',{name:'本舰防火配置',exact:true});await protection.waitFor();
  assert((await protection.innerText()).includes('第 0 层：起火概率降低 50%'));
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');await saved();
  await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');await saved();
  await page.getByLabel('武器 1 弹药种类',{exact:true}).selectOption('recipe.h5d.incendiary');await saved();
  await button('核对资源与预装填').click();
  await page.getByRole('alert').filter({hasText:'特殊货物不足'}).waitFor();assert(await button('保存准备').isDisabled());
  await page.getByLabel('高能燃料装载目标',{exact:true}).fill('5');await saved();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  const state=prepared.receipt.ships[0].after.state;
  assert.equal(state.magazines[0].quantity,45);
  assert.equal(state.cargo.find(c=>c.good_id==='cargo.high_energy_fuel').quantity,4);
  assert.equal(state.weapons[0].recipe_id,'recipe.h5d.incendiary');
  assert.equal(prepared.receipt.supply_after.cargo.find(c=>c.good_id==='cargo.high_energy_fuel').quantity,95);
  await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  checks.push('Fireproof deck shows 50% ignition reduction; missing high-energy fuel blocks preload; one incendiary round costs 5 ammunition + 1 cargo');
  await button('以所选舰为旗舰进入交战').click();
  const firePanel=page.getByRole('region',{name:'交战防火与火情',exact:true});await firePanel.waitFor();
  const gun=()=>scene.view.gunnery.weapons.find(w=>w.ship_id===scene.direct_ship_id);
  await button('开始试航').click();await page.getByLabel('所控火炮',{exact:true}).selectOption(gun().module_id);
  assert.equal(gun().loaded_recipe_id,'recipe.h5d.incendiary');
  await page.getByLabel('下一批装填弹种',{exact:true}).selectOption('recipe.x1a.ordinary');
  await until(()=>gun().selected_recipe_id==='recipe.x1a.ordinary','next ordinary selected');
  assert.equal(gun().loaded_recipe_id,'recipe.h5d.incendiary');
  await page.getByLabel('下一批装填弹种',{exact:true}).selectOption('recipe.h5d.incendiary');
  await until(()=>gun().selected_recipe_id==='recipe.h5d.incendiary','next incendiary selected');
  await button('瞄准红方测试舰').click();
  await until(()=>scene.view.gunnery.damage_control.fires.some(f=>f.ship_id==='ship.web.red'),'actual projectile ignites technical enemy');
  await button('暂停试航').click();
  assert(scene.view.gunnery.damage.recent.some(h=>h.projectile_type==='projectile.h5d.incendiary'));
  assert((await firePanel.innerText()).includes('正在燃烧'));
  const pausedStep=scene.status.fixed_step,pausedFires=structuredClone(scene.view.gunnery.damage_control.fires);
  await page.waitForTimeout(400);assert.equal(scene.status.fixed_step,pausedStep);assert.deepEqual(scene.view.gunnery.damage_control.fires,pausedFires);
  await firePanel.screenshot({path:path.join(out,'fire.png')});
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});
  checks.push('UI next-batch switching preserves loaded rounds; real auto-fired incendiary hits and ignites enemy; all-ship fire panel renders actual fire; pause freezes it');
  await button('结束交战 / 撤离').click();await button('保存全部战后结果').click();
  await page.getByRole('status').filter({hasText:'已保存全部参战舰船'}).waitFor();
  const result=structuredClone(scene.settlement.result);
  const record=result.ships.find(s=>s.after.state.instance_id==='instance.h5d.browser').after;
  assert(result.ships.some(s=>s.after.state.fires?.length));
  assert(result.ships.some(s=>s.changes.some(c=>c.resource==='cargo:cargo.high_energy_fuel'&&c.reason==='reload'&&c.delta<0)));
  await page.getByRole('region',{name:'战后结算与舰船存档',exact:true}).screenshot({path:path.join(out,'settlement.png')});
  await page.goto('about:blank');await stop();start();await hello();scene=null;
  await page.goto(url);await button('战前准备').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();await protection.waitFor();
  assert.deepEqual(packet.ships[0].state,record.state);
  assert.equal(packet.supply.cargo.find(c=>c.good_id==='cargo.high_energy_fuel').quantity,95);
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await firePanel.waitFor();
  assert.equal(scene.view.gunnery.fireproof.find(s=>s.ship_id===scene.direct_ship_id).decks.find(d=>d.deck_level===0).multiplier,.5);
  assert.equal(gun().loaded_recipe_id,record.state.weapons[0].recipe_id);
  checks.push('Settlement records real fire, damage and incendiary resource costs; actual backend restart and reentry preserve player state and finite supply');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'H5D_IGNITION_UI_PASS',checks,result,reentry:scene.view.gunnery.fireproof},null,2));
  console.log(JSON.stringify({status:'H5D_IGNITION_UI_PASS',checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{await browser?.close();if(backend?.exitCode===null)await stop();}
