// Paired list updates on exactly the same frozen observations, production React.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,mkdir,writeFile,readdir} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
const require=createRequire(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json'));
const {chromium}=require('playwright');
const input=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);await mkdir(out);
const sha=data=>createHash('sha256').update(data).digest('hex');
const runnerSha256=sha(await readFile(new URL(import.meta.url)));
const bundleDir=path.resolve('apps/desktop/dist/target-directory-bench');
const bundleFiles=['target-directory-perf.html',...(await readdir(path.join(bundleDir,'assets'))).map(n=>'assets/'+n)];
const bundleHashes=Object.fromEntries(await Promise.all(bundleFiles.map(async n=>[n,sha(await readFile(path.join(bundleDir,n)))])));
const raw=await readFile(input),frames=JSON.parse(raw),results=[];
const dense=Array.from({length:48},(_,n)=>{
  const frame=structuredClone(frames[Math.floor(n/2)%frames.length]);
  const entries=Array.from({length:1000},(_,i)=>({key:JSON.stringify(['projectile',i]),target_id:i,name:`炮弹 #${i}`,category:'shell'}));
  const sample=frame.ship.contacts[0]??{position_m:[0,0],velocity_mps:[100,0],height_layer:'upper',valid:true,age_s:0,sources:[],status:'tracked',radar_source_available:true};
  frame.directory.entries=entries;frame.directory.remembered=[];
  frame.ship.contacts=entries.filter((_,i)=>i%2===0).map(d=>({...sample,id:d.target_id,kind:'shell',valid:n%3!==0,age_s:n%3===0?1:.1}));
  return frame;
});
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-precise-memory-info']});
function stats(v){v=[...v].sort((a,b)=>a-b);return {count:v.length,meanMs:v.reduce((a,b)=>a+b,0)/v.length,p95Ms:v[Math.floor((v.length-1)*.95)],maxMs:v.at(-1)};}
try{
  for(const size of [{width:1366,height:768},{width:1920,height:1080}]){
    const page=await browser.newPage({viewport:size}),cdp=await page.context().newCDPSession(page),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:1425/target-directory-perf.html');await page.waitForFunction(()=>!!window.__directoryBench);
    await page.evaluate(frame=>window.__directoryBench.mount(frame,'collapsed'),frames[0]);
    assert.equal(await page.locator('.tactical-panel.battle-workspace').count(),1,'Production style wrapper required');
    assert.equal(await page.locator('.battle-tabs button').first().evaluate(el=>getComputedStyle(el).color),'rgb(214, 232, 230)');
    for(const [load,inputs] of [['battle',frames],['dense',dense]]){
      const orders=[['legacy','collapsed','expanded'],['expanded','legacy','collapsed'],['collapsed','expanded','legacy']];
      for(let round=0;round<3;round++)for(const variant of orders[round]){
        await page.evaluate(({frame,variant})=>{window.__directoryBench.clear();window.__directoryBench.mount(frame,variant);},{frame:inputs[0],variant});
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        await cdp.send('HeapProfiler.collectGarbage');const before=await cdp.send('Runtime.getHeapUsage');
        const samples=await page.evaluate(async({inputs,variant})=>{
          for(let n=0;n<12;n++)window.__directoryBench.update(inputs[n%inputs.length],variant);
          const rows=[];
          for(let repeat=0;repeat<3;repeat++)for(const frame of inputs){
            await new Promise(resolve=>requestAnimationFrame(resolve));
            rows.push(window.__directoryBench.update(frame,variant));
          }
          return rows;
        },{inputs,variant});
        await cdp.send('HeapProfiler.collectGarbage');const after=await cdp.send('Runtime.getHeapUsage');
        assert(samples.every(r=>r.scrollWidth<=r.width+1));
        if(variant!=='legacy')assert(samples.every(r=>r.rows<=72));
        const row={size,load,round:round+1,variant,commit:stats(samples.map(s=>s.commitMs)),layout:stats(samples.map(s=>s.layoutMs)),
          total:stats(samples.map(s=>s.commitMs+s.layoutMs)),maxRows:Math.max(...samples.map(s=>s.rows)),maxNodes:Math.max(...samples.map(s=>s.nodes)),before,after,samples};
        results.push(row);console.log(JSON.stringify({size,load,round:round+1,variant,total:row.total,maxRows:row.maxRows}));
        if(round===2)await page.screenshot({path:path.join(out,`${load}-${variant}-${size.width}.png`)});
      }
    }
    assert.deepEqual(errors,[]);await page.close();
  }
  await writeFile(path.join(out,'browser.json'),JSON.stringify({browser:browser.version(),runnerSha256,bundleHashes,inputSha256:sha(raw),results,
    scope:'Production React flushSync commit + forced layout on identical target-only inputs. One update per animation frame, 12 warmups, three serial rotated rounds. Excludes canvas, bridge and GPU completion. Dense load is synthetic: 1000 active shells / 500 contacts, not a battle capacity result.'},null,2)+'\n');
}finally{await browser.close();}
