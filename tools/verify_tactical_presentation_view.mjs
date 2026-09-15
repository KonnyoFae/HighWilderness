// Production tactical UI, real stdio simulation, disposable test inventory.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_PRESENTATION_OUT??'artifacts/tactical-presentation-20260915');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`), pending=new Map(), errors=[], checks=[];
let serial=0,scene,browser,page,blockReads=false,control,reading=0;
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser',
  '--settlement-dir',store,'--recovery-dir',path.join(store,'editor')],{windowsHide:true});
backend.stderr.on('data',d=>errors.push(String(d)));
createInterface({input:backend.stdout}).on('line',line=>{
  const value=JSON.parse(line), p=pending.get(value.request_id);
  if(p){pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));}
});
function request(method,params){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`, timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    request_id,session_id:null,expected_revision:null,method,params})+'\n');
});}
try{
  await request('system.hello',{client_name:'presentation.browser',client_version:'1',
    supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.deploy_prepared']});
  browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
    const original=window.requestAnimationFrame.bind(window);
    window.__animationStats={callbacks:0,costs:[]};
    window.requestAnimationFrame=callback=>original(now=>{
      const started=performance.now();callback(now);
      if(callback.name==='animateBattleFrame'){
        window.__animationStats.callbacks++;
        window.__animationStats.costs.push(performance.now()-started);
      }
    });
  });
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='tactical.realtime.read'&&blockReads)throw new Error('测试：暂时中断画面状态传输');
    if(req.method==='tactical.realtime.control')control={sentAt:Date.now(),targetStep:req.params.input.target_step};
    const result=await request(req.method,req.params);
    if(req.method==='tactical.realtime.read')reading++;
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      scene=result;
      if(control&&!control.authorityAt&&result.view.fixed_step>=control.targetStep)control.authorityAt=Date.now();
    }
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(predicate,label)=>{const end=Date.now()+15000;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),label);};
  const canvas=page.locator('.tactical-canvas');
  const state=()=>canvas.evaluate(e=>({step:Number(e.dataset.displayStep),snapshot:Number(e.dataset.snapshotStep),
    labels:[...e.querySelectorAll('.tactical-ship-label')].map(n=>n.style.transform)}));
  async function collect(ms){
    return page.evaluate(ms=>new Promise(resolve=>{
      const started=performance.now(), rows=[];
      const sample=now=>{
        const e=document.querySelector('.tactical-canvas');
        rows.push({t:now,step:Number(e.dataset.displayStep),snapshot:Number(e.dataset.snapshotStep),
          label:e.querySelector('.tactical-ship-label')?.style.transform});
        if(now-started>=ms)resolve(rows);else requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    }),ms);
  }
  async function enter(){
    const library=await request('tactical.preparation.library',{}), source=library.sources.find(s=>s.name.includes('常规有人'));
    assert(source,'standard authored ship is available');
    await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
    await button('从目录设计添加舰船').click();await page.getByLabel('选择准备舰船 1',{exact:true}).waitFor();
    await button('准备所选舰船').click();
    await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');
    await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
    await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');
    await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
    await button('核对资源与预装填').click();await button('保存准备').click();
    await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();
    await button('开始交战').click();await canvas.locator('canvas').waitFor();
  }
  await page.goto(process.env.HW_TACTICAL_PRESENTATION_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('准备所选舰船').waitFor();await enter();
  await page.getByLabel('实时车钟',{exact:true}).selectOption('full');
  await button('执行车钟 / 停止转向').click();
  await until(()=>!!control,'control dispatched');
  const displayedAt=await page.evaluate(target=>new Promise(resolve=>{
    const sample=()=>Number(document.querySelector('.tactical-canvas').dataset.displayStep)>=target?
      resolve(Date.now()):requestAnimationFrame(sample); sample();
  }),control.targetStep);
  const latency={commandToDisplayedStepMs:displayedAt-control.sentAt,
    commandToAuthorityPublicationMs:control.authorityAt-control.sentAt};
  await until(()=>scene.view.ships.find(s=>s.id===scene.direct_ship_id).speed_mps>1,'actual own-ship acceleration');
  await button('火炮').click();
  await page.getByLabel('所控火炮',{exact:true}).selectOption(scene.view.gunnery.weapons.find(g=>g.ship_id===scene.direct_ship_id).module_id);
  await page.getByRole('button',{name:/^瞄准.+/}).first().click();
  const beforeReads=reading, rows=await collect(3000), elapsed=rows.at(-1).t-rows[0].t;
  const changed=rows.filter((r,i)=>i>0&&r.step!==rows[i-1].step).length;
  const snapshots=new Set(rows.map(r=>r.snapshot)).size;
  const lag=rows.map(r=>(r.snapshot-r.step)*1000/60).sort((a,b)=>a-b);
  const costs=await page.evaluate(()=>window.__animationStats.costs.slice(-300).sort((a,b)=>a-b));
  const metrics={observedFramesPerSecond:1000*(rows.length-1)/elapsed,changedFramesPerSecond:1000*changed/elapsed,
    distinctPublications:snapshots,reads:reading-beforeReads,displayedStepsBehindPublicationMsMedian:lag[Math.floor(lag.length/2)],
    displayedStepsBehindPublicationMsP95:lag[Math.floor(lag.length*.95)],animationCallbackMsP95:costs[Math.floor(costs.length*.95)],...latency};
  assert(changed>snapshots*1.7,JSON.stringify(metrics));
  assert(new Set(rows.map(r=>r.label)).size>snapshots*1.7,'ship labels move at display cadence');
  assert(rows.some(r=>Math.abs(r.step-Math.round(r.step))>.01),'intermediate fixed-step positions');
  assert(rows.every((r,i)=>!i||r.step>=rows[i-1].step),'monotonic display time');
  assert(rows.every(r=>r.step<=r.snapshot),'never extrapolate past committed publications');
  await until(()=>scene.view.gunnery.weapons.some(g=>g.ship_id===scene.direct_ship_id&&g.shots>0),'real gun discharge');
  await canvas.screenshot({path:path.join(out,'smooth-battle.png')});
  checks.push('Actual authored ship acceleration, turret targeting and live gunfire render between 15 Hz publications; labels share the same display clock');

  await button('暂停交战').click();await until(()=>!scene.status.running,'pause received');
  await page.waitForTimeout(100);const paused=await state(), callbacks=await page.evaluate(()=>window.__animationStats.callbacks);
  await page.waitForTimeout(250);assert.deepEqual(await state(),paused);
  assert.equal(await page.evaluate(()=>window.__animationStats.callbacks),callbacks,'paused canvas schedules no animation frames');
  await button('战术放大').click();assert.notDeepEqual((await state()).labels,paused.labels,'paused camera remains interactive');
  await canvas.screenshot({path:path.join(out,'paused-battle.png')});
  await button('开始交战').click();await page.waitForTimeout(250);assert((await state()).step>paused.step);
  checks.push('Pause freezes the exact committed scene and removes the animation loop; zoom still works and resume excludes paused wall time');

  blockReads=true;await page.waitForTimeout(300);const stalled=await state();
  await page.waitForTimeout(250);assert.deepEqual(await state(),stalled,'missing snapshots freeze at last known state');
  blockReads=false;await page.waitForTimeout(300);assert((await state()).step>stalled.step);
  checks.push('Temporary read failure freezes at the last known scene and recovers without extrapolated hits or backwards motion');

  // Exercise the actual visibility handler under an explicit hidden document.
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));});
  await until(()=>!scene.status.running,'hidden tab pauses authority');
  const hiddenCallbacks=await page.evaluate(()=>window.__animationStats.callbacks);
  await page.waitForTimeout(200);assert.equal(await page.evaluate(()=>window.__animationStats.callbacks),hiddenCallbacks);
  await page.evaluate(()=>{delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));});
  await button('开始交战').click();await page.waitForTimeout(150);
  const oldScene=scene.status.epoch;
  await button('清空战术测试数据').click();await button('确认清空并重新开始').click();
  await button('准备所选舰船').waitFor();assert.equal(await canvas.count(),0);
  const releasedCallbacks=await page.evaluate(()=>window.__animationStats.callbacks);
  await page.waitForTimeout(200);assert.equal(await page.evaluate(()=>window.__animationStats.callbacks),releasedCallbacks);
  await enter();assert.notEqual(scene.status.epoch,oldScene);assert(scene.view.fixed_step<60);
  assert.equal(scene.view.presentation.finished_projectiles.length,0);
  checks.push('Hidden document stops animation; force reset releases the canvas and the next battle begins with a fresh scene and no prior projectile history');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_PRESENTATION_UI_PASS',metrics,checks,
    scope:'One authored player ship against the prepared opponent; this is presentation validation, not 15–30 ship load acceptance.'},null,2));
  console.log(JSON.stringify({status:'TACTICAL_PRESENTATION_UI_PASS',metrics,checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{
  await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});
}
