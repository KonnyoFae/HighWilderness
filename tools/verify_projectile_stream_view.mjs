// Actual Python publications through the production React realtime screen.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_DISPLAY_OUT??'artifacts/projectile-display-d1-browser-20260921');
await mkdir(out,{recursive:true});
const file=path.join(out,'outfit.json');
await writeFile(file,execFileSync('python',['-X','utf8','-c','import json;from tools.verify_hull_armor_joint import design,ResourceIndex,ROOT;print(json.dumps(design(ResourceIndex(ROOT),25,60).archive()["document"],ensure_ascii=False))'],{windowsHide:true}));
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,`store-${Date.now()}`)],{windowsHide:true});
let serial=0,browser,page,preparation,scene,dropOnce=false,droppedBase,recovered=false;const pending=new Map(),errors=[],streams=[],lifecycle=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function until(fn,label){const end=Date.now()+20000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);}
try{
  await request('system.hello',{client_name:'display.d1.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.read']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:file,mode:'open'});
    const v=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')preparation=v;
    if(v.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=v;
    if(v.view?.projectile_stream){
      const packet=v.view.projectile_stream;streams.push({scene:v.view.scene_id,step:packet.step,sequence:packet.sequence,base:packet.base_sequence,reset:packet.reset});
      if(dropOnce&&req.params.display){dropOnce=false;droppedBase=req.params.display.after_sequence;throw new Error('D1 test: response lost after publication');}
      if(droppedBase!==undefined&&req.params.display?.after_sequence===droppedBase)recovered=true;
    }
    return v;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1431/e3b-test.html?entry=tactical');
  await button('导入栖装文件').click();await until(()=>preparation?.geometry.ships.length===1,'own import');
  await page.getByRole('tab',{name:'敌方',exact:true}).click();await button('导入栖装文件').click();await until(()=>preparation?.geometry.ships.length===2,'enemy import');
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await until(()=>streams.length>1,'production reads negotiate new display protocol');
  await button('开始交战').click();await until(()=>scene.view.fixed_step>30,'real simulation');
  dropOnce=true;await until(()=>recovered,'lost response keeps old cursor until successful retry');
  await button('暂停交战').click();await until(()=>!scene.status.running,'pause');
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});
  assert(streams.some(p=>p.reset)&&streams.some(p=>!p.reset));
  await button('结束本场交战').click();await button('保存全部战后结果').click();await until(()=>scene.settlement.saved,'save');
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  if(process.env.HW_D4_LIFECYCLE){
    const first=scene.status.epoch;
    await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
    await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();
    await until(()=>scene.status.epoch!==first&&!!scene.view.projectile_stream,'second scene negotiated');
    assert.equal(scene.view.projectile_stream.ends.length,0);
    assert.equal(scene.view.fixed_step,0);
    lifecycle.push({firstScene:first,nextScene:scene.status.epoch,oldTerminals:0});
    await button('开始交战').click();await until(()=>scene.view.fixed_step>5,'second scene advances');
    // This store belongs solely to this browser test, never the user's saves.
    await button('清空战术测试数据').click();await button('确认清空并重新开始').click();
    await button('导入栖装文件').waitFor();await until(()=>preparation?.geometry.ships.length===0,'isolated store reset');
    assert.equal(await page.locator('.tactical-canvas').count(),0);
    lifecycle.push({forcedReset:true,remainingShips:0,remainingBattleCanvases:0});
  }
  // Separately replay eight consecutive dense real publications in the browser.
  const frames=JSON.parse(await readFile(process.env.HW_DISPLAY_REPLAY??'artifacts/projectile-display-d1-replay-20260921/frontend-replay.json','utf8'));
  const paired=await page.evaluate(async frames=>{
    const {ProjectileStreamCache}=await import('/src/tactical/projectileStream.ts');
    const {PresentationTimeline}=await import('/src/tactical/presentation.ts');
    const cache=new ProjectileStreamCache(),legacyClock=new PresentationTimeline(),streamClock=new PresentationTimeline();
    const heapBefore=performance.memory?.usedJSHeapSize??null;
    const geometry=frames[0].geometry, timing={legacyParse:[],streamParse:[],merge:[],legacySample:[],streamSample:[]}, decodedFrames=[];let maximumError=0,maximumActive=0,intermediateSamples=0;
    for(const frame of frames){
      const jsonOld=JSON.stringify(frame.legacy),jsonNew=JSON.stringify(frame.stream);
      let t=performance.now();const old=JSON.parse(jsonOld);timing.legacyParse.push(performance.now()-t);
      t=performance.now();const next=JSON.parse(jsonNew);timing.streamParse.push(performance.now()-t);
      t=performance.now();const decoded=cache.apply({geometry,snapshot:next.view});timing.merge.push(performance.now()-t);
      old.view.paused=decoded.snapshot.paused=false;
      decodedFrames.push([{geometry,snapshot:old.view},decoded]);
      legacyClock.push({geometry,snapshot:old.view},old.view.time_s*1000);streamClock.push(decoded,old.view.time_s*1000);
      for(let n=0;n<4;n++){
        const now=old.view.time_s*1000+n*8;
        t=performance.now();const a=legacyClock.sample(now);timing.legacySample.push(performance.now()-t);
        t=performance.now();const b=streamClock.sample(now);timing.streamSample.push(performance.now()-t);
        if(Math.abs(b.snapshot.fixed_step-Math.round(b.snapshot.fixed_step))>1e-6)intermediateSamples++;
        const targets=new Map(a.snapshot.gunnery.projectiles.map(p=>[p.id,p]));
        if(targets.size!==b.snapshot.gunnery.projectiles.length)throw new Error('Flight identities differ');
        maximumActive=Math.max(maximumActive,targets.size);
        for(const p of b.snapshot.gunnery.projectiles){const q=targets.get(p.id);if(!q)throw new Error('Unknown flight');maximumError=Math.max(maximumError,Math.hypot(...p.position_m.map((v,i)=>v-q.position_m[i])));}
      }
    }
    // Warm paired sampling separates first-use JIT from ongoing frame cost.
    const warm={legacy:[],stream:[]};
    for(let n=0;n<600;n++){
      legacyClock.clear();streamClock.clear();
      for(const [old,next] of decodedFrames){legacyClock.push(old,old.snapshot.time_s*1000);streamClock.push(next,next.snapshot.time_s*1000);}
      const now=decodedFrames.at(-1)[0].snapshot.time_s*1000+11;
      for(const key of (n%2?['stream','legacy']:['legacy','stream'])){
        const start=performance.now();(key==='stream'?streamClock:legacyClock).sample(now);
        if(n>=100)warm[key].push(performance.now()-start);
      }
    }
    const summary=Object.fromEntries(Object.entries(warm).map(([key,values])=>{
      values.sort((a,b)=>a-b);return [key,{samples:values.length,meanMs:values.reduce((a,b)=>a+b,0)/values.length,p95Ms:values[Math.floor(values.length*.95)],p99Ms:values[Math.floor(values.length*.99)]}];
    }));
    return {frames:frames.length,maximumActive,maximumError,intermediateSamples,timing,warmSampling:summary,heapBefore,heapAfter:performance.memory?.usedJSHeapSize??null};
  },frames);
  assert(paired.maximumActive>=100);assert(paired.intermediateSamples>0);assert(paired.maximumError<=.100001,'each presentation stays within 5cm of committed sample history');
  let curves=null;
  if(process.env.HW_SHELL_REFERENCE){
    const references=JSON.parse(await readFile(process.env.HW_SHELL_REFERENCE,'utf8'));
    curves=await page.evaluate(async rows=>{
      const {shellPosition,shellTrail}=await import('/src/tactical/shellMotion.ts');
      let maximumError=0,checks=0;
      for(const row of rows){
        for(let n=0;n<=320;n++){
          const step=n/20,a=row.raw[Math.floor(step)],b=row.raw[Math.min(16,Math.floor(step)+1)],t=step-Math.floor(step);
          const expected=[1,2].map(axis=>a[axis]+t*(b[axis]-a[axis]));
          const actual=shellPosition(row.samples,step);
          maximumError=Math.max(maximumError,Math.hypot(...actual.map((v,i)=>v-expected[i])));checks++;
        }
        const trails=[];
        for(const fps of [30,60,120]){
          for(let time=0;time<200;time+=1000/fps)shellTrail(row.samples,time*60/1000,0);
          trails.push(JSON.stringify(shellTrail(row.samples,12,0)));
        }
        if(new Set(trails).size!==1)throw new Error('Trail varies by render cadence');
      }
      return {cases:rows.length,checks,maximumError,frameRates:[30,60,120]};
    },references);
    assert(curves.maximumError<=.05000001);
  }
  let missiles=null;
  if(process.env.HW_MISSILE_REFERENCE){
    const rows=JSON.parse(await readFile(process.env.HW_MISSILE_REFERENCE,'utf8'));
    missiles=await page.evaluate(async ({rows,base,geometry})=>{
      const {missilePose}=await import('/src/tactical/missileMotion.ts');
      const {ProjectileStreamCache}=await import('/src/tactical/projectileStream.ts');
      const wrap=x=>Math.atan2(Math.sin(x),Math.cos(x));let checks=0,maximumError=0,maximumAngleError=0,decodedPackets=0;
      for(const row of rows){
        const cache=new ProjectileStreamCache();let decoded;
        for(const frame of row.frames){
          decoded=cache.apply({geometry,snapshot:{...base,fixed_step:frame.packet.step,time_s:frame.packet.step/60,
            projectile_stream:frame.packet,gunnery:{...base.gunnery,projectiles:[frame.projectile]}}});decodedPackets++;
        }
        const p=decoded.snapshot.gunnery.projectiles[0];
        for(let n=0;n<=480;n++){
          const step=n/20,a=row.raw[Math.floor(step)],b=row.raw[Math.min(24,Math.floor(step)+1)],t=step-Math.floor(step);
          const shown=missilePose(p.missile_samples,p.missile_states,step);
          const expected=[1,2,6].map(axis=>a.sample[axis]+t*(b.sample[axis]-a.sample[axis]));
          maximumError=Math.max(maximumError,Math.hypot(...[...shown.position_m,shown.altitude_m].map((v,i)=>v-expected[i])));
          for(const [axis,value] of [[8,shown.heading_rad],[9,shown.pitch_rad]])maximumAngleError=Math.max(maximumAngleError,Math.abs(wrap(value-a.sample[axis]-wrap(b.sample[axis]-a.sample[axis])*t)));
          for(const key of ['phase','height_layer','target_id','seeker_state','maneuver_state'])if(shown.state[key]!==a.state[key])throw new Error(`${row.label}: early ${key} at ${step}`);
          checks++;
        }
      }
      return {cases:rows.length,checks,decodedPackets,maximumError,maximumAngleErrorDegrees:maximumAngleError*180/Math.PI};
    },{rows,base:frames[0].legacy.view,geometry:frames[0].geometry});
    assert(missiles.maximumError<=.0500001);assert(missiles.maximumAngleErrorDegrees<=.250001);
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',streams,lostResponseRecovered:recovered,lifecycle,paired,curves,missiles,
    scope:'Production React + actual Python preparation/run/pause/lost-read/settlement flow; separate dense real publication replay. Timing samples are short browser diagnostics, not GPU or long-load acceptance.'},null,2));
  console.log(JSON.stringify({status:'PASS',reads:streams.length,paired,curves,missiles}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;}
finally{await browser?.close();backend.kill();for(const p of pending.values())clearTimeout(p.timer);}
