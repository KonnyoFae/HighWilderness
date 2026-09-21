// D5 actual packaged Tauri + WebView2 + Rust IPC + unchanged Python backend.
// Always launches an owned process against a fresh, separately copied repo root.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import net from 'node:net';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const root=path.resolve(process.argv.find(a=>a.startsWith('--root='))?.slice(7)??'.local/display-d5-native-root');
assert(root.startsWith(path.resolve('.local')+path.sep),'Use only the isolated local test root');
const manifest=JSON.parse(await readFile(path.join(root,'source-hashes.json'),'utf8'));
for(const [file,hash] of Object.entries(manifest))assert.equal(createHash('sha256').update(await readFile(file)).digest('hex'),hash,`Production source changed: ${file}`);
const out=path.resolve(process.argv.find(a=>a.startsWith('--out='))?.slice(6)??'artifacts/projectile-display-d5-native-20260921');
const seconds=Number(process.argv.find(a=>a.startsWith('--seconds='))?.slice(10)??120);
assert(Number.isFinite(seconds)&&seconds>=1&&seconds<=120);
await mkdir(out,{recursive:true});
const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const n=s.address().port;s.close(()=>resolve(n));});});
const executable=path.resolve('apps/desktop/src-tauri/target/debug/high-wilderness-desktop.exe');
const python=execFileSync('python',['-c','import sys;print(sys.executable)'],{encoding:'utf8',windowsHide:true}).trim();
const child=spawn(executable,['--tactical'],{cwd:root,windowsHide:true,stdio:'ignore',env:{...process.env,
  HIGH_WILDERNESS_REPO_ROOT:root,HIGH_WILDERNESS_PYTHON:python,
  WEBVIEW2_USER_DATA_FOLDER:path.join(root,'webview'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${port}`}});
let browser,page;const errors=[],heap=[],checks=[];
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
try{
  for(let n=0;n<80;n++){
    try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`,{noDefaults:true});break;}catch{await sleep(250);}
  }
  assert(browser,'Owned native desktop debugging endpoint did not start');
  for(let n=0;n<80;n++){
    page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('tauri.localhost')||p.url().startsWith('tauri:'));
    if(page)break;await sleep(250);
  }
  assert(page,'Expected the built desktop page, not a browser fixture');
  page.on('pageerror',e=>errors.push(e.message));
  await page.getByTestId('bridge-state').filter({hasText:'READY'}).waitFor({timeout:30000});
  const cdp=await page.context().newCDPSession(page);
  await page.evaluate(()=>{
    // Tauri deliberately exposes read-only invoke/runCallback functions. Wrap
    // its debug callback map's registration, preserving every original call.
    const callbacks=window.__TAURI_INTERNALS__.callbacks,register=callbacks.set.bind(callbacks);
    const data=window.__d5={latest:null,reads:[],draw:[],frame:[],errors:[],seen:new Set(),ends:new Set(),models:new Set(),
      peakProjectiles:0,peakMissiles:0,peakBytes:0,kinds:new Map(),sequences:new Set(),scenes:new Set(),lastFrame:null};
    const observe=(result,start)=>{
      if(result?.interface==='gaotian.realtime-view/e3b-v1alpha1'){
        data.latest=result;data.reads.push(performance.now()-start);data.scenes.add(result.view.scene_id);
        const p=result.view.projectile_stream,g=result.view.gunnery;
        data.peakProjectiles=Math.max(data.peakProjectiles,g?.projectiles.length??0);
        data.peakBytes=Math.max(data.peakBytes,new TextEncoder().encode(JSON.stringify(result)).length);
        if(p){data.sequences.add(`${result.view.scene_id}/${p.sequence}`);for(const x of p.starts){
          const key=`${result.view.scene_id}/${x.id}`;data.seen.add(key);data.kinds.set(key,x.kind);
          if(x.missile_identity)data.models.add(x.missile_identity.model_id);
        }for(const x of p.ends)data.ends.add(`${result.view.scene_id}/${x.id}`);}
        // Compact active rows omit immutable kind: it belongs to the start
        // definition. Do not mistake that omission for an absence of missiles.
        data.peakMissiles=Math.max(data.peakMissiles,g?.projectiles.filter(x=>
          (x.kind??data.kinds.get(`${result.view.scene_id}/${x.id}`))==='missile').length??0);
      }
    };
    callbacks.set=function(id,callback){const start=performance.now();return register(id,result=>{observe(result,start);return callback(result);});};
    const raf=window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame=callback=>raf(now=>{
      const start=performance.now();callback(now);
      if(document.querySelector('.tactical-canvas')&&!document.hidden){
        data.draw.push(performance.now()-start);
        if(data.lastFrame!==null&&now>data.lastFrame)data.frame.push(now-data.lastFrame);
        data.lastFrame=now;
      }
    });
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label,ms=20000)=>{
    const end=Date.now()+ms;while(Date.now()<end){if(await fn())return;await sleep(100);}throw new Error(label);
  };
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();
  await button('开始交战').waitFor();await button('开始交战').click();
  await until(()=>page.evaluate(()=>window.__d5.latest?.view.fixed_step>15),'Native battle did not advance');
  await page.evaluate(async()=>{
    for(const order of [{module_id:'weapon_upper_port',kind:'point',point_m:[14000,-35000]},
      {module_id:'weapon_upper_port',kind:'auto_fire',enabled:true}]){
      const current=window.__d5.latest;
      await window.__TAURI_INTERNALS__.invoke('bridge_tactical_request',{request:{backend_instance_id:current.view.backend_instance_id,
        session_id:null,expected_revision:null,method:'tactical.realtime.missile',params:{scene_id:current.status.epoch,
          input:{epoch:current.status.epoch,generation:current.status.generation,sequence:current.view.gunnery.missiles.command_sequence+1,
            ship_id:'ship.ew.ally',order}}}});
    }
  });
  const firstScene=await page.evaluate(()=>window.__d5.latest.view.scene_id);
  const start=Date.now();
  for(let checkpoint=1;checkpoint<=Math.ceil(seconds/20);checkpoint++){
    const elapsed=Math.min(seconds,checkpoint*20);
    await sleep(Math.max(0,start+elapsed*1000-Date.now()));
    const live=await page.evaluate(()=>({status:window.__d5.latest.status,step:window.__d5.latest.view.fixed_step,
      ending:window.__d5.latest.view.gunnery?.ending,projectiles:window.__d5.latest.view.gunnery?.projectiles.length}));
    await cdp.send('HeapProfiler.collectGarbage');heap.push({wallSeconds:(Date.now()-start)/1000,step:live.step,...await cdp.send('Runtime.getHeapUsage')});
    console.log(JSON.stringify({nativeProgress:elapsed,...live}));
    assert.notEqual(live.status.pause_reason,'overload','Native scheduler reported simulation overload');
    assert(live.ending||live.status.running,'Native simulation unexpectedly paused');
    if(live.ending)break;
  }
  if(await button('暂停交战').isEnabled())await button('暂停交战').click();
  await page.screenshot({path:path.join(out,'native-battle.png')});
  if(await button('结束本场交战').isEnabled())await button('结束本场交战').click();
  await button('保存全部战后结果').click();await until(()=>page.evaluate(()=>window.__d5.latest.settlement.saved),'Native settlement was not saved');
  checks.push({savedFirstScene:firstScene});
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').click();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();
  await until(()=>page.evaluate(old=>window.__d5.latest.view.scene_id!==old,firstScene),'Second native scene missing');
  const second=await page.evaluate(()=>({step:window.__d5.latest.view.fixed_step,ends:window.__d5.latest.view.projectile_stream?.ends.length??0,
    projectiles:window.__d5.latest.view.gunnery?.projectiles.length??0,
    scene:window.__d5.latest.view.scene_id}));assert.equal(second.step,0);assert.equal(second.ends,0);assert.equal(second.projectiles,0);checks.push({secondScene:second});
  await button('开始交战').click();await until(()=>page.evaluate(()=>window.__d5.latest.view.fixed_step>30),'Second scene did not advance');
  await button('暂停交战').click();await button('结束本场交战').click();await button('保存全部战后结果').click();
  await until(()=>page.evaluate(()=>window.__d5.latest.settlement.saved),'Second native settlement was not saved');
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  assert.equal(await page.locator('.tactical-canvas').count(),0);
  await cdp.send('HeapProfiler.collectGarbage');heap.push({label:'back-to-preparation',...await cdp.send('Runtime.getHeapUsage')});
  const metrics=await page.evaluate(()=>{
    const d=window.__d5,stats=values=>{values.sort((a,b)=>a-b);return {count:values.length,meanMs:values.reduce((a,b)=>a+b,0)/values.length,
      p95Ms:values[Math.floor(values.length*.95)],p99Ms:values[Math.floor(values.length*.99)],maxMs:values.at(-1)}};
    return {ipc:stats(d.reads),animationCallback:stats(d.draw),frameInterval:stats(d.frame),seen:d.seen.size,ends:d.ends.size,
      models:[...d.models],peakProjectiles:d.peakProjectiles,peakMissiles:d.peakMissiles,peakBytes:d.peakBytes,
      publications:d.sequences.size,scenes:[...d.scenes],lastSaved:d.latest.settlement.saved,
      timingScope:'All RAF callbacks while the tactical canvas is mounted, including Pixi maintenance; not isolated GPU duration or canvas rendering CPU.'};
  });
  await writeFile(path.join(out,'flow-checks.json'),JSON.stringify({checks,heap,metrics,errors},null,2));
  assert(metrics.peakMissiles>0&&metrics.seen>0);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(out,'native-return-to-preparation.png')});
  const result={status:'PASS',scope:'Unmodified packaged Tauri executable, actual WebView2/Rust/Python IPC and real gameplay. Isolated copied source/resources and four finite-stock ships in two legal SCIC fleets. No injected battle damage or browser transport adapter.',
    executableSha256:createHash('sha256').update(await readFile(executable)).digest('hex'),productionSourceFiles:Object.keys(manifest).length,
    requestedSustainedSeconds:seconds,excluded:'Native window visibility/minimization is excluded at user request; no OS window-state tests are run.',metrics,heap,checks,errors};
  await writeFile(path.join(out,'report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}catch(error){
  await writeFile(path.join(out,'error.txt'),String(error));
  await writeFile(path.join(out,'partial-checks.json'),JSON.stringify({status:'FAIL',checks,heap,errors},null,2));
  if(page&&!page.isClosed()){await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText()).catch(()=>{});}
  throw error;
}finally{
  if(page&&!page.isClosed())await page.evaluate(()=>window.__TAURI_INTERNALS__.invoke('bridge_stop')).catch(()=>{});
  await browser?.close();child.kill();
}
