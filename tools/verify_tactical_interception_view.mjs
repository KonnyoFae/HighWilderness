import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_INTERCEPTION_OUT??'artifacts/tactical-interception-20260916');
await mkdir(out,{recursive:true});
const pending=new Map(),errors=[],checks=[],store=path.join(out,`store-${Date.now()}`);
let serial=0,scene,browser,page;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_interception_browser_fixture','--settlement-dir',store],{windowsHide:true});
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
  await request('system.hello',{client_name:'interception.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{const result=await request(req.method,req.params);if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;return result;});
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  const until=async(predicate,label,timeout=20000)=>{const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),label);};
  const gun=()=>scene?.view?.gunnery?.weapons.find(g=>g.ship_id===scene.direct_ship_id);
  const defense=()=>scene?.view?.gunnery?.point_defense;
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('100');await saved();
  await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');await saved();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await until(()=>!!gun()&&scene.status.fixed_step===0,'initial deployment');
  assert(gun().point_defense);const initial=gun().ready_rounds;
  await button('火炮').click();await page.getByRole('button',{name:/30 毫米近防炮.*1 门/}).click();await button('开始交战').click();
  await until(()=>defense()?.intercepted===1,'six real hits intercept a durable shell');
  await button('暂停交战').click();await until(()=>!scene.status.running,'pause');
  const result=structuredClone(defense()),shots=gun().shots;
  assert.equal(result.hits,6);assert.deepEqual(result.recent.map(e=>e.durability_after),[5,4,3,2,1,0]);
  assert.equal(gun().ready_rounds,initial-shots);assert(!scene.view.gunnery.damage.recent.some(h=>h.ship_id===scene.direct_ship_id));
  assert(!scene.view.gunnery.projectiles.some(p=>p.id===1000));
  await page.getByLabel('自动近防设置').scrollIntoViewIfNeeded();
  await page.getByRole('complementary',{name:'所选舰艇',exact:true}).screenshot({path:path.join(out,'point-defense-controls.png')});
  await page.getByText('战场记录与全舰资源',{exact:true}).click();
  const log=page.getByRole('region',{name:'近防拦截记录',exact:true});await log.locator('summary').click();
  await log.screenshot({path:path.join(out,'interception-record.png')});
  assert((await log.innerText()).includes('耐久 6 → 5'));assert((await log.innerText()).includes('拦截成功'));
  const step=scene.status.fixed_step;await page.waitForTimeout(250);assert.equal(scene.status.fixed_step,step);assert.deepEqual(defense(),result);
  checks.push('A normally prepared 30 mm gun detects and shoots a real incoming 120 mm shell; six swept contacts reduce durability 6 to 0, protecting the ship and consuming actual ready rounds');
  await button('开始交战').click();await button('关闭自动近防').click();await until(()=>!gun().point_defense,'defense toggle off');
  await button('开启自动近防').click();await until(()=>gun().point_defense,'defense toggle on');
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused after toggles');
  checks.push('Production weapon-group controls toggle dedicated defense and ordinary fire; records distinguish partial hits from kills, and pause freezes all changes');
  await button('结束本场交战').click();await button('保存全部战后结果').waitFor();await button('保存全部战后结果').click();
  await until(()=>scene.settlement.saved,'saved ammunition');
  const row=scene.settlement.result.ships.find(s=>s.after.state.instance_id==='instance.interception.browser');
  assert.equal(row.after.state.weapons[0].ready_rounds,initial-shots);
  await button('管理战后库存与下一场准备').click();await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();await until(()=>!!gun()&&scene.status.fixed_step===0,'next deployment');
  assert.equal(gun().ready_rounds,initial-shots);assert.equal(defense().intercepted,0);assert(gun().point_defense);
  checks.push('Settlement and another preparation/deployment preserve ammunition expenditure without restoring fired rounds or carrying over old threats');
  assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const report={status:'TACTICAL_INTERCEPTION_UI_PASS',checks,shots,initial_rounds:initial,remaining_rounds:initial-shots,result,
    scope:'Production React/Python, isolated storage, one technical incoming round; no forced contacts, durability deductions or automatic kill. Not fleet-scale/native-window acceptance.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks,shots}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
