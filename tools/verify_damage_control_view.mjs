// Real React and production sidecar, with a wounded entry save made by the fixture tool.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_D1D_OUT??'artifacts/d1d-damage-control-browser-v1');
let backend,browser,page,serial=0,scene,packet,loseDamage=false;
const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store')],{windowsHide:true});
  backend.stderr.on('data',d=>errors.push(String(d)));
  createInterface({input:backend.stdout}).on('line',line=>{
    const value=JSON.parse(line),p=pending.get(value.request_id);
    if(p){pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));}
  });
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,session_id,expected_revision,method,params})+'\n');
});}
async function hello(){await request('system.hello',{client_name:'d1d.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.damage_control']});}
async function stop(){const child=backend;await new Promise(resolve=>{child.once('exit',resolve);child.kill();});}
const url=process.env.HW_D1D_URL??'http://127.0.0.1:1423/e3b-test.html';
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(['tactical.preparation.open','tactical.preparation.read'].includes(req.method))packet=result;
    if(loseDamage&&req.method==='tactical.realtime.damage_control'){loseDamage=false;throw new Error('fixture: damage-control reply lost');}
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  await page.goto(url);await button('战前准备').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByLabel('工程零件装载目标',{exact:true}).fill('1');
  await page.getByLabel('损管 1 预准备',{exact:true}).check();await saved();
  await button('核对资源与预装填').click();
  await page.getByRole('alert').filter({hasText:'工程零件不足'}).waitFor();
  assert(await button('保存准备').isDisabled());
  await page.getByLabel('工程零件装载目标',{exact:true}).fill('4');await saved();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  assert.equal(prepared.receipt.ships[0].after.state.damage_controls[0].quantity_units,100000);
  assert.equal(prepared.receipt.ships[0].after.state.cargo.find(c=>c.good_id==='cargo.engineering_parts').quantity,2);
  assert.equal(prepared.receipt.ships[0].after.state.modules.find(m=>m.module_id==='custom.cargo').durability_points,50);
  checks.push('UI shortage blocks commit; corrected preprepare consumes exactly two parts and does not repair before battle');
  await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  await button('以所选舰为旗舰进入交战').click();
  const dc=page.getByRole('group',{name:'损管操作',exact:true});await dc.waitFor();
  assert(await button('损管 1 启动').isDisabled());
  await button('开始试航').click();
  await page.getByLabel('损管 1 维修目标',{exact:true}).selectOption('custom.cargo');
  loseDamage=true;await button('损管 1 启动').click();
  await button('损管 1 关闭').waitFor();
  await dc.getByText(/优先灭火中/).waitFor();
  await dc.getByText(/维修部件中/).waitFor();
  await button('暂停试航').click();
  const paused=structuredClone(scene),d=paused.view.gunnery.damage_control.devices[0];
  assert.equal(d.target_module_id,'custom.cargo');assert(d.quantity_units<99000);
  assert.equal(paused.view.gunnery.damage_control.fires.length,0);
  assert(paused.view.ships[0].modules.find(m=>m.id==='custom.cargo').durability>49.8);
  assert(await button('损管 1 关闭').isDisabled());
  await button('读取实时状态').click();assert.equal(scene.status.fixed_step,paused.status.fixed_step);
  assert.equal(scene.view.gunnery.damage_control.devices[0].quantity_units,d.quantity_units);
  checks.push('Lost input reply resolves by reading; real entry fire preempts repair; resource spending and HP changes are real; pause freezes work');
  await dc.screenshot({path:path.join(out,'damage-control.png')});
  await button('开始试航').click();await page.getByLabel('损管 1 维修目标',{exact:true}).selectOption('');
  await button('损管 1 关闭').click();await button('暂停试航').click();
  const beforeEnd=structuredClone(scene);
  await button('结束交战 / 撤离').click();await button('保存全部战后结果').click();
  await page.getByRole('status').filter({hasText:'已保存全部参战舰船'}).waitFor();
  const settlement=structuredClone(scene.settlement);
  const record=settlement.result.ships.find(s=>s.after.state.instance_id==='instance.d1d.browser').after;
  assert.equal(record.state.damage_controls[0].quantity_units,beforeEnd.view.gunnery.damage_control.devices[0].quantity_units);
  assert(record.state.modules.find(m=>m.module_id==='custom.cargo').durability_points>49.8);
  assert((await page.getByRole('table').allTextContents()).join('').includes('灭火'));
  assert((await page.getByRole('table').allTextContents()).join('').includes('部件维修'));
  await page.getByRole('region',{name:'战后结算与舰船存档',exact:true}).screenshot({path:path.join(out,'settlement.png')});
  checks.push('UI withdrawal and save preserve exact HP/resources, with separate firefighting/repair costs');
  await page.goto('about:blank');await stop();start();await hello();scene=null;
  await page.goto(url);await button('战前准备').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  assert(await page.getByLabel('损管 1 预准备',{exact:true}).isDisabled());
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await dc.waitFor();
  const after=scene.view.gunnery.damage_control.devices[0];
  assert(!after.enabled);assert.equal(after.target_module_id,null);assert.equal(after.quantity_units,record.state.damage_controls[0].quantity_units);
  assert.equal(scene.view.ships[0].modules.find(m=>m.id==='custom.cargo').durability,record.state.modules.find(m=>m.module_id==='custom.cargo').durability_points);
  checks.push('Actual sidecar restart and reentry preserve repairs/resources while resetting enable/target intents');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'D1D_UI_SCOPED_PASS',checks,settlement},null,2));
  console.log(JSON.stringify({status:'D1D_UI_SCOPED_PASS',checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{await browser?.close();if(backend?.exitCode===null)await stop();}
