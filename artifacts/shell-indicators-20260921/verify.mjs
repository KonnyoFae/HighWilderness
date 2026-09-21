import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright'), sharp=require('sharp');
const out=path.dirname(fileURLToPath(import.meta.url));
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'}), results=[];
try {
  for(const dpr of [1,2]) {
    const page=await browser.newPage({viewport:{width:1100,height:850},deviceScaleFactor:dpr}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:1431/t2a-test.html');
    await page.evaluate(async()=>{
      const {createElement}=(await import('/node_modules/.vite/deps/react.js')).default;
      const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
      const {TacticalViewport}=await import('/src/tactical/TacticalViewport.tsx');
      const {observedBattleView}=await import('/src/tactical/observedView.ts');
      const snapshot=structuredClone((await import('/src/tactical/testing/snapshot.fixture.json')).default);
      const host=document.createElement('div');host.id='shell-check';host.style.cssText='position:fixed;inset:0;background:#081b20;z-index:10000';document.body.append(host);
      const root=createRoot(host),geometry=snapshot.static,own=geometry.ships.find(s=>s.side_id==='side.blue').id,enemy=geometry.ships.find(s=>s.id!==own).id;
      let view={geometry,snapshot};
      const render=()=>root.render(createElement(TacticalViewport,{view,active:true,selected:own,onSelect:()=>{}}));
      render();
      window.placeShells=layer=>{
        const camera=JSON.parse(host.querySelector('.tactical-canvas').dataset.camera);
        const positions=[[210,220],[260,220],[310,220],[360,220],[410,220]];
        const projectiles=positions.map(([x,y],i)=>({id:i+1,kind:'shell',ship_id:i<2?own:enemy,
          position_m:[(x-camera.x)/camera.scale,(camera.y-y)/camera.scale],previous_m:[(x-camera.x)/camera.scale,(camera.y-y)/camera.scale],velocity_mps:[100,0],height_layer:layer,maximum_durability:i===0||i===2?null:3}));
        const contact={id:4,kind:'shell',position_m:projectiles[3].position_m,velocity_mps:[100,0],height_layer:layer,valid:true,age_s:0,sources:['sensor'],status:'tracked',radar_source_available:true};
        view=observedBattleView({...view,snapshot:{...view.snapshot,ships:snapshot.ships,gunnery:{interface:'gaotian.gunnery-view/p2a-v1alpha1',command_sequence:0,policy_id:'fixture',damage_enabled:false,weapons:[],projectiles,
          observation:{command_sequence:0,sample_interval_s:.2,memory_s:5,ships:[{ship_id:own,locked_target_id:null,lock_status:null,devices:[],contacts:[contact]}]}}}},own,own);
        render();return view.snapshot.gunnery.projectiles.map(p=>({id:p.id,has_durability:p.has_durability,position_m:p.position_m}));
      };
    });
    const canvas=page.locator('#shell-check .tactical-canvas');await canvas.locator('canvas').waitFor();
    await page.waitForFunction(()=>document.querySelector('#shell-check .tactical-canvas')?.dataset.camera);
    for(const [layer,label] of [['upper','上层'],['cloud','云层'],['rain','雨层']]) {
      await page.locator('#shell-check').getByRole('button',{name:`观察${label}`,exact:true}).click();
      for(const zoom of [0,1]) {
        if(zoom)await page.locator('#shell-check').getByRole('button',{name:'战术放大',exact:true}).click();
        await page.waitForTimeout(80);const shown=await page.evaluate(layer=>window.placeShells(layer),layer);
        assert.deepEqual(shown.map(p=>p.id),[1,2,3,4]);assert.equal(shown[3].has_durability,true);
        await page.waitForTimeout(100);
        const png=await canvas.locator('canvas').screenshot();
        const {data,info}=await sharp(png).removeAlpha().raw().toBuffer({resolveWithObject:true});
        const counts=[];
        for(const [i,x] of [210,260,310,360,410].entries()) {
          const color=i<2?[40,125,255]:[240,68,82];let count=0;
          for(let y=215*dpr;y<226*dpr;y++)for(let xx=(x-5)*dpr;xx<(x+6)*dpr;xx++){
            const n=(y*info.width+xx)*info.channels;
            if(color.every((c,k)=>Math.abs(c-data[n+k])<=1))count++;
          }
          counts.push(count);assert.equal(count,[1,8,1,8,0][i]*dpr*dpr,`${layer} DPR ${dpr} zoom ${zoom} indicator ${i}`);
        }
        results.push({layer,dpr,zoom,coloredPixels:counts});
        if(!zoom&&dpr===1)await writeFile(path.join(out,`${layer}.png`),png);
      }
    }
    assert.deepEqual(errors,[]);await page.close();
  }
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',scope:'Real tactical canvas with synthetic paused projectile/observation states; three weather layers, two zoom levels, DPR 1 and 2; hidden large enemy shell excluded.',results},null,2));
  console.log(JSON.stringify({status:'PASS',cases:results.length}));
}finally{await browser.close();}
