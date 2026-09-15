// Production UI and backend, isolated stores and explicit damage/clock fixture.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_DESCENT_OUT??'artifacts/tactical-descent-20260915');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[];
let serial=0,scene,browser,page,dropReply=false;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_descent_browser_fixture','--settlement-dir',store],{windowsHide:true});
backend.stderr.on('data',d=>errors.push(String(d)));
createInterface({input:backend.stdout}).on('line',line=>{
  const v=JSON.parse(line),p=pending.get(v.request_id);
  if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}
});
function request(method,params){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    request_id,session_id:null,expected_revision:null,method,params})+'\n');
});}
try{
  await request('system.hello',{client_name:'descent.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.damage_control']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  let dcCalls=0;
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(req.method==='tactical.realtime.damage_control'){
      dcCalls++;
      if(dropReply){dropReply=false;throw new Error('测试：损管回执丢失');}
    }
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  const until=async(predicate,label,timeout=20000)=>{
    const end=Date.now()+timeout;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);
    assert(predicate(),`${label}: ${JSON.stringify(scene?.status)}`);
  };
  const ship=n=>scene.view.ships[n],dc=n=>scene.view.gunnery.damage_control.devices.find(d=>d.ship_id===ship(n).id);
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('准备所选舰船').waitFor();
  const library=await request('tactical.preparation.library',{}),source=library.sources.find(s=>s.name.includes('常规有人'));
  await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
  for(let i=1;i<=2;i++){await button('从目录设计添加舰船').click();await page.getByLabel(`选择准备舰船 ${i}`,{exact:true}).waitFor();}
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await page.getByLabel('选择准备舰船 2',{exact:true}).check();
  await button('准备所选舰船').click();await page.getByLabel('当前准备舰船').waitFor();
  const options=await page.getByLabel('当前准备舰船').locator('option').evaluateAll(nodes=>nodes.map(n=>n.value));
  for(const value of options){
    await page.getByLabel('当前准备舰船').selectOption(value);
    await page.getByLabel('工程零件装载目标',{exact:true}).fill('4');await saved();
    await page.getByLabel('损管 1 预准备',{exact:true}).check();await saved();
  }
  await page.getByLabel('当前准备舰船').selectOption(options[0]);
  await button('核对资源与预装填').click();await button('保存准备').click();
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await page.locator('.tactical-canvas canvas').waitFor();await until(()=>!!scene,'deployment is ready');
  const fleet=page.getByRole('navigation',{name:'战场舰艇'}).getByRole('button');
  const flagship=scene.direct_ship_id;
  await button('开始交战').click();await until(()=>scene.view.ships.every(s=>s.descent),'all tanks really destroyed');
  assert.equal(ship(0).physical_status,'operational');assert.equal(scene.available,true);assert(!scene.view.gunnery.ending);
  await page.getByRole('status',{name:'强制下坠'}).waitFor();assert(await button('取消换层').isDisabled());
  await page.getByLabel('实时车钟').selectOption('quarter');await button('执行车钟 / 停止转向').click();
  await until(()=>ship(0).speed_mps>0,'flagship remains controllable while falling');
  await button('损管').click();dropReply=true;const before=dcCalls;
  await button('损管 1 启动').click();await until(()=>dc(0).emergency_progress>.15,'real emergency progress');
  assert.equal(dcCalls,before+1);assert.equal(ship(0).modules.find(m=>m.id==='lift_tank').durability,0);
  await button('暂停交战').click();const paused=structuredClone({ship:ship(0),device:dc(0),step:scene.status.fixed_step});
  await page.waitForTimeout(250);assert.deepEqual({ship:ship(0),device:dc(0),step:scene.status.fixed_step},paused);
  assert(await button('损管 1 关闭').isDisabled());
  await page.screenshot({path:path.join(out,'repair-paused.png'),fullPage:true});
  await button('开始交战').click();await until(()=>!ship(0).descent,'flagship rescued');
  assert.equal(ship(0).height_layer,'upper');assert.equal(scene.direct_ship_id,flagship);
  assert(dc(0).quantity_units<=75000);assert(ship(0).modules.find(m=>m.id==='lift_tank').durability>=25);
  await button('损管 1 关闭').click();
  checks.push('Destroyed lift tanks trigger rescuable descent without flagship loss; helm still works, emergency work takes actual fixed steps, pause freezes both progress bars, and a lost command reply does not duplicate work');
  await fleet.nth(1).click();await until(()=>ship(1).height_layer==='cloud','ally completes a real 5km descent segment',35000);
  await button('观察云层').click();await button('适应本层').click();
  await page.screenshot({path:path.join(out,'ally-cloud-descent.png'),fullPage:true});
  await button('损管').click();await button('损管 1 启动').click();
  await until(()=>!ship(1).descent,'selected allied ship rescued');
  assert.equal(ship(1).height_layer,'cloud');assert.equal(scene.direct_ship_id,flagship);
  assert(dc(1).quantity_units<=75000);await button('损管 1 关闭').click();
  await fleet.nth(2).click();await button('损管').click();assert.equal(await button('损管 1 启动').count(),0);
  await button('舰况').click();await until(()=>ship(2).height_layer==='rain','enemy reaches real rain segment',35000);
  await button('观察雨层').click();await button('适应本层').click();
  await page.setViewportSize({width:1280,height:860});await page.screenshot({path:path.join(out,'rain-last-chance.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  checks.push('Allied damage control targets and costs belong to the selected ship; recovery in cloud keeps that layer and flagship authority; the rain segment displays its final rescue deadline');
  await until(()=>!!scene.settlement,'unrepaired enemy crashes and battle ends',35000);
  await button('保存全部战后结果').waitFor();
  const result=scene.settlement.result;
  assert.equal(result.wrecks.length,1);assert.equal(result.wrecks[0].height_layer,'rain');
  assert.equal(result.wrecks[0].reason,'insufficient_lift');assert.equal(result.reason,'victory');
  for(const n of [0,1]){
    const row=result.ships[n];
    assert(row.changes.some(c=>c.reason==='emergency_lift_refill'&&c.delta>0));
    assert(row.changes.some(c=>c.reason==='emergency_lift_repair'&&c.delta===-25000));
  }
  await page.screenshot({path:path.join(out,'wreck-settlement.png'),fullPage:true});
  await button('保存全部战后结果').click();await button('管理战后库存与下一场准备').click();await button('准备所选舰船').waitFor();
  checks.push('Only the final rain deadline creates one wreck; actual repairs, resource expenses, automatic refills and wreck position survive a saved settlement and return to preparation');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_DESCENT_UI_PASS',checks,scope:'Two legal prepared friendly ships and one passive technical enemy; actual production simulation, UI and settlement. Isolated domain fixture destroys all lift tanks at step 5 and runs a 2x external clock. No player data is used.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
