// Three-layer canvas against the real prepared-battle service. All save data is isolated.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_TACTICAL_LAYERS_OUT??'artifacts/tactical-layers-20260915');
await mkdir(out,{recursive:true});
const store=path.join(out,`store-${Date.now()}`), pending=new Map(), errors=[], checks=[], calls=[], assets=new Set();
let serial=0,scene,browser,page;
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
  await request('system.hello',{client_name:'layers.browser',client_version:'1',
    supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.deploy_prepared']});
  browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.url().includes('/tactical/art/')&&r.ok())assets.add(new URL(r.url()).pathname);});
  await page.addInitScript(()=>{
    const raf=requestAnimationFrame.bind(window);window.__battleFrames=0;
    window.requestAnimationFrame=fn=>raf(t=>{if(fn.name==='animateBattleFrame')window.__battleFrames++;fn(t);});
  });
  await page.exposeFunction('__e3b_request',async req=>{
    calls.push(req.method);const result=await request(req.method,req.params);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene=result;
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true}), canvas=page.locator('.tactical-canvas');
  const observe=layer=>button(`观察${layer}`).click();
  const until=async(predicate,label)=>{const end=Date.now()+15000;while(!predicate()&&Date.now()<end)await page.waitForTimeout(50);assert(predicate(),label);};
  const references=()=>page.locator('.tactical-grid-coordinate').evaluateAll(nodes=>nodes.map(n=>[n.textContent,n.style.left,n.style.top]));
  const countGuns=()=>calls.filter(m=>m==='tactical.realtime.gun').length;
  async function enter(){
    const library=await request('tactical.preparation.library',{}), source=library.sources.find(s=>s.name.includes('常规有人'));
    await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
    await button('从目录设计添加舰船').click();await page.getByLabel('选择准备舰船 1',{exact:true}).waitFor();
    await button('准备所选舰船').click();
    await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('50');
    await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
    await page.getByLabel('武器 1 准备动作',{exact:true}).selectOption('preload');
    await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();
    await button('核对资源与预装填').click();await button('保存准备').click();
    await button('以所选舰为旗舰进入交战').click();await button('开始交战').waitFor();await canvas.locator('canvas').waitFor();
  }
  await page.goto(process.env.HW_TACTICAL_LAYERS_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('准备所选舰船').waitFor();await enter();
  await until(()=>assets.size>=5,'all five independent SVG art resources loaded');
  await page.waitForTimeout(250);
  assert.equal(await button('观察上层').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('.tactical-ship-label:visible').count(),2);
  assert(await page.getByLabel('舰内点选甲板').isVisible());
  const origin=await references();assert(origin.length>0);
  const upper=await canvas.screenshot({path:path.join(out,'upper.png')});
  const before=structuredClone(scene.view), gunCount=countGuns();
  await observe('云层');await page.waitForTimeout(120);
  assert.equal(await page.locator('.tactical-ship-label:visible').count(),0);
  assert.deepEqual(await references(),origin);
  await page.getByRole('status').filter({hasText:'云层暂无已知舰艇'}).waitFor();
  const cloud=await canvas.screenshot({path:path.join(out,'cloud.png')});assert(!cloud.equals(upper));
  await observe('雨层');await page.waitForTimeout(120);
  assert.deepEqual(await references(),origin);
  const pausedRain=await canvas.screenshot({path:path.join(out,'rain-paused.png')});assert(!pausedRain.equals(cloud));
  await page.waitForTimeout(160);assert(pausedRain.equals(await canvas.screenshot()),'paused rain is pixel-stable');
  assert.equal(countGuns(),gunCount);assert.deepEqual(scene.view.ships,before.ships);
  checks.push('Independent light, grey/cloud-foreground, and dark/rain art load; observation changes neither actual ship layers nor gun commands; distance references remain identical across layers');

  for(let n=0;n<4;n++)await button('战术缩小').click();
  await page.waitForTimeout(80);
  await canvas.screenshot({path:path.join(out,'rain-distance-grid.png')});
  assert((await page.locator('.tactical-grid-key').innerText()).includes('小格 50 m'));
  assert((await references()).some(row=>row[0].includes('km')),'distant major references retain kilometre labels');
  const beforePan=await references(), box=await canvas.boundingBox();
  await page.mouse.move(box.x+box.width*.7,box.y+box.height*.6);await page.mouse.down({button:'middle'});
  await page.mouse.move(box.x+box.width*.7+37,box.y+box.height*.6+24);await page.mouse.up({button:'middle'});
  const afterPan=await references();
  for(const row of beforePan){const match=afterPan.find(r=>r[0]===row[0]);if(match){
    const axis=row[0].startsWith('X')?1:2;
    assert(Math.abs(parseFloat(match[axis])-parseFloat(row[axis])-(axis===1?37:24))<.1);
  }}
  checks.push('Zoom preserves the 50 metre cell and 500/5000 metre references; panning moves world-anchored coordinate labels by the exact screen offset');

  await button('返回旗舰所在层').click();await button('适应本层').click();await button('开始交战').click();
  await button('火炮').click();
  await page.getByLabel('所控火炮').selectOption(scene.view.gunnery.weapons.find(g=>g.ship_id===scene.direct_ship_id).module_id);
  await page.getByRole('button',{name:/^瞄准.+/}).first().click();
  await until(()=>scene.view.gunnery.weapons.some(g=>g.ship_id===scene.direct_ship_id&&g.shots>0),'actual discharge');
  const sequence=scene.view.gunnery.command_sequence;
  await observe('雨层');await page.waitForTimeout(100);
  const a=await canvas.screenshot();await page.waitForTimeout(160);const b=await canvas.screenshot({path:path.join(out,'rain-running.png')});
  assert(!a.equals(b),'running rain visibly moves on a layer without ships');
  assert.equal(scene.view.gunnery.command_sequence,sequence);
  assert(scene.view.gunnery.projectiles.every(p=>p.height_layer==='upper'));
  const rect=await canvas.boundingBox(), beforeClick=countGuns();
  await page.mouse.click(rect.x+rect.width/2,rect.y+rect.height/2);await page.waitForTimeout(100);
  assert.equal(countGuns(),beforeClick,'no off-layer click becomes a gun command');
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused');await page.waitForTimeout(120);
  const stopped=await canvas.screenshot(), frames=await page.evaluate(()=>window.__battleFrames);
  await page.waitForTimeout(180);assert(stopped.equals(await canvas.screenshot()));
  assert.equal(await page.evaluate(()=>window.__battleFrames),frames);
  await observe('上层');assert.equal(await page.locator('.tactical-ship-label:visible').count(),2);
  await canvas.screenshot({path:path.join(out,'upper-gunfire.png')});
  checks.push('Actual shells keep their upper-layer identity while observing rain; rain moves visibly during battle, freezes on pause, and off-layer clicks cannot fire into the wrong layer');

  await page.setViewportSize({width:1280,height:860});await button('适应本层').click();await page.waitForTimeout(100);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no horizontal page overflow at desktop default size');
  assert((await canvas.boundingBox()).height>=180);
  await page.screenshot({path:path.join(out,'desktop-layout.png'),fullPage:true});
  checks.push('Default 1280 by 860 desktop layout keeps layer controls, distance legend and the fitted battlefield available without horizontal page overflow');

  await button('清空战术测试数据').click();await button('确认清空并重新开始').click();
  await button('准备所选舰船').waitFor();assert.equal(await canvas.count(),0);
  const released=await page.evaluate(()=>window.__battleFrames);await page.waitForTimeout(150);
  assert.equal(await page.evaluate(()=>window.__battleFrames),released);
  // Small SVGs are embedded by Vite as data URLs. Replace their imported URLs
  // with a missing local image to exercise an actual decode failure on reload.
  await page.route(/\/art\/[^/]+\.svg\?import$/,route=>route.fulfill({contentType:'text/javascript',body:'export default "/missing-tactical-art.svg";'}));
  await page.route('**/missing-tactical-art.svg',route=>route.fulfill({status:404,body:'missing test image'}));
  await page.reload();await button('准备所选舰船').waitFor();
  await enter();await page.getByRole('status').filter({hasText:'部分云海素材暂未载入'}).waitFor();
  assert.equal(await page.locator('.tactical-ship-label:visible').count(),2);
  await button('战术放大').click();await observe('云层');
  assert(await button('返回旗舰所在层').isEnabled());
  checks.push('Reset destroys the canvas and art without a remaining battle animation; a fresh scene still supports observation and camera controls when image resources fail');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_LAYERS_UI_PASS',checks,assets:[...assets],
    scope:'Real prepared battle currently deploys both ships in upper layer; simultaneous three-layer picking and fixed projectile layer semantics are additionally checked in unit tests.'},null,2));
  console.log(JSON.stringify({status:'TACTICAL_LAYERS_UI_PASS',checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{
  await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});
}
