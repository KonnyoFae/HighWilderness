// Real React/Python preparation flow, isolated damaged three-ship fixture.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_AVIATION_OUT??`artifacts/aviation-av1-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,'store');
const setup=spawnSync('python',['-X','utf8','-m','tools.aviation_browser_fixture',store],{encoding:'utf8',windowsHide:true});
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
async function stop(){const b=backend;if(b&&b.exitCode===null)await new Promise(resolve=>{b.once('exit',resolve);b.kill();setTimeout(resolve,2000);});}
async function hello(){await request('system.hello',{client_name:'missiles.5c.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.aviation','tactical.realtime.aviation']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(30000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(req.method==='tactical.preparation.open'||req.method==='tactical.preparation.read')form=r;
    if(req.method==='tactical.preparation.aviation'||req.method==='tactical.preparation.draft')form={...form,draft:r};
    if(req.method==='tactical.preparation.commit'){receipt=r;console.log('preparation committed');}
    if(req.method==='tactical.realtime.deploy_encounter')console.log('battle deployed');
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.preparation.aviation'&&loseAction){loseAction=false;throw new Error('测试：航空计划保存后的回执丢失');}
    if(req.method==='tactical.preparation.commit'&&loseCommit){loseCommit=false;throw new Error('测试：准备保存后的回执丢失');}
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const controls=page.getByRole('complementary',{name:'战前部件面板'});
  await page.goto(process.env.HW_AVIATION_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').click();await until(()=>form?.ships.length===2,'draft opened');
  await controls.getByRole('button',{name:'航空',exact:true}).click();
  const before=structuredClone((await request('tactical.preparation.scene_read',{})).ships);
  await button('接收 F1').click();await button('重试部件操作').click();
  await page.getByRole('heading',{name:'准备变动预览',exact:true}).waitFor();
  await button('接收 1 名飞行员').click();
  await button('整备至待命').click();
  await button('战前预装弹射器').click();
  await button('接收 F1').click();await button('接收 1 名飞行员').click();
  await button('安排开战后整备').click();
  await until(()=>form?.draft.revision===7,'seven aviation draft orders');
  assert.deepEqual((await request('tactical.preparation.scene_read',{})).ships,before);
  await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  await button('保存准备').click();await button('重试保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  const saved=receipt.ships.find(s=>s.after.state.instance_id==='instance.aviation.player').after.state.aviation;
  assert.equal(saved.manifest.aircraft.filter(a=>a.location==='catapult').length,1);
  assert.equal(saved.manifest.aircraft.filter(a=>a.location==='cargo').length,1);
  assert.equal(saved.queue.length,1);assert.equal(saved.jobs.length,0);
  checks.push('Actual preparation UI receives two unique aircraft and pilots, services one, preloads it, and schedules the other; lost action/commit replies retry once without duplicating inventory.');
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='按当前编队进入交战'&&!b.disabled));
  console.log('enter button ready');await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'火控'});
  await inspector.getByRole('button',{name:'航空',exact:true}).click();
  const player=()=>live?.view?.gunnery?.aviation?.ships.find(s=>s.ship_id===live.direct_ship_id);
  await until(()=>player()?.state.jobs.length===1,'queued job starts in actual battle');
  await until(()=>player()?.state.jobs[0].remaining_steps<1795,'real powered/staffed hangar ticking');
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});
  await button('取消并立即完工').click();
  await until(()=>player()?.state.manifest.aircraft.some(a=>a.location==='ready'),'cancel completes job');
  await request('tactical.realtime.withdraw',{scene_id:live.status.epoch});
  await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await page.screenshot({path:path.join(out,'settlement.png'),fullPage:true});
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const settled=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.aviation.player').state;
  assert.equal(settled.aviation.jobs.length,0);assert.equal(settled.aviation.manifest.aircraft.length,2);
  assert.equal(settled.aviation.manifest.aircraft.flatMap(a=>a.crew).length,2);
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.aviation.player').state;
  assert.deepEqual(restored,settled);
  checks.push('Real battle powers the hangar, begins only the queued job, accepts an immediate-completion cancel, and preserves both airframes and crew across ending, saving and backend restart.');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'AVIATION_AV1_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
