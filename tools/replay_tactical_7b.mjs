// Separately replay actual 7b publications at 15 Hz; never a realtime simulation claim.
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const input=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);await mkdir(out,{recursive:true});
const bytes=await readFile(input),data=JSON.parse(input.endsWith('.gz')?gunzipSync(bytes):bytes);
const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.route('**/7b-replay.html',r=>r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"><script type="module">
    import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
    </script></head><body><div id="root"></div></body></html>`}));
  await page.goto('http://127.0.0.1:1423/7b-replay.html');
  await page.evaluate(async()=>{
    const React=(await import('/node_modules/.vite/deps/react.js')).default,{createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
    const {TacticalViewport}=await import('/src/tactical/TacticalViewport.tsx');
    const {PresentationTimeline}=await import('/src/tactical/presentation.ts');
    const {ProjectileStreamCache}=await import('/src/tactical/projectileStream.ts');
    const {observedBattleView}=await import('/src/tactical/observedView.ts');
    const {Application}=await import('/src/rendering/pixi.ts');
    const root=createRoot(document.getElementById('root')),cache=new ProjectileStreamCache();let geometry,record=false,last,maxFlights=0,maxFrames=0,maxDisplayed=0;
    const metrics={},push=(k,v)=>{if(record)(metrics[k]??=[]).push(v);};
    const sample=PresentationTimeline.prototype.sample;PresentationTimeline.prototype.sample=function(...a){const start=performance.now(),r=sample.apply(this,a);push('sampling',performance.now()-start);maxFrames=Math.max(maxFrames,this.frames.length);maxDisplayed=Math.max(maxDisplayed,r?.snapshot.gunnery?.projectiles.length??0);return r;};
    const render=Application.prototype.render;Application.prototype.render=function(...a){const start=performance.now(),r=render.apply(this,a);push('renderSubmission',performance.now()-start);return r;};
    const raf=window.requestAnimationFrame.bind(window);window.requestAnimationFrame=callback=>raf(now=>{if(callback.name!=='animateBattleFrame'){callback(now);return;}if(last!==undefined)push('frameInterval',now-last);last=now;const start=performance.now();callback(now);push('frameCPU',performance.now()-start);});
    window.setup=g=>{geometry=g;};
    window.deliver=json=>{
      let start=performance.now();const packet=JSON.parse(json);push('parse',performance.now()-start);packet.view.paused=false;
      start=performance.now();const decoded=cache.apply({geometry,snapshot:packet.view});push('merge',performance.now()-start);maxFlights=Math.max(maxFlights,cache.flights.size);
      const own=decoded.snapshot.control_state?.direct_ship_id??geometry.ships[0].id;
      start=performance.now();const view=observedBattleView(decoded,own,own);push('observation',performance.now()-start);
      root.render(React.createElement(TacticalViewport,{view,active:true,selected:own,onSelect:()=>{},overlay:true}));
    };
    window.measure=()=>{record=true;last=undefined;};
    window.finish=()=>{record=false;return {metrics,maxFlights,maxFrames,maxDisplayed,remainingFlights:cache.flights.size};};
    window.release=()=>{cache.clear();geometry=null;root.unmount();};
  });
  await page.evaluate(g=>window.setup(g),data.geometry);await page.evaluate(v=>window.deliver(v),JSON.stringify(data.initial));
  await page.locator('.tactical-canvas canvas').waitFor();await page.waitForTimeout(300);
  const cdp=await page.context().newCDPSession(page),heap=[];
  async function mem(label){await cdp.send('HeapProfiler.collectGarbage');heap.push({label,...await cdp.send('Runtime.getHeapUsage'),...await cdp.send('Memory.getDOMCounters')});}
  await mem('before');await page.evaluate(()=>window.measure());const started=performance.now();
  for(let i=0;i<data.packets.length;i++){
    await new Promise(r=>setTimeout(r,Math.max(0,started+i*1000/15-performance.now())));
    await page.evaluate(v=>window.deliver(v),JSON.stringify(data.packets[i]));
  }
  const result=await page.evaluate(()=>window.finish());await page.screenshot({path:path.join(out,'replay.png')});await mem('last');
  await page.evaluate(()=>window.release());await mem('released');
  const stats=v=>{v.sort((a,b)=>a-b);return {count:v.length,meanMs:v.reduce((a,b)=>a+b,0)/v.length,p95Ms:v[Math.floor((v.length-1)*.95)],maxMs:v.at(-1)};};
  result.metrics=Object.fromEntries(Object.entries(result.metrics).map(([k,v])=>[k,stats(v)]));
  const report={input,scope:'15 Hz recorded production viewport / legal player observation / headless Edge / Vite dev; excludes live backend and native GPU.',browser:browser.version(),...result,heap,errors};
  await writeFile(path.join(out,'replay.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
  if(errors.length)throw new Error(errors.join('\n'));
}finally{await browser.close();}
