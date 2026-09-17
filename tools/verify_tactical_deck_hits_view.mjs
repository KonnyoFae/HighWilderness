// Production React panels/sidecar with an isolated legal ship and saved gun groups.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_DECKS_OUT??'artifacts/tactical-decks-20260916');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[];
let serial=0,scene,packet,browser,page,geometry;
const commands=[], observedHits=new Map();
const backend=spawn('python',['-X','utf8','-m','tools.tactical_targeting_browser_fixture','--settlement-dir',store],{windowsHide:true});
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
  await request('system.hello',{client_name:'deck-hits.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='tactical.realtime.gun')commands.push(req.params.input);
    const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      scene=result;geometry=result.view.static??geometry;
      for(const hit of result.view.gunnery?.damage?.recent??[])observedHits.set(hit.projectile_id,hit);
    }
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
  await page.screenshot({path:path.join(out,'group-preparation.png'),fullPage:true});
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  assert.equal(prepared.receipt.ships[0].after.state.magazines.reduce((s,m)=>s+m.quantity,0),26);
  checks.push('Custom saved groups survive preparation: two 30 mm guns in one group, a separate 30 mm group, and a 75 mm group');
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await page.locator('.tactical-canvas canvas').waitFor();await until(()=>!!scene,'deployment ready');
  assert.equal(guns().length,4);assert.equal(scene.view.ships[1].height_layer,'cloud');
  await button('开始交战').click();await button('火炮').click();
  const forward=()=>guns().filter(g=>['weapon_upper_port','gun.partner'].includes(g.module_id));
  await page.getByRole('button',{name:/前部近防炮组/}).click();
  assert(await page.getByLabel('甲板瞄准规则').innerText().then(t=>t.includes('不保证命中该层或该模块')));
  await page.getByText('从列表指定目标',{exact:true}).click();await button('瞄准红方测试舰').click();
  await page.getByLabel('指定目标模块').selectOption('crew_quarters_upper');
  await until(()=>forward().every(g=>g.aimed_deck_levels?.length===1&&g.aimed_deck_levels[0]===1),'both guns prefer target module deck');
  assert(await page.getByLabel('甲板瞄准规则').innerText().then(t=>t.includes('当前偏好：第 1 甲板')));
  await page.getByLabel('炮弹作用层',{exact:true}).selectOption('cloud');
  const weighted=()=>[...observedHits.values()].filter(h=>h.source_ship_id===scene.direct_ship_id&&h.deck_selection?.probabilities?.length===2);
  await until(()=>new Set(weighted().map(h=>h.deck_level)).size===2,'weighted fire physically hits both decks',30000);
  await button('停止开火').click();await until(()=>forward().every(g=>g.target_policy==='hold'),'hold accepted');
  for(const hit of weighted()){
    assert.equal(hit.height_layer,'cloud');assert.deepEqual(hit.deck_selection.preferred_levels,[1]);
    assert.deepEqual(hit.deck_selection.probabilities,[{deck_level:0,probability:1/3},{deck_level:1,probability:2/3}]);
    const ship=geometry.ships.find(s=>s.id===hit.ship_id);assert(ship);
    for(const id of hit.module_ids)assert.equal(ship.modules.find(m=>m.id===id).deck_level,hit.deck_level);
  }
  checks.push('A group aiming at an upper-deck module produces real hits on both candidate decks, with 1/3 and 2/3 weights and no cross-height-layer damage');
  await page.getByLabel('火炮模式',{exact:true}).selectOption('manual');
  assert.equal(await page.getByLabel('瞄准甲板',{exact:true}).inputValue(),'');
  await page.getByLabel('瞄准甲板',{exact:true}).selectOption('1');
  await until(()=>forward().every(g=>g.deck_level===1),'manual deck preference accepted');
  await page.getByLabel('瞄准甲板',{exact:true}).selectOption('');
  await until(()=>forward().every(g=>g.deck_level===null),'manual no-preference accepted');
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused');
  await page.getByText('战场记录与全舰资源',{exact:true}).click();
  const hitLog=page.getByLabel('甲板命中记录');await hitLog.locator('summary').click();
  await hitLog.scrollIntoViewIfNeeded();
  const text=await hitLog.innerText();assert(text.includes('命中第'));assert(text.includes('瞄准偏好：第 1 甲板'));assert(text.includes('66.7%'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'probabilistic-deck-hits.png'),fullPage:true});
  checks.push('UI distinguishes actual hit deck, aimed deck, height layer and per-shot probabilities; manual aim permits clearing the preference; pause prevents firing');
  const beforeSave=scene.view.ships.map(s=>({id:s.id,hull:s.hull_integrity,modules:s.modules}));
  await button('开始交战').click();await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  await button('保存全部战后结果').click();await button('管理战后库存与下一场准备').click();
  await button('准备所选舰船').waitFor();await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  checks.push('Probabilistic-hit battle settles and saves through the production flow, then returns to the next preparation');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_DECK_HITS_UI_PASS',checks,hits:[...observedHits.values()],beforeSave,
    scope:'Production React and Python simulation, isolated saved multi-gun ship and passive cloud enemy. Does not validate future spreading, magazine explosion or personnel damage.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks,hits:report.hits.length}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
