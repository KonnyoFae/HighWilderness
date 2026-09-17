import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_PERSONNEL_OUT??'artifacts/tactical-personnel-20260916');
await mkdir(out,{recursive:true});
const pending=new Map(),errors=[],checks=[],store=path.join(out,`store-${Date.now()}`);
let serial=0,scene,browser,page;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_personnel_browser_fixture','--settlement-dir',store],{windowsHide:true});
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
  await request('system.hello',{client_name:'personnel.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  const until=async(predicate,label,timeout=20000)=>{const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),label);};
  const people=()=>scene?.view?.gunnery?.personnel?.ships.find(s=>s.ship_id===scene.direct_ship_id);
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');await saved();
  await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');await saved();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await until(()=>!!people()&&scene.status.fixed_step===0,'initial deployment');
  assert.equal(people().fit,18);assert.equal(people().wounded,0);
  await button('开始交战').click();await until(()=>people()?.fit===9,'real quarter casualties');
  await button('暂停交战').click();await until(()=>!scene.status.running,'pause');
  assert.equal(people().wounded,8);assert.equal(people().dead,1);
  assert.equal(scene.view.gunnery.damage.hits,6);
  assert(scene.view.gunnery.damage.recent.every(h=>h.module_ids.every(id=>id.startsWith('crew_quarters'))));
  const losses=structuredClone(scene.view.gunnery.personnel.recent),totals=structuredClone(people());
  assert(losses.some(e=>e.deck_level===0)&&losses.some(e=>e.deck_level===1));
  assert.equal(people().modules.find(m=>m.module_id==='cic').requirements[0].assigned,1);
  assert.equal(people().modules.find(m=>m.module_id==='weapon_upper_port').functions.find(f=>f.function_id==='weapon.reload').crew_efficiency,0);
  checks.push('Six actual oblique shell hits destroy only the two crew quarters; 18 fit personnel become 9 fit, 8 wounded and 1 dead, while priority staffing retains CIC control and disables unstaffed manual reloading');
  const panel=page.getByRole('region',{name:'舰上人员',exact:true});
  await panel.getByText('人员类别与岗位',{exact:true}).click();
  assert((await panel.innerText()).includes('可执勤 9'));assert((await panel.innerText()).includes('普通船员'));
  const post=panel.locator('.personnel-post').filter({hasText:'装填人员效能 0%'}).first();
  await post.locator('summary').click();
  await post.scrollIntoViewIfNeeded();
  assert(await post.getByText(/装填人员效能 0%/).isVisible());
  await page.getByRole('complementary',{name:'所选舰艇',exact:true}).screenshot({path:path.join(out,'personnel-staffing.png')});
  await panel.getByText('人员类别与岗位',{exact:true}).click();
  await page.getByText('战场记录与全舰资源',{exact:true}).click();
  const log=page.getByLabel('人员伤亡记录');await log.locator('summary').click();await log.scrollIntoViewIfNeeded();
  assert((await log.innerText()).includes('炮击'));assert((await log.innerText()).includes('阵亡 1'));
  await log.screenshot({path:path.join(out,'personnel-casualties.png')});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const step=scene.status.fixed_step;await page.waitForTimeout(250);assert.equal(scene.status.fixed_step,step);assert.deepEqual(people(),totals);
  checks.push('Production UI distinguishes fit/wounded/dead personnel, per-type requirements, staffing efficiency and actual hit deck/cause; pause freezes all changes');
  await button('结束本场交战').click();await button('保存全部战后结果').waitFor();await button('保存全部战后结果').click();
  await until(()=>scene.settlement.saved,'saved personnel');
  const row=scene.settlement.result.ships.find(s=>s.after.state.instance_id==='instance.personnel.browser');
  assert.equal(row.after.state.crew.reduce((n,v)=>n+v.count,0),9);assert.equal(row.after.state.wounded_aboard,8);
  assert.equal(row.after.state.personnel.statuses.reduce((n,v)=>n+v.dead,0),1);
  const persisted=structuredClone(row.after.state.personnel);
  const change=page.getByRole('region',{name:'人员战后变化',exact:true}).first();await change.screenshot({path:path.join(out,'personnel-settlement.png')});
  await button('管理战后库存与下一场准备').click();await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await until(()=>!!people()&&scene.status.fixed_step===0,'next deployment');
  assert.equal(people().fit,9);assert.equal(people().wounded,8);assert.equal(people().dead,1);
  assert.equal(scene.view.gunnery.personnel.recent.length,0);
  await button('开始交战').click();await until(()=>scene.status.fixed_step>5,'next battle');await button('暂停交战').click();
  assert.equal(people().fit,9);assert.equal(people().wounded,8);assert.equal(people().dead,1);
  checks.push('Save, next preparation and another battle retain fit counts, typed injuries/deaths and fractional carries, with no free treatment, replacement crew or repeated casualty events');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_PERSONNEL_UI_PASS',checks,totals,losses,persisted,
    scope:'Production React/Python in isolated storage; six technical incoming rounds use real swept damage without forced personnel or durability changes.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
