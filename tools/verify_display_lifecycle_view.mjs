// D4: production Pixi pixels and visibility/lifetime handling in a controlled
// renderer fixture. UI/authority integration is verified separately.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright'),sharp=require('sharp');
const out=path.resolve(process.env.HW_DISPLAY_VISUAL_OUT??'artifacts/projectile-display-d4-visual-20260921');
await mkdir(out,{recursive:true});
const fixture=JSON.parse(await readFile('apps/desktop/src/tactical/testing/snapshot.fixture.json','utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});const checks=[],errors=[];
const specs=[[-100,60,1],[100,60,3],[-100,-60,1],[100,-60,3],[-100,140,0],[100,140,0]];
try{
  for(const dpr of [1,2]){
    const page=await browser.newPage({viewport:{width:1200,height:900},deviceScaleFactor:dpr});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/d4-visual.html',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"><script type="module">
      import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
      </script></head><body><div id="root"></div></body></html>`}));
    await page.goto('http://127.0.0.1:1431/d4-visual.html');
    await page.evaluate(async({fixture,specs})=>{
      const React=(await import('/node_modules/.vite/deps/react.js')).default;
      const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
      const {TacticalViewport}=await import('/src/tactical/TacticalViewport.tsx');
      const {acceptSnapshot}=await import('/src/tactical/model.ts');
      const base=acceptSnapshot(null,fixture,'fixture.1'),root=createRoot(document.getElementById('root'));
      const own=base.geometry.ships[0].id,enemy=base.geometry.ships[1].id;
      window.paint=(layer='upper',trails=true,step=8,paused=true,empty=false,scene='fixture.d4')=>{
        const projectiles=empty?[]:specs.map(([x,y,size],i)=>{
          const missile=i>=4,ship_id=i<2||i===4?own:enemy;
          const p={id:i+1,kind:missile?'missile':'shell',ship_id,maximum_durability:size===1?null:3,
            position_m:[x,y],previous_m:[x,y],velocity_mps:[600,0],height_layer:layer,
            missile:missile?{phase:'powered'}:undefined};
          if(trails){
            Object.assign(p,{born_step:step-8,expires_step:step+100,origin_m:[x-80,y]});
            const samples=[[step-8,x-80,y,600,0,1],[step-4,x-40,y,600,0,0],[step,x,y,600,0,0]];
            if(missile)Object.assign(p,{missile_samples:samples.map(v=>[...v,10000,0,0,0]),
              missile_identity:{model_id:'fixture',warhead_id:'blast',interceptor:i===5,born_step:step-8},
              missile_states:[{step:step-8,height_layer:layer,phase:'powered',seeker_state:'tracking',target_id:'test',maneuver_state:'level',maneuver_reason:null,maneuver_target_layer:null,vertical_goal:false}]});
            else p.shell_samples=samples;
          }
          return p;
        });
        const view={...base,snapshot:{...base.snapshot,scene_id:scene,ships:[],fixed_step:step,time_s:step/60,paused,
          gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1',command_sequence:0,policy_id:'test',damage_enabled:true,weapons:[],projectiles}}};
        root.render(React.createElement(TacticalViewport,{view,active:true,selected:own,onSelect:()=>{}}));
      };
      window.paint();
    },{fixture,specs});
    const host=page.locator('.tactical-canvas');await host.locator('canvas').waitFor();
    await page.waitForFunction(()=>document.querySelector('.tactical-canvas')?.dataset.camera);
    await page.waitForTimeout(500); // finish local atmospheric texture loading
    const pixels=async buffer=>sharp(buffer).removeAlpha().raw().toBuffer({resolveWithObject:true});
    for(const [layer,label] of [['upper','上层'],['cloud','云层'],['rain','雨层']]){
      await page.evaluate(layer=>window.paint(layer),layer);await page.getByRole('button',{name:`观察${label}`,exact:true}).click();
      for(let zoom=0;zoom<2;zoom++){
        if(zoom)await page.getByRole('button',{name:'战术放大',exact:true}).click();
        await page.waitForTimeout(80);
        const camera=await host.evaluate(e=>JSON.parse(e.dataset.camera));
        const file=path.join(out,`${layer}-dpr${dpr}-zoom${zoom}.png`),shot=await host.screenshot({path:file});
        const {data,info}=await pixels(shot);
        await page.evaluate(layer=>window.paint(layer,false),layer);await page.waitForTimeout(80);
        const baseline=(await pixels(await host.screenshot({path:path.join(out,`${layer}-dpr${dpr}-zoom${zoom}-baseline.png`)}))).data;
        const rows=[];
        for(let i=0;i<specs.length;i++){
          const [x,y,size]=specs[i],cx=Math.round(camera.x+x*camera.scale),cy=Math.round(camera.y-y*camera.scale);
          const px=Math.floor((camera.x+(x-18)*camera.scale)*dpr);let coverage=0,changedRows=0;
          for(let py=(cy-6)*dpr;py<(cy+7)*dpr;py++){
            const index=(py*info.width+px)*info.channels,b=baseline[index+2],a=(b-data[index+2])/Math.max(1,b);
            if(a<.03)continue;
            changedRows++;coverage+=a;
            // MSAA can cover different texture subsamples at a grid boundary.
            for(const channel of [0,1])assert(Math.abs(data[index+channel]-(baseline[index+channel]*(1-a)+255*a))<12,`yellow blend ${layer}/${dpr}/${i}: ${[...data.slice(index,index+3)]} vs ${[...baseline.slice(index,index+3)]}, alpha ${a}`);
          }
          assert(Math.abs(coverage-dpr)<.15*dpr,`${layer} dpr${dpr} trail${i} coverage ${coverage}`);
          assert(changedRows<=dpr+1,'single CSS pixel plus edge antialiasing only');
          if(size){
            const color=i<2?[40,125,255]:[240,68,82];let count=0;
            for(let yy=(cy-6)*dpr;yy<(cy+7)*dpr;yy++)for(let xx=(cx-6)*dpr;xx<(cx+7)*dpr;xx++){
              const at=(yy*info.width+xx)*info.channels;if(color.every((v,k)=>data[at+k]===v))count++;
            }
            assert.equal(count,(size===1?1:8)*dpr*dpr,'original red/blue marker');
          }
          rows.push({kind:i<4?'shell':i===4?'missile':'interceptor',coverageCssPixels:coverage/dpr,changedRows});
        }
        checks.push({layer,dpr,zoom,rows});
        await page.evaluate(layer=>window.paint(layer),layer);
      }
      await page.getByRole('button',{name:'战术缩小',exact:true}).click();
    }
    // Exercise the same visibilitychange handler as window/tab hiding. This is
    // a synthetic visibility event, not a native Windows minimization claim.
    await page.evaluate(()=>{window.paint('upper',true,12,false);});
    await page.getByRole('button',{name:'观察上层',exact:true}).click();await page.waitForTimeout(150);
    const before=await host.getAttribute('data-display-step');
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));window.paint('upper',true,80,false);});
    await page.waitForTimeout(180);assert.equal(await host.getAttribute('data-display-step'),before,'hidden canvas stops drawing');
    await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));});
    await page.waitForFunction(()=>document.querySelector('.tactical-canvas').dataset.displayStep==='80');
    await page.evaluate(()=>window.paint('upper',true,80,true,true));await page.waitForTimeout(100);
    assert.equal(await host.getAttribute('data-visible-projectiles'),'[]','finished/cleared scene has no ghosts');
    await page.evaluate(()=>window.paint('upper',true,0,true,true,'scene.next'));await page.waitForTimeout(100);
    assert.equal(await host.getAttribute('data-display-step'),'0');assert.equal(await host.getAttribute('data-visible-projectiles'),'[]');
    await page.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',checks,lifecycle:'synthetic visibility hide/resume, explicit empty/next scene, reused renderer',scope:'Actual production Pixi screenshot difference pixels: yellow 1 CSS px coverage for shells/missiles/interceptors; red/blue dots/frames remain. Three layers, two zoom levels, DPR 1/2.'},null,2));
  console.log(JSON.stringify({status:'PASS',cases:checks.length}));
}finally{await browser.close();}
