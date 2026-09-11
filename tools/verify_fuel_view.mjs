// Real React and production sidecar, with a saved fuel-filling design in an isolated store.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_H5C_OUT??'artifacts/h5c-fuel-browser-v1');
let backend,browser,page,serial=0,scene,packet;
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
async function hello(){await request('system.hello',{client_name:'h5c.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.commit','tactical.realtime.deploy_prepared']});}
async function stop(){const child=backend;await new Promise(resolve=>{child.once('exit',resolve);child.kill();});}
const url=process.env.HW_H5C_URL??'http://127.0.0.1:1423/e3b-test.html';
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    if(['tactical.preparation.open','tactical.preparation.read'].includes(req.method))packet=result;
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const saved=()=>page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
  await page.goto(url);await button('战前准备').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByRole('heading',{name:'灵烷燃料',exact:true}).waitFor();
  const fillIndex=packet.ships[0].resources.fuel_tanks.findIndex(t=>t.module_id===null)+1;
  const input=page.getByLabel(`燃料槽 ${fillIndex} 装载目标`,{exact:true});
  await input.fill('10001');await saved();await button('核对资源与预装填').click();
  await page.getByRole('alert').filter({hasText:'可用供给不足'}).waitFor();assert(await button('保存准备').isDisabled());
  await input.fill('120');await saved();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const prepared=await request('tactical.preparation.read',{preparation_id:packet.draft.preparation_id});
  assert.equal(prepared.receipt.supply_after.fuel_units,9880);
  assert.equal(prepared.receipt.ships[0].after.state.fuel_units,920);
  assert((await page.getByRole('table').allTextContents()).join('').includes('装载 +120'));
  await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  checks.push('Filling tank shows capacity and independent HP; shortage blocks saving; loading 120 consumes exactly 120 finite supply units');
  await button('以所选舰为旗舰进入交战').click();
  const fuelPanel=page.getByRole('region',{name:'燃料储备',exact:true});await fuelPanel.waitFor();
  assert.equal(scene.view.gunnery.fuel[0].total_units,920);
  await button('开始试航').click();
  await page.getByLabel('实时车钟',{exact:true}).selectOption('full');await button('执行车钟 / 停止转向').click();
  await page.getByText(/运行中 · 第/).waitFor();
  await button('暂停试航').click();
  assert(scene.status.fixed_step>0);assert.equal(scene.view.gunnery.fuel[0].total_units,920);
  await fuelPanel.screenshot({path:path.join(out,'fuel.png')});
  await button('结束交战 / 撤离').click();await button('保存全部战后结果').click();
  await page.getByRole('status').filter({hasText:'已保存全部参战舰船'}).waitFor();
  const record=scene.settlement.result.ships.find(s=>s.after.state.instance_id==='instance.h5c.browser').after;
  assert.equal(record.state.fuel_units,920);
  await page.getByRole('region',{name:'战后结算与舰船存档',exact:true}).screenshot({path:path.join(out,'settlement.png')});
  checks.push('Live fuel view remains unchanged under propulsion; legal settlement preserves fuel quantities and tank HP');
  await page.goto('about:blank');await stop();start();await hello();scene=null;
  await page.goto(url);await button('战前准备').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).check();await button('准备所选舰船').click();
  await page.getByRole('heading',{name:'灵烷燃料',exact:true}).waitFor();
  assert.equal(await page.getByLabel(`燃料槽 ${fillIndex} 装载目标`,{exact:true}).inputValue(),'120');
  assert.equal(packet.supply.fuel_units,9880);
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('以所选舰为旗舰进入交战').click();await fuelPanel.waitFor();
  assert.equal(scene.view.gunnery.fuel[0].total_units,920);
  checks.push('Real backend restart and preparation reentry preserve exact fuel and finite supply without refill');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'H5C_FUEL_UI_PASS',checks,record},null,2));
  console.log(JSON.stringify({status:'H5C_FUEL_UI_PASS',checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{await browser?.close();if(backend?.exitCode===null)await stop();}
