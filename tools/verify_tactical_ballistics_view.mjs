// Production React panels/sidecar with an isolated legal four-caliber ship.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_BALLISTICS_OUT??'artifacts/tactical-ballistics-20260915');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[];
let serial=0,scene,packet,browser,page;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_ballistics_browser_fixture','--settlement-dir',store],{windowsHide:true});
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
  await request('system.hello',{client_name:'ballistics.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(['tactical.preparation.open','tactical.preparation.read'].includes(req.method))packet=result;
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  const until=async(predicate,label,timeout=20000)=>{const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),`${label}: ${JSON.stringify(scene?.view?.gunnery?.weapons)}`);};
  const guns=()=>scene.view.gunnery.weapons.filter(g=>g.ship_id===scene.direct_ship_id);
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  for(let i=1;i<=2;i++){await page.getByLabel(`弹药库 ${i} 装载目标`,{exact:true}).fill('100');await saved();}
  for(let i=1;i<=4;i++){
    await page.getByLabel(`武器 ${i} 准备动作`,{exact:true}).selectOption('preload');await saved();
    const option=await page.getByLabel(`武器 ${i} 弹药种类`,{exact:true}).inputValue();assert(option.endsWith('.ordinary'));
  }
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await page.screenshot({path:path.join(out,'four-gun-preparation.png'),fullPage:true});
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  assert.equal(prepared.receipt.ships[0].after.state.magazines.reduce((s,m)=>s+m.quantity,0),65);
  checks.push('Four real gun modules preload caliber-compatible ordinary ammunition using 135 finite ammunition resources across two magazines');
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await page.locator('.tactical-canvas canvas').waitFor();await until(()=>!!scene,'deployment ready');
  assert.equal(guns().length,4);assert.equal(scene.view.ships[1].height_layer,'cloud');
  await button('开始交战').click();await button('火炮').click();
  const shots=[];
  await page.getByText('单炮细调',{exact:true}).click();
  for(const initial of [...guns()]){
    await page.getByLabel('所控火炮',{exact:true}).selectOption(initial.module_id);
    const rain=page.getByLabel('炮弹作用层',{exact:true}).locator('option[value=rain]');
    assert(await rain.evaluate(e=>e.disabled),await rain.evaluate(e=>e.outerHTML));
    await page.getByLabel('炮弹作用层',{exact:true}).selectOption('cloud');
    await until(()=>guns().find(g=>g.module_id===initial.module_id).effective_layer==='cloud','cross-layer command accepted');
    await button('观察云层').click();await button('适应本层').click();
    const targetList=page.getByText('从列表指定目标',{exact:true});
    if(await targetList.locator('..').getAttribute('open')===null)await targetList.click();
    await page.getByRole('button',{name:/^瞄准红方测试舰$/}).click();
    await until(()=>guns().find(g=>g.module_id===initial.module_id).shots>0,`caliber ${initial.ballistics.caliber_mm} fires`);
    await button('停止开火').click();
    const gun=guns().find(g=>g.module_id===initial.module_id);
    assert.equal(gun.ballistics.speed_retention,.7);assert.equal(gun.ballistics.lifetime_s,initial.ballistics.lifetime_s);
    assert.equal(gun.ballistics.effective_speed_mps,initial.ballistics.speed_mps*.7);
    assert(await page.getByLabel('火炮弹道与作用层').innerText().then(t=>t.includes('炮弹穿过友舰')));
    shots.push({caliber:gun.ballistics.caliber_mm,shots:gun.shots,ballistics:gun.ballistics});
  }
  await until(()=>scene.view.gunnery.damage.hits>0,'real cross-layer hits');
  assert(scene.view.gunnery.damage.recent.every(h=>h.height_layer==='cloud'));
  const thirty=()=>guns().find(g=>g.ballistics.caliber_mm===30),beforeManual=thirty().shots;
  await page.getByLabel('火炮模式',{exact:true}).selectOption('manual');
  const clickCenter=async()=>{const rect=await page.locator('.tactical-canvas').boundingBox();const x=rect.x+rect.width/2,y=rect.y+rect.height/2;await page.mouse.move(x,y);await page.waitForTimeout(250);await page.mouse.click(x,y);};
  await button('观察上层').click();await button('适应本层').click();await clickCenter();
  await page.waitForTimeout(150);assert.equal(thirty().shots,beforeManual);
  await button('观察云层').click();await button('适应本层').click();await clickCenter();
  await until(()=>thirty().shots===beforeManual+1,'manual cross-layer click fires once');
  checks.push('Manual canvas fire is enabled only on the selected ammunition layer; switching observation alone does not change that layer');
  await button('暂停交战').click();await until(()=>!scene.status.running,'pause accepted');
  assert(await page.getByLabel('炮弹作用层').isDisabled());
  const step=scene.status.fixed_step;await page.waitForTimeout(200);assert.equal(scene.status.fixed_step,step);
  await page.setViewportSize({width:1280,height:860});
  await button('适应本层').click();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'cross-layer-gunfire.png'),fullPage:true});
  checks.push('All four calibers fire through real controls into the cloud layer; speed is 70%, lifetime unchanged, hits stay in cloud; pause freezes simulation and controls');
  await button('开始交战').click();await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  await button('保存全部战后结果').click();await button('管理战后库存与下一场准备').click();
  await button('准备所选舰船').waitFor();await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  assert.equal(packet.ships[0].state.weapons.length,4);
  assert(packet.ships[0].state.weapons.every(w=>w.recipe_id.endsWith('.ordinary')));
  checks.push('Result saves successfully and all four gun inventories remain caliber-specific in the next preparation');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_BALLISTICS_UI_PASS',checks,shots,
    scope:'Production UI and simulation with an isolated custom four-gun ship. The passive technical enemy starts in cloud. Not a CIWS interception test.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
