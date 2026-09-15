// Production React panels/sidecar with an isolated legal four-caliber ship.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_TARGETING_OUT??'artifacts/tactical-targeting-20260915');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`),pending=new Map(),errors=[],checks=[];
let serial=0,scene,packet,browser,page;
const commands=[];
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
  await request('system.hello',{client_name:'ballistics.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.gun']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='tactical.realtime.gun')commands.push(req.params.input);
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
  await page.screenshot({path:path.join(out,'group-preparation.png'),fullPage:true});
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  assert.equal(prepared.receipt.ships[0].after.state.magazines.reduce((s,m)=>s+m.quantity,0),26);
  checks.push('Custom saved groups survive preparation: two 30 mm guns in one group, a separate 30 mm group, and a 75 mm group');
  await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
  await page.locator('.tactical-canvas canvas').waitFor();await until(()=>!!scene,'deployment ready');
  assert.equal(guns().length,4);assert.equal(scene.view.ships[1].height_layer,'cloud');
  await button('开始交战').click();await button('火炮').click();
  const forward=()=>guns().filter(g=>['weapon_upper_port','gun.partner'].includes(g.module_id));
  const others=()=>guns().filter(g=>!['weapon_upper_port','gun.partner'].includes(g.module_id));
  await page.getByRole('button',{name:/前部近防炮组/}).click();
  assert.equal(await page.locator('.gun-individual-selection').getAttribute('open'),null);
  assert.equal(await page.getByRole('group',{name:'普通火炮操作'}).getByText('从列表指定目标',{exact:true}).locator('..').getAttribute('open'),null);
  await page.getByLabel('炮弹作用层',{exact:true}).selectOption('cloud');
  await until(()=>forward().every(g=>g.shots>0),'both guns acquire and fire without target commands');
  assert(!commands.some(c=>c.kind==='target'));assert(others().every(g=>g.shots===0));
  await button('停止开火').click();await until(()=>forward().every(g=>g.target_policy==='hold'),'group hold accepted');
  const held=forward().map(g=>g.shots);await page.waitForTimeout(400);assert.deepEqual(forward().map(g=>g.shots),held);
  checks.push('No target order needed: both group members acquire the cloud enemy after layer selection; stop-fire holds without automatic reacquisition');
  await button('观察云层').click();await button('适应本层').click();
  const clickCenter=async()=>{const rect=await page.locator('.tactical-canvas').boundingBox();await page.mouse.click(rect.x+rect.width/2,rect.y+rect.height/2);};
  const before=commands.length;await clickCenter();
  const ambiguity=page.getByRole('button',{name:'瞄准整舰',exact:true});
  if(await ambiguity.isVisible())await ambiguity.click();
  await until(()=>forward().every(g=>g.target_policy==='assigned'&&g.target_ship_id==='ship.web.red'),'canvas assigns the whole group');
  const targetCommands=commands.slice(before).filter(c=>c.kind==='target');
  assert.equal(targetCommands.length,1);assert.equal(targetCommands[0].group_id,'group.forward');assert(!('weapon_id' in targetCommands[0]));
  assert(others().every(g=>g.target_policy==='automatic'&&g.target_ship_id===null));
  await page.getByText('从列表指定目标',{exact:true}).click();
  await page.getByLabel('指定目标模块').selectOption('cic');
  await until(()=>forward().every(g=>g.target_module_id==='cic'),'module order reaches every member');
  await button('停止开火').click();
  checks.push('One canvas target command atomically assigns both members; module selection also applies to both; another same-caliber group remains unchanged');
  await page.getByText('单炮细调',{exact:true}).click();await page.getByLabel('所控火炮',{exact:true}).selectOption('gun.partner');
  await page.getByLabel('炮弹作用层',{exact:true}).selectOption('upper');
  await until(()=>forward().find(g=>g.module_id==='gun.partner').attack_layer==='upper','single member adjustment');
  await page.getByRole('button',{name:/前部近防炮组/}).click();
  assert.equal(await page.getByLabel('炮弹作用层',{exact:true}).inputValue(),'mixed');
  await page.getByLabel('炮弹作用层',{exact:true}).selectOption('cloud');
  await page.getByLabel('火炮模式',{exact:true}).selectOption('manual');
  await until(()=>forward().every(g=>g.mode==='manual'),'manual group mode');
  const shots=forward().map(g=>g.shots);
  await button('观察上层').click();await button('适应本层').click();await clickCenter();
  await page.waitForTimeout(200);assert.deepEqual(forward().map(g=>g.shots),shots);
  await button('观察云层').click();await button('适应本层').click();await clickCenter();
  await until(()=>forward().every((g,i)=>g.shots===shots[i]+1),'one manual click fires each group member once');
  await button('恢复自动选敌').click();await until(()=>forward().every(g=>g.mode==='auto'&&g.target_policy==='automatic'&&g.target_ship_id==='ship.web.red'),'group automatic resume');
  await button('停止开火').click();await button('暂停交战').click();await until(()=>!scene.status.running,'pause accepted');
  const step=scene.status.fixed_step;await page.waitForTimeout(200);assert.equal(scene.status.fixed_step,step);
  assert(await button('恢复自动选敌').isDisabled());
  await page.setViewportSize({width:1280,height:860});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.screenshot({path:path.join(out,'group-fire-control.png'),fullPage:true});
  checks.push('Single-gun overrides display mixed group settings; group manual fire respects observation layer and fires one round per member; automatic resume and pause work');
  await button('开始交战').click();await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  await button('保存全部战后结果').click();await button('管理战后库存与下一场准备').click();
  await button('准备所选舰船').waitFor();await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('当前准备舰船').waitFor();
  assert.equal(packet.ships[0].state.weapons.length,4);
  checks.push('Battle result saves and the grouped ship can enter the next preparation without resetting inventory');
  assert.deepEqual(errors,[]);
  const report={status:'TACTICAL_TARGETING_UI_PASS',checks,commands:commands.map(c=>({kind:c.kind,group_id:c.group_id,weapon_id:c.weapon_id})),
    scope:'Production React and Python simulation; isolated saved custom groups, passive cloud enemy. Not a CIWS interception test.'};
  await writeFile(path.join(out,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;
}finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
