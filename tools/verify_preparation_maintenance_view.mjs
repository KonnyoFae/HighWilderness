// Real React/Python preparation flow, isolated damaged three-ship fixture.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_MAINTENANCE_OUT??`artifacts/tactical-preparation-5b-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const setup=spawnSync('python',['-X','utf8','-m','tools.preparation_maintenance_browser_fixture',store],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,packet,form,receipt,live,loseAction=true,loseCommit=true;
const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function stop(){const b=backend;if(b.exitCode===null)await new Promise(resolve=>{b.once('exit',resolve);b.kill();});}
async function hello(){await request('system.hello',{client_name:'maintenance.5b.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.maintenance']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(req.method==='tactical.preparation.open'||req.method==='tactical.preparation.read')form=r;
    if(req.method==='tactical.preparation.maintenance'||req.method==='tactical.preparation.draft')form={...form,draft:r};
    if(req.method==='tactical.preparation.commit')receipt=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.preparation.maintenance'&&loseAction){loseAction=false;throw new Error('测试：部件操作保存后的回执丢失');}
    if(req.method==='tactical.preparation.commit'&&loseCommit){loseCommit=false;throw new Error('测试：保存准备后的回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const controls=page.getByRole('complementary',{name:'战前部件面板'});
  const main=()=>packet.ships.find(s=>s.instance_id==='instance.5b');
  const selectModule=async id=>{
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='核对资源与预装填'&&!b.disabled));
    const shape=packet.geometry.ships.find(s=>s.id==='instance.5b'),m=shape.modules.find(m=>m.id===id);
    await page.getByLabel('我方显示甲板',{exact:true}).selectOption(String(m.deck_level));
    // The same hull appears twice; first is the flagship under test.
    const target=page.getByRole('region',{name:'我方编队画布'}).getByRole('button',{name:`选择部件 ${shape.name} ${m.name}`,exact:true}).first();
    await target.press('Enter');
    await page.locator('.selected-preparation-module strong').filter({hasText:m.name}).waitFor();
  };
  await page.goto(process.env.HW_MAINTENANCE_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('配置双方舰内物资').waitFor();
  await until(()=>packet?.ships.length===3,'initial fleet loaded');
  const baseline=structuredClone(packet.ships);
  await button('配置双方舰内物资').click();await until(()=>form?.ships.length===3,'draft opened');
  await selectModule('gun.heavy');assert.equal(await controls.getByRole('button',{name:'火炮',exact:true}).getAttribute('aria-pressed'),'true');
  await button('补满本舰全部火炮').click();await button('重试部件操作').click();
  await page.getByRole('alert').filter({hasText:'可用供给不足'}).waitFor();assert(await button('保存准备').isDisabled());
  assert.equal(form.draft.revision,1);
  assert(form.draft.ships.find(s=>s.instance_id==='instance.5b').weapons.every(w=>w.action==='top_up'));
  assert(form.draft.ships.filter(s=>s.instance_id!=='instance.5b').every(s=>s.weapons.every(w=>w.action==='keep')));
  const unchanged=await request('tactical.preparation.scene_read',{});assert.deepEqual(unchanged.ships,baseline);
  checks.push('Canvas selection opens guns; mixed-caliber same-ship top-up is durable/idempotent after lost reply; shortages block all saving and leave all three ships unchanged.');
  await button('放弃准备草稿').click();await button('配置双方舰内物资').waitFor();
  await page.getByText('测试供给池',{exact:true}).click();await button('补充到上述数量').click();
  await page.getByRole('status').filter({hasText:'已补充测试供给池'}).waitFor();
  await button('配置双方舰内物资').click();await button('核对资源与预装填').waitFor();
  await selectModule('gun.heavy');await button('补满本舰全部火炮').click();await page.getByRole('heading',{name:'准备变动预览',exact:true}).waitFor();
  await button('修复此部件（3 份工程零件）').click();await until(()=>form.draft.ships.find(s=>s.instance_id==='instance.5b').repairs.includes('gun.heavy'),'repair saved');
  await selectModule('damage_control');await button('补满此部件').click();await page.getByRole('heading',{name:'准备变动预览',exact:true}).waitFor();
  await selectModule('generator');assert(await button('修复此部件（4 份工程零件）').isDisabled());
  await selectModule('gun.heavy');await controls.evaluate(e=>e.scrollTop=0);await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  await button('保存准备').click();await button('重试保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await until(()=>main().state.modules.find(m=>m.module_id==='gun.heavy').durability_points===100,'outer selected module health refreshed');
  const saved=receipt.ships.find(s=>s.after.state.instance_id==='instance.5b');
  assert(saved.after.state.weapons.every(w=>w.ready_rounds===(w.module_id==='gun.heavy'?6:60)));
  assert.equal(saved.after.state.damage_controls[0].quantity_units,100000);
  assert.equal(saved.after.state.modules.find(m=>m.module_id==='generator').durability_points,0);
  assert.equal(receipt.supply_before.cargo.find(c=>c.good_id==='cargo.engineering_parts').quantity-receipt.supply_after.cargo.find(c=>c.good_id==='cargo.engineering_parts').quantity,4);
  for(const r of receipt.ships.filter(s=>s.after.state.instance_id!=='instance.5b')){assert.deepEqual(r.before.state.weapons,r.after.state.weapons);assert.deepEqual(r.before.state.modules,r.after.state.modules);}
  await controls.evaluate(e=>e.scrollTop=e.scrollHeight);await page.screenshot({path:path.join(out,'saved.png'),fullPage:true});
  checks.push('Explicit supply refill followed by one shared commit fills only flagship guns/DC, repairs living gun 39→100 for three parts, excludes destroyed generator and charges four total engineering parts; lost commit reply does not duplicate spending.');
  const expected=structuredClone(receipt);await stop();start();await hello();await page.reload();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  for(const ship of packet.ships)assert.deepEqual(ship.state,expected.ships.find(r=>r.after.state.instance_id===ship.instance_id).after.state);
  assert.deepEqual(form.receipt,expected);
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='按当前编队进入交战'&&!b.disabled));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  checks.push('Backend/browser restart restores the exact saved receipt, repaired health, stock, unchanged other ships and enabled next-battle entry.');
  await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await until(()=>live?.view?.ships.length===3,'saved maintenance fleet deployed');
  assert.equal(live.view.ships.length,3);
  const identity=live.view.static.resources.instance_mapping.find(s=>s.instance_id==='instance.5b');
  assert(identity);const guns=live.view.gunnery.weapons.filter(w=>w.ship_id===identity.ship_id);
  assert.equal(guns.length,4);assert(guns.every(w=>w.ready_rounds>0));
  checks.push('The repaired and refilled saved fleet deploys through the real three-ship tactical entry.');
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_PREPARATION_5B_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
