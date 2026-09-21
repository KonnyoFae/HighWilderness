// Real TacticalViewport/Pixi rendering of D2 shell curves, with pixel checks.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright'),sharp=require('sharp');
const out=path.resolve(process.env.HW_SHELL_VISUAL_OUT??'artifacts/projectile-display-d2-visual-20260921');
await mkdir(out,{recursive:true});
const fixture=JSON.parse(await readFile('apps/desktop/src/tactical/testing/snapshot.fixture.json','utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});
const checks=[],errors=[];
try{
  for(const dpr of [1,2]){
    const page=await browser.newPage({viewport:{width:1200,height:900},deviceScaleFactor:dpr});
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/d2-visual.html',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"><script type="module">
      import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
      </script></head><body><div id="root"></div></body></html>`}));
    await page.goto('http://127.0.0.1:1431/d2-visual.html');
    await page.evaluate(async fixture=>{
      const React=(await import('/node_modules/.vite/deps/react.js')).default;
      const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
      const {TacticalViewport}=await import('/src/tactical/TacticalViewport.tsx');
      const {acceptSnapshot}=await import('/src/tactical/model.ts');
      const base=acceptSnapshot(null,fixture,'fixture.1'),root=createRoot(document.getElementById('root'));
      const own=base.geometry.ships[0].id,enemy=base.geometry.ships[1].id;
      window.setShellLayer=layer=>{
        const projectiles=[[-100,60,own,null],[100,60,own,3],[-100,-60,enemy,null],[100,-60,enemy,3]].map(([x,y,ship_id,hp],i)=>({
          id:i+1,kind:'shell',ship_id,maximum_durability:hp,position_m:[x,y],previous_m:[x-36,y],velocity_mps:[600,0],
          born_step:0,expires_step:100,height_layer:layer,origin_m:[x-80,y],
          shell_samples:[[0,x-80,y,600,0,1],[4,x-40,y,600,0,0],[8,x,y,600,0,0]]}));
        const view={...base,snapshot:{...base.snapshot,ships:[],fixed_step:8,time_s:8/60,paused:true,
          gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1',command_sequence:0,policy_id:'test',damage_enabled:true,weapons:[],projectiles}}};
        root.render(React.createElement(TacticalViewport,{view,active:true,selected:own,onSelect:()=>{}}));
      };
      window.setShellLayer('upper');
    },fixture);
    const host=page.locator('.tactical-canvas');
    await host.locator('canvas').waitFor();await page.waitForFunction(()=>document.querySelector('.tactical-canvas')?.dataset.camera);
    for(const [layer,label] of [['upper','上层'],['cloud','云层'],['rain','雨层']]){
      await page.evaluate(layer=>window.setShellLayer(layer),layer);
      await page.getByRole('button',{name:`观察${label}`,exact:true}).click();
      for(let zoom=0;zoom<2;zoom++){
        if(zoom)await page.getByRole('button',{name:'战术放大',exact:true}).click();
        await page.waitForTimeout(80);
        const camera=await host.evaluate(e=>JSON.parse(e.dataset.camera));
        const file=path.join(out,`${layer}-dpr${dpr}-zoom${zoom}.png`),buffer=await host.screenshot({path:file});
        const {data,info}=await sharp(buffer).removeAlpha().raw().toBuffer({resolveWithObject:true});
        const markerChecks=[];
        for(const [x,y,color,size] of [[-100,60,[40,125,255],1],[100,60,[40,125,255],3],[-100,-60,[240,68,82],1],[100,-60,[240,68,82],3]]){
          const cx=Math.round(camera.x+x*camera.scale),cy=Math.round(camera.y-y*camera.scale);
          const points=[];
          for(let py=(cy-6)*dpr;py<(cy+7)*dpr;py++)for(let px=(cx-6)*dpr;px<(cx+7)*dpr;px++){
            const index=(py*info.width+px)*info.channels;
            if(color.every((v,i)=>data[index+i]===v))points.push([px,py]);
          }
          assert.equal(points.length,(size===1?1:8)*dpr*dpr,`${layer} DPR${dpr} ${size}px color`);
          const width=Math.max(...points.map(p=>p[0]))-Math.min(...points.map(p=>p[0]))+1;
          const height=Math.max(...points.map(p=>p[1]))-Math.min(...points.map(p=>p[1]))+1;
          assert.equal(width,size*dpr);assert.equal(height,size*dpr);
          markerChecks.push({size,physicalPixels:points.length,width,height});
        }
        checks.push({layer,dpr,zoom,camera,markers:markerChecks});
      }
      await page.getByRole('button',{name:'战术缩小',exact:true}).click();
    }
    await page.close();
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',checks,scope:'Production TacticalViewport with D2 local trails; three weather layers, two zoom levels and DPR 1/2. 1px dots and hollow 3x3 frames checked from rendered screenshot pixels.'},null,2));
  console.log(JSON.stringify({status:'PASS',cases:checks.length}));
}finally{await browser.close();}
