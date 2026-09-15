// Production React panels + real prepared ships and fixed-step authority.
// The isolated fixture uses a 2x external clock and a passive technical enemy.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_HEIGHT_OUT??'artifacts/tactical-height-20260915');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[],calls=[];
let serial=0,scene,browser,page,dropHeightReply=false;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_height_browser_fixture','--settlement-dir',store],{windowsHide:true});
backend.stderr.on('data',d=>errors.push(String(d)));
createInterface({input:backend.stdout}).on('line',line=>{
  const value=JSON.parse(line),p=pending.get(value.request_id);
  if(p){pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));}
});
function request(method,params){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    request_id,session_id:null,expected_revision:null,method,params})+'\n');
});}
try{
  await request('system.hello',{client_name:'height.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.height']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    calls.push(req.method);const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(dropHeightReply&&req.method==='tactical.realtime.height'){dropHeightReply=false;throw new Error('测试：换层回执丢失');}
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true}),canvas=page.locator('.tactical-canvas');
  const until=async(predicate,label,timeout=15000)=>{const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),`${label}: ${JSON.stringify(scene?.status)}`);};
  const direct=()=>scene.view.ships.find(s=>s.id===scene.direct_ship_id);
  const heightCalls=()=>calls.filter(m=>m==='tactical.realtime.height').length;
  await page.goto(process.env.HW_TACTICAL_HEIGHT_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('准备所选舰船').waitFor();
  const library=await request('tactical.preparation.library',{}),source=library.sources.find(s=>s.name.includes('常规有人'));
  await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
  for(let i=1;i<=2;i++){await button('从目录设计添加舰船').click();await page.getByLabel(`选择准备舰船 ${i}`,{exact:true}).waitFor();}
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await page.getByLabel('选择准备舰船 2',{exact:true}).check();
  await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  const options=await page.getByLabel('当前准备舰船').locator('option').evaluateAll(nodes=>nodes.map(n=>n.value));
  await page.getByLabel('当前准备舰船').selectOption(options[0]);
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');
  await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');
  await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();await canvas.locator('canvas').waitFor();
  const fleet=page.getByRole('navigation',{name:'战场舰艇'}).getByRole('button');
  const friendly=scene.view.ships[1].id;
  const baseline=direct().height_navigation.base_duration_s;
  assert(baseline>0);assert.equal(scene.view.ships.length,3);
  assert(await button('前往云层').isDisabled());
  await button('开始交战').click();await button('火炮').click();
  const gun=scene.view.gunnery.weapons.find(g=>g.ship_id===scene.direct_ship_id);
  await page.getByLabel('所控火炮').selectOption(gun.module_id);
  await page.getByRole('button',{name:/^瞄准.+/}).first().click();
  await until(()=>scene.view.gunnery.weapons.find(g=>g.ship_id===scene.direct_ship_id).shots>0,'real gun fires');
  await page.getByLabel('实时车钟').selectOption('quarter');await button('执行车钟 / 停止转向').click();
  await until(()=>direct().speed_mps>0,'real propulsion runs');
  await button('舰况').click();const gunSequence=scene.view.gunnery.command_sequence;
  await button('前往云层').click();
  await until(()=>direct().height_navigation.progress>.015,'height progress advances');
  assert.equal(direct().height_layer,'upper');assert.equal(scene.view.gunnery.command_sequence,gunSequence);
  assert(direct().speed_mps>0);await page.screenshot({path:path.join(out,'height-with-helm.png'),fullPage:true});
  await button('取消换层').click();assert.equal(direct().height_layer,'upper');assert.equal(direct().height_navigation.target_layer,null);
  checks.push('Real prepared flagship starts a timed layer order while propulsion and its automatic gun order remain active; cancellation stops progress without changing the actual layer');
  // Stop gunfire before waiting through long segments; this is a navigation test.
  await button('火炮').click();await button('停止瞄准 / 清除目标').click();await button('舰况').click();
  const beforeLost=heightCalls();dropHeightReply=true;await button('前往雨层').click();
  await until(()=>!dropHeightReply&&direct().height_navigation.target_layer==='rain','lost reply resolves');
  await page.waitForTimeout(250);assert.equal(heightCalls(),beforeLost+1);
  await until(()=>direct().height_navigation.progress>.015,'first segment progressing');
  await button('暂停交战').click();const paused=structuredClone(direct().height_navigation),step=scene.status.fixed_step;
  assert(await button('取消换层').isDisabled());await page.waitForTimeout(200);
  assert.equal(scene.status.fixed_step,step);assert.deepEqual(direct().height_navigation,paused);
  await page.screenshot({path:path.join(out,'height-paused.png'),fullPage:true});
  await button('开始交战').click();
  await fleet.nth(1).click();await button('前往云层').click();
  assert.equal(scene.view.ships.find(s=>s.id===friendly).height_navigation.target_layer,'cloud');
  assert.notEqual(friendly,scene.direct_ship_id);await fleet.nth(2).click();assert.equal(await button('前往云层').count(),0);
  await fleet.nth(0).click();
  await until(()=>direct().height_layer==='cloud'&&direct().height_navigation.next_layer==='rain','first 5km segment completes',90000);
  await button('取消换层').click();assert.equal(direct().height_layer,'cloud');assert.equal(direct().height_navigation.target_layer,null);
  checks.push('A lost command reply is resolved by reading without resending; pause freezes countdown; a second-segment cancellation retains cloud layer; friendly orders keep flagship control and enemy inspection is read-only');
  await button('观察云层').click();await button('适应本层').click();await page.screenshot({path:path.join(out,'cloud-arrival.png'),fullPage:true});
  await button('前往雨层').click();
  await until(()=>direct().height_layer==='rain'&&!direct().height_navigation.target_layer,'rain arrival',90000);
  await until(()=>scene.view.ships.find(s=>s.id===friendly).height_layer==='cloud','friendly cloud arrival',90000);
  await button('暂停交战').click();
  for(const layer of ['上层','云层','雨层']){
    await button(`观察${layer}`).click();await page.waitForTimeout(100);
    assert.equal(await page.locator('.tactical-ship-label:visible').count(),1,`${layer}: one actual ship`);
  }
  await page.setViewportSize({width:1280,height:860});await button('适应本层').click();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'rain-arrival-desktop.png'),fullPage:true});
  assert.equal(direct().height_navigation.base_duration_s,baseline);
  checks.push('All three layers contain real ships after actual sequential fixed-step transitions; observation filters the correct identities and the default desktop layout remains usable');
  // End with an active order to check settlement does not lock the next test.
  await button('开始交战').click();await button('前往上层').click();
  await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  await button('保存全部战后结果').click();await button('管理战后库存与下一场准备').click();
  await button('准备所选舰船').waitFor();assert.equal(await canvas.count(),0);
  checks.push('Ending during active height movement still produces a savable result and returns to preparation without retaining a live canvas or countdown');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_HEIGHT_UI_PASS',checks,base_duration_s:baseline,
    scope:'Two legal prepared friendly ships and one technical enemy; real production simulation and UI. External test clock is 2x and the technical enemy is passive. No player store is used.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
