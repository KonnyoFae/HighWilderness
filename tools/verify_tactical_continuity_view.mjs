// Real React + production sidecar. Storage fault and lost replies affect only this isolated QA store.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const layered=process.env.HW_CONTINUITY_LAYERED==='1';
const out=path.resolve(process.env.HW_CONTINUITY_OUT??`artifacts/tactical-ui-u3-continuity-${Date.now()}`),store=path.join(out,`store-${Date.now()}`);
await mkdir(out,{recursive:true});
function python(args){const r=spawnSync('python',['-X','utf8',...args],{encoding:'utf8',windowsHide:true});assert.equal(r.status,0,r.stderr);return r.stdout;}
python(['-m','tools.continuity_fixture',store,...(layered?['--layered']:[])]);
function fault(on){python(['-c',`import sqlite3,sys\nc=sqlite3.connect(sys.argv[1])\nc.execute(${JSON.stringify(on?"CREATE TRIGGER fail_save BEFORE INSERT ON ships WHEN NEW.id='instance.ew.ally' BEGIN SELECT RAISE(ABORT,'5i storage failure'); END":"DROP TRIGGER fail_save")})\nc.commit()`,path.join(store,'settlements.sqlite3')]);}
let backend,browser,page,serial=0,live,packet,dropSave=false,dropReset=false;
const pending=new Map(),errors=[],checks=[],resetRequests=[],metrics={peak_live_bytes:0,peak_settlement_bytes:0,overload:false};
function start(){backend=spawn('python',['-X','utf8','-m',...(layered?['tools.continuity_fixture',store,'--serve']:['backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store])],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},30000);
  pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');});}
async function stop(){if(backend?.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
async function hello(){await request('system.hello',{client_name:'continuity.5i.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.save']});}
const url='http://127.0.0.1:1423/e3b-test.html?entry=tactical';
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      live=r;metrics.overload ||=r.status.pause_reason==='overload';
      const key=r.settlement?'peak_settlement_bytes':'peak_live_bytes';metrics[key]=Math.max(metrics[key],Buffer.byteLength(JSON.stringify(r)));
    }
    if(req.method==='tactical.realtime.save'&&dropSave){dropSave=false;throw new Error('测试：保存回执丢失');}
    if(req.method==='tactical.reset_test_state'){
      resetRequests.push(req.params);
      if(dropReset){dropReset=false;throw new Error('测试：清空回执丢失');}
    }
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(70);assert(fn(),label);};
  async function prepare(){await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();}
  async function enter(){await button('按当前编队进入交战').click();await button('开始交战').click();}
  async function order(kind,extra={},mid='weapon_upper_port'){
    const r=await request('tactical.realtime.missile',{scene_id:live.status.epoch,input:{epoch:live.status.epoch,generation:live.status.generation,
      sequence:live.view.gunnery.missiles.command_sequence+1,ship_id:'ship.ew.ally',order:{module_id:mid,kind,...extra}}});live=r;
  }
  await page.goto(url);await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===4,'four ships loaded');
  await prepare();await enter();const scene1=live.status.epoch;
  await page.getByRole('navigation',{name:'战场舰艇'}).getByRole('button',{name:/电子对抗测试舰 · ally/}).click();
  await page.getByRole('complementary',{name:'火控',exact:true}).getByRole('button',{name:'导弹',exact:true}).click();
  // A deterministic empty-space aim isolates end-of-battle ownership from hits.
  const observedEnemy=()=>live.view.gunnery.observation.ships.find(s=>s.ship_id==='ship.ew.ally').contacts.find(c=>c.id==='ship.ew.enemy'&&c.valid);
  if(layered)await until(()=>observedEnemy()?.height_layer==='cloud','valid observed cloud target');
  const crossTarget=layered?observedEnemy():null;
  if(layered)assert(crossTarget?.height_layer==='cloud');
  await order('point',{point_m:crossTarget?.position_m??[12000,-25000]});await button('单发导弹').click();
  await until(()=>live.view.gunnery.projectiles.some(p=>p.missile&&p.ship_id==='ship.ew.ally'),'first VLS missile emerges');
  if(layered){
    const firstFlight=live.view.gunnery.projectiles.find(p=>p.missile&&p.ship_id==='ship.ew.ally');
    const inspector=page.getByRole('complementary',{name:'火控',exact:true});
    await inspector.getByRole('button',{name:/在途制导/}).click();
    await page.getByLabel(`导弹 ${firstFlight.id} 数据链改攻`,{exact:true}).selectOption(crossTarget.id);
    await until(()=>live.view.gunnery.projectiles.some(p=>p.id===firstFlight.id&&p.missile.maneuver_state==='diving'),'real altitude pursuit before settlement');
    await page.screenshot({path:path.join(out,'before-save-layer-pursuit.png'),fullPage:true});
    await inspector.getByRole('button',{name:'发射控制',exact:true}).click();
    checks.push('The first battle contains a real VLS missile retargeted by the visible data-link control into a continuous descent before settlement; no flight state is seeded.');
  }
  await button('装填与导弹库').click();await button('组装一批（5 枚）').click();
  await until(()=>live.view.gunnery.missiles.ships.find(s=>s.ship_id==='ship.ew.ally').state.magazines[0].jobs.length===5,'five paid assembly jobs');
  await page.screenshot({path:path.join(out,'assembly-in-progress.png'),fullPage:true});await button('发射控制').click();
  await button('单发导弹').click();
  await until(()=>live.view.gunnery.missiles.pending.some(p=>p.ship_id==='ship.ew.ally'),'second VLS is turning');
  await button('暂停交战').click();
  await until(()=>!live.status.running,'pause acknowledged');
  const beforeEnd=structuredClone(live.view.gunnery.missiles.ships.find(s=>s.ship_id==='ship.ew.ally').state);
  assert(live.view.gunnery.projectiles.some(p=>p.missile));assert(live.view.gunnery.missiles.pending.length);
  await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  const first=structuredClone(live.settlement.result),ally=first.ships.find(s=>s.after.state.instance_id==='instance.ew.ally');
  assert.deepEqual(ally.after.state.missiles,beforeEnd);
  assert.equal(first.ships.filter(s=>s.side_id==='side.enemy').length,2);
  assert.equal(ally.changes.filter(c=>c.reason==='missile_fired').reduce((n,c)=>n-c.delta,0),2);
  assert(await page.getByRole('heading',{name:/敌方 · 电子对抗测试舰 · enemy/}).isVisible());
  await page.screenshot({path:path.join(out,'settlement-ledger.png'),fullPage:true});
  fault(true);await button('保存全部战后结果').click();await page.getByRole('alert').filter({hasText:'5i storage failure'}).waitFor();
  assert(await button('管理战后库存与下一场准备').isDisabled());
  assert.equal((await request('tactical.realtime.settlements',{})).results[0].saved,false);
  await page.screenshot({path:path.join(out,'save-failed.png'),fullPage:true});
  checks.push('Both sides are labeled by saved identity; two fired missiles remain spent, including one in flight and one VLS turn; five assembly jobs preserve progress; a real SQLite write fault blocks return without partial commit.');
  await page.goto('about:blank');await stop();fault(false);start();await hello();await page.goto(url);
  await page.getByRole('heading',{name:'恢复战后结果',exact:true}).waitFor();await button('保存全部战后结果').waitFor();
  dropSave=true;await button('保存全部战后结果').click();await page.getByRole('alert').filter({hasText:'保存回执丢失'}).waitFor();
  await button('重试读取战果').click();await button('结算已保存').waitFor();
  assert.deepEqual((await request('tactical.realtime.settlement',{settlement_id:first.settlement_id})).result,first);
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  await until(()=>packet.ships.every(s=>s.state.revision===2),'saved revisions loaded');
  await page.screenshot({path:path.join(out,'recovered-preparation.png'),fullPage:true});
  await prepare();await enter();assert.notEqual(live.status.epoch,scene1);
  assert.equal(live.view.gunnery.missiles.pending.length,0);assert.equal(live.view.gunnery.projectiles.filter(p=>p.missile).length,0);
  const secondAlly=live.view.gunnery.missiles.ships.find(s=>s.ship_id==='ship.ew.ally').state;
  assert.equal(secondAlly.magazines[0].jobs.length,0);assert.equal(secondAlly.launchers[0].ready.length,4);
  await order('point',{point_m:[12000,-25000]});await order('fire');await until(()=>live.view.gunnery.missiles.pending.length>0,'second battle VLS expenditure');
  await button('结束本场交战').click();await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  const second=structuredClone(live.settlement.result);assert(second.ships.every(s=>s.after.state.revision===4));
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  await until(()=>packet.ships.every(s=>s.state.revision===4),'second battle revisions loaded');
  const retained=structuredClone(packet.ships);
  await page.goto('about:blank');await stop();start();await hello();await page.goto(url);await button('配置双方舰内物资').waitFor();
  assert.deepEqual(packet.ships,retained);assert.equal((await request('tactical.realtime.settlements',{})).results.length,2);
  checks.push('Restart recovers a >256 KiB four-ship result; a lost successful save reply reconciles without double charging. Remaining jobs finish at preparation, the next battle has fresh flight state, and both battle results survive another restart.');
  await button('清空战术测试数据').click();await button('取消').click();assert.equal((await request('tactical.preparation.library',{})).ships.length,4);
  await button('清空战术测试数据').click();dropReset=true;await button('确认清空并重新开始').click();
  await page.getByRole('alertdialog').getByRole('alert').filter({hasText:'清空回执丢失'}).waitFor();
  await button('重试清空').click();await page.getByRole('alertdialog').waitFor({state:'hidden'});await button('配置双方舰内物资').waitFor();
  assert.deepEqual(resetRequests[0],resetRequests[1]);assert.equal((await request('tactical.preparation.library',{})).ships.length,0);
  assert.equal((await request('tactical.realtime.settlements',{})).results.length,0);
  await page.screenshot({path:path.join(out,'reset-empty.png'),fullPage:true});
  // Recreate only this isolated test store and prove a fresh UI launch after clearing.
  await page.goto('about:blank');await stop();python(['-m','tools.continuity_fixture',store,...(layered?['--layered']:[])]);start();await hello();await page.goto(url);
  await button('配置双方舰内物资').waitFor();await prepare();await enter();assert.notEqual(live.status.epoch,scene1);
  const discarded=live.status.epoch;
  await button('清空战术测试数据').click();await button('确认清空并重新开始').click();await button('配置双方舰内物资').waitFor();
  assert.equal((await request('tactical.preparation.library',{})).ships.length,0);
  assert.equal((await request('tactical.realtime.settlements',{})).results.length,0);
  await assert.rejects(()=>request('tactical.realtime.read',{scene_id:discarded,known_static_sha256:null,ack_inputs:[],ack_events:0}),/identity mismatch/);
  checks.push('Cancel preserves both results; confirmed reset clears all tactical progress, lost reset reply uses the same identity, and a fresh fleet starts another battle. A running battle can also be abandoned without producing a settlement; stale scene reads fail.');
  assert.deepEqual(errors,[]);assert.equal(metrics.overload,false);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_UI_U3_CONTINUITY_PASS',checks,metrics,first,second},null,2));console.log(JSON.stringify({out,checks,metrics}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());await writeFile(path.join(out,'debug.json'),JSON.stringify({live,packet,metrics},null,2));}throw e;}
finally{await browser?.close();await stop();for(const p of pending.values())clearTimeout(p.timer);}
