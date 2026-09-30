// Real AV5 UI: independent full-loss pool -> receipt -> another battle -> restart.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_AVIATION_OUT??`artifacts/aviation-av5-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const setup=spawnSync('python',['-X','utf8','-m','tools.aviation_salvage_browser_fixture',store],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,form,receipt,live,packet,loseClaim=true;
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
async function stop(){const b=backend;if(b&&b.exitCode===null)await new Promise(resolve=>{b.once('exit',resolve);b.kill();setTimeout(resolve,2000);});}
async function hello(){await request('system.hello',{client_name:'aviation.av5.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.salvage_claim','tactical.realtime.aviation']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(30000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.salvage_read')packet=r;
    if(req.method==='tactical.preparation.open'||req.method==='tactical.preparation.read')form=r;
    if(req.method==='tactical.preparation.aviation')form={...form,draft:r};
    if(req.method==='tactical.preparation.commit')receipt=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.preparation.salvage_claim'&&loseClaim){loseClaim=false;throw new Error('测试：打捞领取已保存，但回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto(process.env.HW_AVIATION_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await page.getByText('航空打捞池',{exact:true}).click();
  await until(()=>packet?.pools[0].entries.length===3,'full-loss assets available independently');
  assert.equal(packet.receivers.length,1);assert.equal(packet.receivers[0].instance_id,'instance.rescue');
  const entries=structuredClone(packet.pools[0].entries);
  assert.equal(entries.filter(e=>e.kind==='pilot').length,2);
  const checkboxes=page.locator('.salvage-panel input[type=checkbox]');
  for(let i=0;i<3;i++)await checkboxes.nth(i).check();
  const before=structuredClone((await request('tactical.preparation.scene_read',{})).ships);
  await button('核对接收容量').click();await button('接收所选资源').waitFor();
  assert.deepEqual((await request('tactical.preparation.scene_read',{})).ships,before);
  await page.screenshot({path:path.join(out,'salvage-preview.png'),fullPage:true});
  await button('接收所选资源').click();await button('重试打捞操作').click();
  await page.getByText('已接收 1 架飞机、2 名飞行员并保存。',{exact:true}).waitFor();
  const received=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.rescue').state;
  assert.equal(received.aviation.manifest.aircraft.length,1);assert.equal(received.aviation.manifest.personnel.length,2);
  assert.equal(received.aviation.manifest.personnel[0].modifiers.speed,1.2);
  assert.deepEqual(received.aviation.manifest.aircraft[0].loadout,{});
  assert.equal(packet.pools[0].entries.length,0);
  await page.screenshot({path:path.join(out,'salvage-received.png'),fullPage:true});
  checks.push('A real full-loss ending leaves one E1 and two unique pilots in an independent saved pool. Actual UI selects a new friendly rescue ship, previews without writes, receives all assets, and retries a lost reply without duplication; ace traits and remaining ammunition survive.');
  await button('配置双方舰内物资').click();await until(()=>form?.ships.length===2,'next battle draft opened');
  await page.getByRole('complementary',{name:'战前部件面板'}).getByRole('button',{name:'航空',exact:true}).click();
  await button('整备至待命').click();await button('战前预装弹射器').click();
  await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const saved=receipt.ships.find(s=>s.after.state.instance_id==='instance.rescue').after.state.aviation;
  assert.equal(saved.manifest.aircraft.filter(a=>a.location==='catapult').length,1);
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='按当前编队进入交战'&&!b.disabled));
  await button('按当前编队进入交战').click();await button('开始交战').click();
  await page.getByRole('complementary',{name:'火控'}).getByRole('button',{name:'航空',exact:true}).click();
  await button('弹射起飞').click();await until(()=>live?.view.gunnery.aviation.flights.length===1,'salvaged E1 launched');
  assert.equal(live.view.gunnery.aviation.flights[0].id,received.aviation.manifest.aircraft[0].id);
  await page.screenshot({path:path.join(out,'second-battle.png'),fullPage:true});
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await page.screenshot({path:path.join(out,'second-settlement.png'),fullPage:true});
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const settled=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.rescue').state;
  assert.equal(settled.aviation.manifest.aircraft.length,1);assert.equal(settled.aviation.manifest.personnel.length,2);
  assert.equal(settled.aviation.manifest.aircraft[0].id,received.aviation.manifest.aircraft[0].id);
  assert.deepEqual(new Set(settled.aviation.manifest.personnel.map(p=>p.id)),new Set(received.aviation.manifest.personnel.map(p=>p.id)));
  await stop();start();await hello();
  assert.deepEqual((await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.rescue').state,settled);
  assert((await request('tactical.preparation.salvage_read',{})).pools.every(p=>!p.entries.length));
  checks.push('The recovered E1 and original crew are serviced and preloaded in the UI, launch in a second real encounter, return at ending, and persist through saving and backend restart. No old salvage entry is restored and no asset is duplicated.');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'AVIATION_AV5_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
