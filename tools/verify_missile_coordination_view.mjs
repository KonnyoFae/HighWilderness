// Actual React commands and Python simulation; no player storage is used.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const mode=process.env.HW_COORDINATION_MODE??'retarget';
const out=path.resolve(process.env.HW_COORDINATION_OUT??`artifacts/tactical-missile-coordination-5j3-${mode}-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,`store-${Date.now()}`);
const args=['-X','utf8','-m','tools.missile_coordination_browser_fixture','--mode',mode,'--settlement-dir',store];
const setup=spawnSync('python',[...args,'--setup'],{encoding:'utf8',windowsHide:true});assert.equal(setup.status,0,setup.stderr);
let backend,browser,page,serial=0,live,packet;const pending=new Map(),errors=[],checks=[],seen=[];
backend=spawn('python',args,{windowsHide:true});backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
function request(method,params){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id:null,expected_revision:null})+'\n');
});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
try{
  await request('system.hello',{client_name:'coordination.5j3.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.missile']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1500,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const r=await request(req.method,req.params);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      live=r;
      for(const p of r.view.gunnery?.projectiles??[])if(p.missile&&p.ship_id===r.direct_ship_id)seen.push({id:p.id,layer:p.height_layer,expires:p.missile.age_s+p.missile.remaining_s});
    }
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===2,'two fleets');
  if(mode==='retarget'){
    await page.getByLabel('初始交战距离（公里）',{exact:true}).fill('16');await button('应用距离').click();
  }
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'所选舰艇'}),tab=async name=>inspector.getByRole('button',{name,exact:true}).click();
  const guns=()=>live?.view.gunnery,ship=()=>guns()?.missiles.ships.find(s=>s.ship_id===live.direct_ship_id);
  const ownFlights=()=>guns()?.projectiles.filter(p=>p.missile&&p.ship_id===live.direct_ship_id)??[];
  await tab('导弹');
  if(mode==='retarget'){
    const observation=()=>guns()?.observation.ships.find(s=>s.ship_id===live.direct_ship_id);
    await until(()=>observation()?.contacts.some(c=>c.kind==='ship'&&c.valid&&c.height_layer==='cloud'),'observed cloud enemy');
    const target=observation().contacts.find(c=>c.kind==='ship'&&c.valid);
    // Existing point command establishes a same-layer launch. The new retarget
    // action below is issued entirely through the actual visible selector.
    await request('tactical.realtime.missile',{scene_id:live.status.epoch,input:{epoch:live.status.epoch,generation:0,
      sequence:guns().missiles.command_sequence+1,ship_id:live.direct_ship_id,
      order:{kind:'point',module_id:'weapon_upper_port',point_m:target.position_m}}});
    await until(()=>ship().launchers[0].point_m,'point launch reference');
    const before=ship().state.launchers[0].ready.length;
    await button('单发导弹').click();await until(()=>ownFlights().length===1,'real VLS emergence');
    const flight=ownFlights()[0];assert.equal(flight.height_layer,'upper');assert(flight.missile.datalink);
    await inspector.getByRole('button',{name:/^在途制导/}).click();
    const selector=page.getByLabel(`导弹 ${flight.id} 数据链改攻`,{exact:true});
    assert((await selector.locator(`option[value="${target.id}"]`).innerText()).includes('云层'));
    await selector.selectOption(target.id);
    await until(()=>ownFlights().some(p=>p.missile.link_sender&&p.missile.maneuver_state==='diving'),'retarget uses valid cross-layer link');
    await button('暂停交战').click();await until(()=>!live.status.running,'paused descent');
    const changed=ownFlights().find(p=>p.id===flight.id);
    assert.equal(changed.missile.age_s+changed.missile.remaining_s,flight.missile.age_s+flight.missile.remaining_s);
    assert.equal(changed.missile.target_id,null);assert.equal(changed.height_layer,'upper');
    assert.equal(changed.missile.maneuver_target_layer,'cloud');assert.equal(ship().launchers[0].shots,1);
    assert.equal(ship().state.launchers[0].ready.length,before-1);
    await inspector.locator(`[data-flight="${flight.id}"]`).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,'cross-layer-retarget.png'),fullPage:true});
    checks.push('A real same-layer VLS departure is retargeted to an observed cloud target through the visible data-link selector, starts physical descent without a seeker lock, keeps its fixed deadline and spends exactly one round.');
  }else{
    assert.equal(await page.getByLabel('导弹作用层',{exact:true}).inputValue(),'auto');
    await until(()=>ownFlights().some(p=>p.missile.maneuver_state==='diving'),'automatic interceptor follows into cloud');
    await tab('火控');
    await page.screenshot({path:path.join(out,'observed-altitude.png'),fullPage:true});
    assert((await inspector.innerText()).includes('观测高度'));
    await tab('导弹');await inspector.getByRole('button',{name:/^在途制导/}).click();
    await page.screenshot({path:path.join(out,'interceptor-layer-pursuit.png'),fullPage:true});
    await until(()=>guns().point_defense.recent.some(e=>e.projectile_id===10000&&e.intercepted),'real cloud interception');
    const impact=guns().point_defense.recent.find(e=>e.projectile_id===10000&&e.intercepted);
    assert.equal(impact.height_layer,'cloud');assert.equal(ship().launchers[0].shots,1);
    assert.deepEqual([...new Set(seen.map(p=>p.id))],[impact.round_id]);assert.deepEqual(new Set(seen.map(p=>p.layer)),new Set(['upper','cloud']));
    assert(new Set(seen.map(p=>p.expires)).size===1);
    checks.push('Real radar/IR displays measured altitude and vertical motion. The automatic launcher selects the legal adjacent layer; one actual interceptor follows the layer change and destroys the threat in cloud without a duplicate shot or TTL reset.');
  }
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_MISSILE_COORDINATION_5J3_UI_PASS',mode,checks},null,2));
  console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
