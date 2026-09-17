// Real React/Python preparation flow, isolated damaged three-ship fixture.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_MISSILE_OUT??`artifacts/tactical-missiles-5c-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const setup=spawnSync('python',['-X','utf8','-m','tools.missile_browser_fixture',store],{encoding:'utf8',windowsHide:true});
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
async function hello(){await request('system.hello',{client_name:'missiles.5c.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.missile','tactical.realtime.missile']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(req.method==='tactical.preparation.open'||req.method==='tactical.preparation.read')form=r;
    if(req.method==='tactical.preparation.missile'||req.method==='tactical.preparation.draft')form={...form,draft:r};
    if(req.method==='tactical.preparation.commit')receipt=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.preparation.missile'&&loseAction){loseAction=false;throw new Error('测试：导弹计划保存后的回执丢失');}
    if(req.method==='tactical.preparation.commit'&&loseCommit){loseCommit=false;throw new Error('测试：准备保存后的回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const controls=page.getByRole('complementary',{name:'战前部件面板'});
  const selectModule=async id=>{
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='核对资源与预装填'&&!b.disabled));
    const shape=packet.geometry.ships.find(s=>s.id==='instance.missile.player'),m=shape.modules.find(m=>m.id===id);
    await page.getByLabel('我方显示甲板',{exact:true}).selectOption(String(m.deck_level));
    await page.getByRole('region',{name:'我方编队画布'}).getByRole('button',{name:`选择部件 ${shape.name} ${m.name}`,exact:true}).press('Enter');
    await page.locator('.selected-preparation-module strong').filter({hasText:m.name}).waitFor();
    assert.equal(await controls.getByRole('button',{name:'导弹',exact:true}).getAttribute('aria-pressed'),'true');
  };
  await page.goto(process.env.HW_MISSILE_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===2,'two fleets loaded');
  const before=structuredClone(packet.ships);
  await button('配置双方舰内物资').click();await until(()=>form?.ships.length===2,'draft opened');
  await selectModule('ammunition_magazine');
  await button('组装一批（5 枚）').click();await button('重试部件操作').click();
  await page.getByRole('heading',{name:'准备变动预览',exact:true}).waitFor();
  assert.equal(form.draft.revision,1);assert.equal(form.draft.ships.find(s=>s.instance_id==='instance.missile.player').missile_orders.length,1);
  assert.deepEqual((await request('tactical.preparation.scene_read',{})).ships,before);
  await selectModule('weapon_upper_port');await button('装满此发射器').click();
  await until(()=>form.draft.revision===2,'launcher order saved');
  await page.getByText('本次准备耗时 59.0 秒；各舰同步准备，以最长用时为准。',{exact:true}).waitFor();
  await controls.evaluate(e=>e.scrollTop=0);await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  checks.push('Canvas selection opens missile page; five parallel rounds assemble in 31 seconds and four ready rounds load in 28 seconds. Lost action reply retries without duplication; preview leaves both ships unchanged.');
  await button('保存准备').click();await button('重试保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const saved=receipt.ships.find(s=>s.after.state.instance_id==='instance.missile.player');
  assert.equal(saved.after.state.missiles.launchers[0].ready.length,4);
  assert(saved.after.state.missiles.launchers[0].ready.every(u=>u.source==='magazine'));
  assert.equal(saved.after.state.missiles.magazines[0].stock.length,1);
  assert.deepEqual(receipt.ships.find(s=>s.after.state.instance_id==='instance.missile.enemy').before.state.missiles,receipt.ships.find(s=>s.after.state.instance_id==='instance.missile.enemy').after.state.missiles);
  checks.push('One atomic preparation commit preserves source identities and enemy stores; lost commit reply returns the same receipt.');
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='按当前编队进入交战'&&!b.disabled));
  await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await until(()=>live?.view?.gunnery?.missiles?.ships.length===2,'missile fleet deployed');
  await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'});
  await inspector.getByRole('button',{name:'导弹',exact:true}).click();
  await button('组装一批（5 枚）').click();
  const player=()=>live.view.gunnery.missiles.ships.find(s=>s.ship_id===live.direct_ship_id);
  await until(()=>player().state.magazines[0].jobs.length===5,'real battle assembly started');
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});
  // End while jobs are incomplete; the normal save must retain exact progress.
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').waitFor();await button('保存全部战后结果').click();
  await button('结算已保存').waitFor();
  await page.screenshot({path:path.join(out,'settlement.png'),fullPage:true});
  await button('管理战后库存与下一场准备').click();
  await button('配置双方舰内物资').waitFor();
  const settled=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.missile.player').state;
  assert.equal(settled.missiles.magazines[0].jobs.length,5);
  assert(settled.missiles.magazines[0].jobs.every(j=>j.remaining_work_steps>0&&j.remaining_work_steps<j.total_work_steps));
  assert.equal(settled.missiles.launchers[0].ready.length,4);
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.missile.player').state;
  assert.deepEqual(restored,settled);
  checks.push('Real battle assembly consumes onboard materials and progresses; ending/saving/restarting retains five incomplete jobs and four loaded rounds with no refund or completion.');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_MISSILES_5C_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
