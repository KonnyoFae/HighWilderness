// D5: real production viewport, CPU submission timings and GC-normalized heap.
// Input recordings stay in Node; the page receives only the current publication.
// No gameplay code, hidden enemy data or production timing hooks are added.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.argv.find(a=>a.startsWith('--out='))?.slice(6)??process.env.HW_D5_PERF_OUT??'artifacts/projectile-display-d5-render-20260921');
const input=path.resolve(process.env.HW_D5_INPUT??'artifacts/projectile-display-d5-joint-20260921');
await mkdir(out,{recursive:true});
const headed=process.argv.includes('--headed')||process.env.HW_D5_HEADED==='1';
let ownedProcess;
async function launch(){
  if(!headed)return chromium.launch({channel:'msedge',headless:true,args:['--enable-precise-memory-info']});
  // Attach without Playwright's automatic focus emulation. A second CDP
  // session cannot cancel another session's forced-visible override.
  const executable=[process.env['PROGRAMFILES(X86)'],process.env.ProgramFiles]
    .filter(Boolean).map(p=>path.join(p,'Microsoft/Edge/Application/msedge.exe')).find(existsSync);
  assert(executable,'Microsoft Edge is required');
  const profile=path.resolve('.local/projectile-display-d5-browser',String(Date.now()));
  await mkdir(profile,{recursive:true});
  ownedProcess=spawn(executable,[`--user-data-dir=${profile}`,'--remote-debugging-port=0','--no-first-run',
    '--no-default-browser-check','--disable-background-networking','--disable-component-update',
    '--enable-precise-memory-info','--window-size=1440,1000','about:blank'],{windowsHide:true,stdio:'ignore'});
  for(let attempt=0;attempt<100;attempt++){
    try{
      const port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];
      return await chromium.connectOverCDP(`http://127.0.0.1:${port}`,{noDefaults:true});
    }catch{await new Promise(resolve=>setTimeout(resolve,100));}
  }
  ownedProcess.kill();throw new Error('Owned test browser did not expose its debugging endpoint');
}
const browser=await launch();
const page=headed?await browser.contexts()[0].newPage():await browser.newPage({viewport:{width:1440,height:1000}});
await page.setViewportSize({width:1440,height:1000});
const errors=[],reports=[],heap=[];
page.on('pageerror',error=>errors.push(error.message));
const cdp=await page.context().newCDPSession(page);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,Math.max(0,ms)));
try{
  await page.route('**/d5-performance.html',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"><script type="module">
    import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
    </script></head><body><div id="root"></div></body></html>`}));
  await page.goto('http://127.0.0.1:1431/d5-performance.html');
  // Preflight actual OS visibility before spending minutes on measurements.
  // Playwright normally disables backgrounding; those flags are omitted above.
  if(headed){
    // Playwright also forces focus/visibility through CDP, independently of
    // Chromium launch flags. Disable that test override for the native check.
    await cdp.send('Emulation.setFocusEmulationEnabled',{enabled:false});
    const rootCdp=await browser.newBrowserCDPSession();
    const {targetInfo}=await cdp.send('Target.getTargetInfo');
    const {windowId}=await rootCdp.send('Browser.getWindowForTarget',{targetId:targetInfo.targetId});
    await rootCdp.send('Browser.setWindowBounds',{windowId,bounds:{windowState:'minimized'}});
    await page.waitForFunction(()=>document.hidden,null,{polling:100,timeout:10000});
    await rootCdp.send('Browser.setWindowBounds',{windowId,bounds:{windowState:'normal'}});
    await page.bringToFront();
    await page.waitForFunction(()=>!document.hidden,null,{polling:100});
    await cdp.send('Emulation.setFocusEmulationEnabled',{enabled:true});
  }
  await page.evaluate(async()=>{
    const React=(await import('/node_modules/.vite/deps/react.js')).default;
    const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
    const {TacticalViewport}=await import('/src/tactical/TacticalViewport.tsx');
    const {PresentationTimeline}=await import('/src/tactical/presentation.ts');
    const {ProjectileStreamCache}=await import('/src/tactical/projectileStream.ts');
    const {observedBattleView}=await import('/src/tactical/observedView.ts');
    const {Application}=await import('/src/rendering/pixi.ts');
    const root=createRoot(document.getElementById('root')),cache=new ProjectileStreamCache();
    let geometry,view,mode='stream',record=false,lastFrame,drawStart,sampleCost=0;
    let metrics={},maxFlights=0,maxFrames=0,maxDisplayed=0,lastClock;
    const push=(key,v)=>(metrics[key]??=[]).push(v);
    const sample=PresentationTimeline.prototype.sample;
    PresentationTimeline.prototype.sample=function(now){
      drawStart=performance.now();lastClock=this;const result=sample.call(this,now);sampleCost=performance.now()-drawStart;
      maxFrames=Math.max(maxFrames,this.frames.length);
      maxDisplayed=Math.max(maxDisplayed,result?.snapshot.gunnery?.projectiles.length??0);
      if(record)push('sampling',sampleCost);return result;
    };
    const render=Application.prototype.render;
    Application.prototype.render=function(){
      const start=performance.now();const value=render.call(this);const end=performance.now();
      if(record&&drawStart!==undefined){
        push('prepare',Math.max(0,start-drawStart-sampleCost));push('renderSubmission',end-start);push('canvasTotal',end-drawStart);
      }
      drawStart=undefined;return value;
    };
    const raf=window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame=callback=>raf(now=>{
      if(record&&callback.name==='animateBattleFrame'){
        if(lastFrame!==undefined)push('frameInterval',now-lastFrame);lastFrame=now;
      }
      callback(now);
    });
    window.setup=(g,m)=>{geometry=g;mode=m;cache.clear();record=false;lastFrame=undefined;metrics={};maxFlights=maxFrames=maxDisplayed=0;};
    window.measure=()=>{metrics={};lastFrame=undefined;record=true;};
    window.deliver=(json,sceneSuffix='',observe=true)=>{
      let start=performance.now();const packet=JSON.parse(json);if(record)push('parse',performance.now()-start);
      packet.view.scene_id+=sceneSuffix;packet.view.paused=false;
      start=performance.now();const decoded=mode==='stream'?cache.apply({geometry,snapshot:packet.view}):{geometry,snapshot:packet.view};
      if(record)push('merge',performance.now()-start);
      maxFlights=Math.max(maxFlights,cache.flights.size);
      const own=decoded.snapshot.control_state?.direct_ship_id??geometry.ships[0].id;
      start=performance.now();view=observe?observedBattleView(decoded,own,own):decoded;
      if(record)push('observation',performance.now()-start);
      root.render(React.createElement(TacticalViewport,{view,active:true,selected:own,onSelect:()=>{},overlay:observe}));
    };
    window.finish=()=>{
      record=false;
      const stats=Object.fromEntries(Object.entries(metrics).map(([key,values])=>{
        values.sort((a,b)=>a-b);return [key,{count:values.length,meanMs:values.reduce((a,b)=>a+b,0)/Math.max(1,values.length),
          p95Ms:values[Math.floor(values.length*.95)]??0,p99Ms:values[Math.floor(values.length*.99)]??0,maxMs:values.at(-1)??0}];
      }));
      metrics={};return {stats,maxFlights,maxFrames,maxDisplayed,remainingFlights:cache.flights.size,clockFrames:lastClock?.frames.length??0};
    };
    window.empty=()=>{
      cache.clear();view={...view,snapshot:{...view.snapshot,scene_id:'d5.empty',fixed_step:0,time_s:0,paused:true,
        ships:[],presentation:{finished_projectiles:[]},gunnery:{...view.snapshot.gunnery,projectiles:[],ending:{reason:'test'},damage:undefined,point_defense:undefined}}};
      root.render(React.createElement(TacticalViewport,{view,active:true,selected:null,onSelect:()=>{}}));
    };
    window.release=()=>{record=false;metrics={};cache.clear();view=null;geometry=null;lastClock=null;root.unmount();};
  });
  const viewport=page.locator('.tactical-canvas');
  async function heapSample(label){
    await cdp.send('HeapProfiler.collectGarbage');
    const memory=await cdp.send('Runtime.getHeapUsage'),dom=await cdp.send('Memory.getDOMCounters');
    const row={label,...memory,...dom};heap.push(row);return row;
  }
  // Paired dense A5 recordings use identical camera, full sample visibility,
  // current yellow-trail drawing, and the legacy vs current interpolation paths.
  const pairs=JSON.parse(await readFile(process.env.HW_DISPLAY_REPLAY??'artifacts/projectile-display-d2-replay-20260921/frontend-replay.json','utf8'));
  for(const mode of ['legacy','stream']){
    await page.evaluate(({g,mode})=>window.setup(g,mode),{g:pairs[0].geometry,mode});
    const start=performance.now();let serial=0;
    for(let cycle=0;cycle<32;cycle++)for(const frame of pairs){
      await sleep(start+serial++*1000/15-performance.now());
      await page.evaluate(({json,suffix})=>window.deliver(json,suffix,false),{json:JSON.stringify(frame[mode]),suffix:`.dense.${mode}.${cycle}`});
      if(cycle===3&&frame===pairs[0])await page.evaluate(()=>window.measure());
    }
    reports.push({kind:'paired-dense',mode,...await page.evaluate(()=>window.finish())});
    await page.screenshot({path:path.join(out,`dense-${mode}.png`)});
  }
  // Keep actual recorded steps, observations and identities. Each frame is
  // supplied at 15 Hz; backend simulation was recorded independently.
  let nativeWindow=null;
  for(const layer of ['upper','cloud','rain']){
    const geometry=JSON.parse(await readFile(path.join(input,`${layer}-geometry.json`),'utf8'));
    const lines=gunzipSync(await readFile(path.join(input,`${layer}-stream.jsonl.gz`))).toString('utf8').trim().split('\n');
    await page.evaluate(g=>window.setup(g,'stream'),geometry);
    await page.evaluate(json=>window.deliver(json),lines[0]);await viewport.locator('canvas').waitFor();
    await page.waitForFunction(id=>document.querySelector('.tactical-canvas')?.dataset.visibleShips?.includes(id),geometry.ships[0].id);
    const layerButton=page.getByRole('button',{name:`观察${{upper:'上层',cloud:'云层',rain:'雨层'}[layer]}`,exact:true});
    if(!await layerButton.isVisible())await page.getByText('视图与观察层',{exact:true}).click();
    await layerButton.click();
    await page.waitForTimeout(400);await page.evaluate(()=>window.measure());
    const seconds=layer==='upper'?Number(process.env.HW_D5_SECONDS??120):20;
    const start=performance.now();let h=0;
    for(let i=1;i<Math.min(lines.length,seconds*15+1);i++){
      await sleep(start+i*1000/15-performance.now());
      await page.evaluate(json=>window.deliver(json),lines[i]);
      if(i%300===0){
        await heapSample(`${layer}.${++h*20}s`);
        console.log(JSON.stringify({progress:layer,seconds:h*20}));
      }
      if(i===120)await page.screenshot({path:path.join(out,`${layer}-mixed.png`)});
    }
    reports.push({kind:'recorded-wall-clock',layer,wallSeconds:(performance.now()-start)/1000,...await page.evaluate(()=>window.finish())});
    if(headed&&layer==='upper'){
      await cdp.send('Emulation.setFocusEmulationEnabled',{enabled:false});
      const rootCdp=await browser.newBrowserCDPSession();
      const {targetInfo}=await cdp.send('Target.getTargetInfo');
      const {windowId}=await rootCdp.send('Browser.getWindowForTarget',{targetId:targetInfo.targetId});
      await rootCdp.send('Browser.setWindowBounds',{windowId,bounds:{windowState:'minimized'}});
      await page.waitForFunction(()=>document.hidden,{},{polling:100});
      const before=await viewport.getAttribute('data-display-step');
      // A new scene while minimized exercises visibility cleanup and re-anchor.
      await page.evaluate(json=>window.deliver(json,'.minimized'),lines[0]);
      await page.evaluate(json=>window.deliver(json,'.minimized'),lines[10]);await page.waitForTimeout(300);
      assert.equal(await viewport.getAttribute('data-display-step'),before);
      await rootCdp.send('Browser.setWindowBounds',{windowId,bounds:{windowState:'normal'}});
      await page.bringToFront();
      await page.waitForFunction(()=>!document.hidden,{},{polling:100});
      const expected=JSON.parse(lines[10]).view.fixed_step;
      await page.waitForFunction(step=>Number(document.querySelector('.tactical-canvas').dataset.displayStep)===step,expected);
      await cdp.send('Emulation.setFocusEmulationEnabled',{enabled:true});
      nativeWindow={host:'Microsoft Edge native Windows window; production React/Pixi, not Tauri IPC',hidden:true,stoppedAt:before,resumedAt:expected};
    }
  }
  await page.evaluate(()=>window.empty());await page.waitForTimeout(250);
  assert.equal(await viewport.getAttribute('data-visible-projectiles'),'[]');await heapSample('empty-scene');
  await page.evaluate(()=>window.release());await page.waitForTimeout(250);await heapSample('unmounted');
  assert.equal(await page.locator('canvas').count(),0);
  assert.deepEqual(errors,[]);
  for(const r of reports){assert(r.maxFrames<=32);assert(r.stats.sampling.count>100);}
  const warm=heap.filter(r=>r.label.startsWith('upper.')&&r.label!=='upper.20s');
  const growth=warm.at(-1).usedSize-warm[0].usedSize;
  assert(growth<8*1024*1024,'warmed live heap must not grow by 8 MiB during this bounded run');
  const old=reports[0].stats,current=reports[1].stats;
  // D0 has no frozen drawing P95 budget. Declare this acceptance threshold
  // explicitly: 25% relative or 0.5 ms absolute regression, whichever is larger.
  const thresholds={};
  for(const key of ['sampling','canvasTotal']){
    thresholds[key]=Math.max(old[key].p95Ms*1.25,old[key].p95Ms+.5);
    assert(current[key].p95Ms<=thresholds[key],`${key} P95 regressed beyond stated D5 tolerance`);
  }
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',reports,heap,warmHeapGrowthBytes:growth,thresholds,nativeWindow,errors,
    peakCountersIncludeSetup:true,
    scope:'Same-input dense legacy/current display comparison and 120 s real-time paced upper-layer recorded combat plus cloud/rain. Parse, merge, legal observations, trajectory sample, CPU geometry preparation, CPU render submission, rAF intervals and GC-normalized JS heap. CPU render submission is not GPU time. No real-time backend or 9v9 claim; drawing baseline is current renderer with legacy data, not a preserved pre-D0 executable.'},null,2));
  console.log(JSON.stringify({status:'PASS',reports,warmHeapGrowthBytes:growth,nativeWindow}));
}catch(error){await writeFile(path.join(out,'error.txt'),String(error));
  await writeFile(path.join(out,'incomplete.json'),JSON.stringify({reports,heap,errors},null,2));
  await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});throw error;}
finally{
  if(ownedProcess){
    await (await browser.newBrowserCDPSession()).send('Browser.close').catch(()=>{});
    ownedProcess.kill();
  }
  await browser.close();
}
