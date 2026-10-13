// 7c.4b paced live page, command receipts and canvas submission timing.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const source=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);
const fixed=process.argv.includes('--fixed'),seconds=Number(process.argv[process.argv.indexOf('--seconds')+1])||30;
await mkdir(out,{recursive:true});
const pending=new Map(),errors=[],requests=[],parseMs=[];let serial=0,live,record=false;
const backend=spawn('python',['-X','utf8','-m','tools.tactical_7c4b_live',source,out,...(fixed?['--fixed']:[])],{windowsHide:true});
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const start=performance.now(),v=JSON.parse(line);if(record)parseMs.push(performance.now()-start);const p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
function request(req){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${req.method}: ${errors.join('')}`)),60000);
  pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',session_id:null,expected_revision:null,...req,request_id})+'\n');
});}
const browser=await chromium.launch({channel:'msedge',headless:true,args:['--enable-precise-memory-info']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(60000);
const cdp=await page.context().newCDPSession(page);page.on('pageerror',e=>errors.push(e.message));
const heap=[];async function memory(label){await cdp.send('HeapProfiler.collectGarbage');heap.push({label,...await cdp.send('Runtime.getHeapUsage'),...await cdp.send('Memory.getDOMCounters')});}
try{
  await request({method:'system.hello',params:{client_name:'7b.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.aviation']}});
  await page.addInitScript(()=>{
    window.__7b={record:false,metrics:{},push(k,v){if(this.record)(this.metrics[k]??=[]).push(v);}};
    const raf=window.requestAnimationFrame.bind(window);let last;
    window.requestAnimationFrame=callback=>raf(now=>{
      if(callback.name!=='animateBattleFrame'){callback(now);return;}
      if(last!==undefined)window.__7b.push('frameInterval',now-last);last=now;
      const start=performance.now();callback(now);window.__7b.push('frameCPU',performance.now()-start);
    });
  });
  await page.exposeFunction('__e3b_request',async req=>{
    const start=performance.now(),r=await request(req);if(record)requests.push({method:req.method,ms:performance.now()-start,bytes:Buffer.byteLength(JSON.stringify(r))});
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await page.evaluate(()=>{
    window.__7b.commands=[];window.__7b.lastSample=null;
    const request=window.__e3b_request;
    window.__e3b_request=async req=>{
      const start=performance.now();
      let command;
      if(req.method==='tactical.realtime.control'){
        command={sequence:req.params.input.sequence,targetStep:req.params.input.target_step,submitted:start};window.__7b.commands.push(command);
      }
      try{
        const result=await request(req);
        if(command){command.replyMs=performance.now()-start;command.replyStatus=result.status;}
        for(const receipt of result.receipts??[]){
          const c=window.__7b.commands.find(c=>c.sequence===receipt.sequence&&!c.error);
          if(c&&receipt.status!=='accepted'&&c.receiptMs===undefined){
            c.receiptMs=performance.now()-c.submitted;c.status=receipt.status;c.resolvedStep=receipt.receipt?.resolved_at_step;
            requestAnimationFrame(()=>requestAnimationFrame(()=>{
              if([...document.querySelectorAll('[role="status"]')].some(el=>el.textContent.includes(`操纵 ${c.sequence}：已执行`)))c.domMs=performance.now()-c.submitted;
            }));
          }
        }
        return result;
      }catch(error){if(command)command.error=String(error);throw error;}
      finally{window.__7b.push('browserBridge.'+req.method,performance.now()-start);}
    };
  });
  await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await page.evaluate(async()=>{
    const {PresentationTimeline}=await import('/src/tactical/presentation.ts');
    const {ProjectileStreamCache}=await import('/src/tactical/projectileStream.ts');
    const {Application}=await import('/src/rendering/pixi.ts');
    for(const [cls,name,label] of [[PresentationTimeline,'sample','sampling'],[ProjectileStreamCache,'apply','merge'],[Application,'render','renderSubmission']]){
      const fn=cls.prototype[name];cls.prototype[name]=function(...args){const start=performance.now();try{
        const result=fn.apply(this,args);
        if(label==='sampling')window.__7b.lastSample=result?.snapshot;
        if(label==='renderSubmission')for(const c of window.__7b.commands){
          if(c.status==='executed'&&c.canvasMs===undefined&&window.__7b.lastSample?.fixed_step>=c.resolvedStep){
            c.canvasMs=performance.now()-c.submitted;c.shownStep=window.__7b.lastSample.fixed_step;
          }
        }
        return result;
      }finally{window.__7b.push(label,performance.now()-start);}};
    }
  });
  await memory('paused-entry');
  const selectionMs=await page.locator('[data-fleet-ship]').nth(1).evaluate(el=>new Promise(resolve=>{
    const start=performance.now();el.click();requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.now()-start)));
  }));
  await page.locator('[data-fleet-ship]').first().click();
  if(!fixed)await button('展开旗舰操纵').click();
  await page.evaluate(()=>{window.__7b.record=true;window.__7b.metrics={};});
  record=true;await button('开始交战').click();const start=performance.now();
  let nextCommand=performance.now()+1500,commandCount=0;
  while(performance.now()-start<seconds*1000){
    await page.waitForTimeout(100);
    if(live?.status.pause_reason==='overload'||live?.error||live?.settlement)break;
    if(!fixed&&performance.now()>=nextCommand&&commandCount<6&&await button('右转半速').isEnabled()){
      await button('右转半速').click();commandCount++;nextCommand=performance.now()+2500;
    }
  }
  const wallMs=performance.now()-start,finalStatus=live.status;
  const runningText=await page.locator('body').innerText();
  await page.screenshot({path:path.join(out,'running.png'),fullPage:true});
  if(live.status.running)await button('暂停交战').click();
  await page.waitForTimeout(150);
  const metrics=await page.evaluate(()=>{window.__7b.record=false;return window.__7b.metrics;});
  const commands=await page.evaluate(()=>window.__7b.commands);
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});await memory('paused-end');
  await page.goto('about:blank');await memory('unmounted');
  function stats(v){v=[...v].sort((a,b)=>a-b);return {count:v.length,meanMs:v.length?v.reduce((a,b)=>a+b,0)/v.length:null,p95Ms:v.length?v[Math.floor((v.length-1)*.95)]:null,maxMs:v.at(-1)??null};}
  const groups={};for(const r of requests)(groups[r.method]??=[]).push(r.ms);
  const report={source,variant:fixed?'fixed':'adaptive',scope:'Headless Edge / Vite / Node bridge. Adaptive run includes up to six UI helm orders, so its state is not compared with the fixed run. Command timings start at browser bridge submission; canvas timing means render submission after the resolved authority step, not native/GPU completion. All latency is wall milliseconds.',browser:browser.version(),wallMs,finalStatus,commands,
    commandSummary:{submitted:commands.length,rejected:commands.filter(c=>c.error).length,executed:commands.filter(c=>c.status==='executed').length,
      reply:stats(commands.filter(c=>Number.isFinite(c.replyMs)).map(c=>c.replyMs)),receipt:stats(commands.filter(c=>Number.isFinite(c.receiptMs)).map(c=>c.receiptMs)),
      dom:stats(commands.filter(c=>Number.isFinite(c.domMs)).map(c=>c.domMs)),canvas:stats(commands.filter(c=>Number.isFinite(c.canvasMs)).map(c=>c.canvasMs))},
    runningText,nodeResponseParse:stats(parseMs),
    selectionClickThroughTwoFramesMs:selectionMs,metrics:Object.fromEntries(Object.entries(metrics).map(([k,v])=>[k,stats(v)])),
    requests:Object.fromEntries(Object.entries(groups).map(([k,v])=>[k,stats(v)])),peakResponseBytes:Math.max(...requests.map(r=>r.bytes)),heap,errors};
  await writeFile(path.join(out,'browser.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
  assert.equal(errors.length,0,errors.join('\n'));
}catch(e){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());throw e;}
finally{await browser.close();backend.stdin.end();for(const p of pending.values())clearTimeout(p.timer);if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);setTimeout(()=>{backend.kill();resolve();},3000);});}
