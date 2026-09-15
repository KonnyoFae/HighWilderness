// Real UI + stdio service, always against an explicitly isolated fixture store.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_RESET_OUT??'artifacts/tactical-reset-20260915');
const url=process.env.HW_TACTICAL_RESET_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical';
let backend,browser,page,serial=0,scene;
let dropSave=false,failSave=false,dropReset=false,failReadAfterSave=false,blockReads=false;
const pending=new Map(),errors=[],checks=[],resetRequests=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser',
    '--settlement-dir',path.join(out,'store'),'--recovery-dir',path.join(out,'editor')],{windowsHide:true});
  backend.stderr.on('data',d=>errors.push(String(d)));
  createInterface({input:backend.stdout}).on('line',line=>{
    const value=JSON.parse(line),p=pending.get(value.request_id);
    if(p){pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));}
  });
}
function request(method,params){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    request_id,session_id:null,expected_revision:null,method,params})+'\n');
});}
async function hello(){await request('system.hello',{client_name:'reset.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.reset_test_state']});}
async function stop(){const child=backend;await new Promise(resolve=>{child.once('exit',resolve);child.kill();});}
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1280,height:900}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='tactical.realtime.save'&&failSave)throw new Error('测试：保存失败');
    if(req.method==='tactical.realtime.read'&&blockReads)throw new Error('测试：保存后场景读取失败');
    if(req.method==='tactical.reset_test_state')resetRequests.push(req.params);
    const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(req.method==='tactical.realtime.save'){
      if(failReadAfterSave)blockReads=true;
      if(dropSave){dropSave=false;throw new Error('测试：保存回执丢失');}
    }
    if(req.method==='tactical.reset_test_state'&&dropReset){dropReset=false;throw new Error('测试：清空回执丢失');}
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const pendingCount=n=>page.getByRole('status').filter({hasText:`还有 ${n} 份战果待处理`}).waitFor();
  const library=()=>request('tactical.preparation.library',{});
  async function clearDialog(){await button('清空战术测试数据').click();await page.getByRole('alertdialog').waitFor();}
  async function enter(){
    const source=(await library()).sources.find(s=>s.name.includes('常规有人'));
    await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
    await button('从目录设计添加舰船').click();
    await page.getByLabel('选择准备舰船 1',{exact:true}).waitFor();
    await button('准备所选舰船').click();
    await button('核对资源与预装填').click();await button('保存准备').click();
    await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
    await button('开始交战').click();
  }
  await page.goto(url);await pendingCount(3);
  await button('保存全部战后结果').click();await pendingCount(2);
  assert(await button('保存全部战后结果').isEnabled());
  assert.equal((await request('tactical.realtime.settlements',{})).results.filter(r=>!r.saved).length,2);
  checks.push('Saving one of multiple pending results automatically opens the next unsaved result');
  dropSave=true;await button('保存全部战后结果').click();
  await page.getByRole('alert').filter({hasText:'保存回执丢失'}).waitFor();
  await button('重试读取战果').click();await pendingCount(1);
  assert(await button('保存全部战后结果').isEnabled());
  checks.push('Refreshing after a lost save reply reconciles the committed result and advances the recovery queue');
  failSave=true;await button('保存全部战后结果').click();
  await page.getByRole('alert').filter({hasText:'测试：保存失败'}).waitFor();
  await clearDialog();await page.screenshot({path:path.join(out,'reset-dialog.png'),fullPage:true});
  await button('取消').click();
  assert.equal(resetRequests.length,0);
  assert.equal((await request('tactical.realtime.settlements',{})).results.filter(r=>!r.saved).length,1);
  await clearDialog();dropReset=true;await button('确认清空并重新开始').click();
  await page.getByRole('alertdialog').getByRole('alert').filter({hasText:'清空回执丢失'}).waitFor();
  assert.equal((await library()).ships.length,0);
  await button('重试清空').click();await button('准备所选舰船').waitFor();
  assert.deepEqual(resetRequests[0],resetRequests[1]);
  assert.equal((await request('tactical.realtime.settlements',{})).results.length,0);
  assert.equal((await library()).drafts.length,0);
  await page.screenshot({path:path.join(out,'reset-done.png'),fullPage:true});
  checks.push('Reset remains accessible after save failure; cancel preserves data; lost reset reply retries the same operation and returns to empty preparation');
  failSave=false;await enter();const discardedScene=scene.status.epoch;
  await clearDialog();await button('确认清空并重新开始').click();await button('准备所选舰船').waitFor();
  assert.equal((await library()).ships.length,0);
  await assert.rejects(()=>request('tactical.realtime.read',{scene_id:discardedScene,known_static_sha256:null,ack_inputs:[],ack_events:0}),/identity mismatch/);
  checks.push('A running prepared battle can be abandoned without creating or saving a settlement; stale scene reads are rejected');
  await page.goto('about:blank');await stop();start();await hello();
  await page.goto(url);await button('准备所选舰船').waitFor();
  assert.equal(await page.getByRole('region',{name:'恢复战后结果',exact:true}).count(),0);
  await enter();await button('结束本场交战').click();await button('保存全部战后结果').waitFor();
  failReadAfterSave=true;await button('保存全部战后结果').click();
  await page.getByRole('status').filter({hasText:'已保存全部参战舰船'}).waitFor();
  await page.getByRole('alert').filter({hasText:'保存后场景读取失败'}).first().waitFor();
  assert(await button('管理战后库存与下一场准备').isEnabled());
  await button('管理战后库存与下一场准备').click();await button('准备所选舰船').waitFor();
  checks.push('After reset and backend restart, a new ship enters a real battle; an acknowledged save unlocks preparation even when subsequent scene reads fail');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_RESET_UI_PASS',checks,resetRequests},null,2));
  console.log(JSON.stringify({status:'TACTICAL_RESET_UI_PASS',checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{await browser?.close();if(backend?.exitCode===null)await stop();}
