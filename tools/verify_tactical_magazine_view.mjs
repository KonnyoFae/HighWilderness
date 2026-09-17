// Real React, preparation, projectile destruction, finite inventory and save.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_MAGAZINE_OUT??'artifacts/tactical-magazine-20260916');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[];
let serial=0,scene,packet,browser,page;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_magazine_browser_fixture','--settlement-dir',store],{windowsHide:true});
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
  await request('system.hello',{client_name:'magazine.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
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
  const until=async(predicate,label,timeout=20000)=>{const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),label);};
  const explosions=()=>scene?.view?.gunnery?.damage?.magazine_explosions??[];
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');await saved();
  await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');await saved();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  const preparedState=prepared.receipt.ships[0].after.state;
  const expected=preparedState.magazines[0].quantity;
  assert.equal(preparedState.modules.find(m=>m.module_id==='ammunition_magazine').durability_points,100);
  assert(expected>0&&expected<50);
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await button('开始交战').click();await until(()=>explosions().length===1,'actual shell detonation');
  await button('暂停交战').click();await until(()=>!scene.status.running,'pause');
  const event=explosions()[0];assert.equal(event.cause,'projectile');assert.equal(event.ammunition_resources,expected);
  assert.equal(scene.view.gunnery.damage.hits,3);assert.equal(event.deck_level,0);
  const ownGun=scene.view.gunnery.weapons.find(w=>w.ship_id===scene.direct_ship_id);
  assert.equal(ownGun.ammo_resources,0);assert.equal(ownGun.ready_rounds,1);
  assert(event.module_losses.length>0);
  checks.push(`A legal full-health magazine is destroyed by three real swept shells, consumes exactly ${expected} remaining resources, and preserves the already loaded round`);
  await page.screenshot({path:path.join(out,'magazine-blast.png'),fullPage:true});
  await page.getByText('战场记录与全舰资源',{exact:true}).click();
  const log=page.getByLabel('甲板命中记录');await log.locator('summary').click();
  const record=page.getByLabel('弹药库殉爆记录');await record.scrollIntoViewIfNeeded();
  const text=await record.innerText();assert(text.includes(`损失 ${expected} 弹药资源`));assert(text.includes('不连锁殉爆'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'magazine-record.png'),fullPage:true});
  const step=scene.status.fixed_step;await page.waitForTimeout(250);assert.equal(scene.status.fixed_step,step);
  assert.deepEqual(explosions(),[event]);
  checks.push('The battle canvas and damage log show the blast, deck, local damage and finite stock loss; pausing freezes the event and simulation');
  await button('结束本场交战').click();await button('保存全部战后结果').waitFor();await button('保存全部战后结果').click();
  await until(()=>scene.settlement.saved,'save');
  const result=scene.settlement.result.ships.find(s=>s.after.state.instance_id==='instance.magazine.browser');
  assert.equal(result.after.state.magazines[0].quantity,0);
  assert.equal(result.after.state.modules.find(m=>m.module_id==='ammunition_magazine').durability_points,0);
  assert(result.changes.some(c=>c.reason==='magazine_detonation'&&c.delta===-expected));
  checks.push('Battle results save magazine destruction, zero remaining stock and a separately reconciled detonation ledger entry');
  await button('管理战后库存与下一场准备').click();await button('准备所选舰船').waitFor();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await until(()=>scene?.view?.fixed_step===0||scene?.status?.fixed_step===0,'new scene');
  assert.equal(scene.view.gunnery.weapons.find(w=>w.ship_id===scene.direct_ship_id).ammo_resources,0);
  await button('开始交战').click();await until(()=>scene.status.fixed_step>5,'new steps');
  await button('暂停交战').click();assert.equal(explosions().length,0);
  checks.push('The next preparation and battle retain destroyed empty storage without restoring stock or replaying an old explosion');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_MAGAZINE_UI_PASS',checks,event,savedChanges:result.changes,
    scope:'Production UI/Python preparation and save, isolated store, three real technical incoming shells; no forced HP or explosion event.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
