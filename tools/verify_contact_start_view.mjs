// Real React/Python preparation, automatic contact, first frame and restart.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_CONTACT_OUT??`artifacts/tactical-contact-${Date.now()}`);
await mkdir(out,{recursive:true});
let backend,browser,page,packet,live,serial=0;
const pending=new Map(),errors=[],checks=[],entries=[];
function startBackend(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser',
    '--settlement-dir',path.join(out,'store'),'--recovery-dir',path.join(out,'recovery')],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  backend.on('exit',code=>{for(const wait of pending.values()){clearTimeout(wait.timer);wait.reject(new Error(`sidecar exited: ${code}; ${errors.join('\n')}`));}pending.clear();});
  createInterface({input:backend.stdout}).on('line',line=>{
    const response=JSON.parse(line),wait=pending.get(response.request_id);
    if(!wait){if(response.error)errors.push(JSON.stringify(response.error));return;}
    clearTimeout(wait.timer);pending.delete(response.request_id);
    if(response.ok===false||response.error)wait.reject(new Error(JSON.stringify(response.error)));
    else wait.resolve(response.result);
  });
}
async function stopBackend(){
  if(backend?.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});
}
function request(method,params={},session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`;
  const timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`timeout ${method}`));},60000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    request_id,method,params,session_id,expected_revision})+'\n');
});}
const hello=()=>request('system.hello',{client_name:'contact.ui',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:[]});
try{
  startBackend();await hello();
  const index=await request('resource.list'),source=index.resources.find(r=>r.id==='gtw.outfit.fixture.stage_f.conventional_crewed');
  assert(source);
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1600,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=result;
    if(result.view&&result.status?.epoch)live=result;
    if(req.method==='tactical.realtime.deploy_encounter')entries.push(structuredClone(result));
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+60000;while(!(await fn())&&Date.now()<end)await page.waitForTimeout(50);assert(await fn(),label);};
  const go=()=>page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await go();await until(()=>packet?.contact_start.status==='incomplete','new automatic empty layout');
  assert.equal(await page.getByLabel('开战距离模式',{exact:true}).inputValue(),'automatic');
  assert(await button('按当前编队进入交战').isDisabled());
  await page.getByLabel('开战距离模式',{exact:true}).selectOption('manual');
  await page.getByLabel('初始交战距离（公里）',{exact:true}).fill('8');await button('应用距离').click();
  await until(()=>packet.scene.distance_m===8000,'manual test distance');
  await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
  await button('从目录加入我方').click();await until(()=>packet.scene.sides[1].ships.length===1,'player imported');
  await page.getByRole('tab',{name:'敌方',exact:true}).click();await button('从目录加入敌方').click();
  await until(()=>packet.scene.sides[0].ships.length===1,'enemy imported');
  await page.getByLabel('开战距离模式',{exact:true}).selectOption('automatic');
  await until(()=>packet.contact_start.status==='ready','automatic contact ready');
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('重新计算距离').click();await until(()=>packet.contact_start.status==='ready','post-preparation distance');
  const distance=packet.contact_start.distance_m;assert(distance>0&&distance<=50000);
  await until(()=>button('按当前编队进入交战').isEnabled(),'preparation finished refreshing');
  await page.screenshot({path:path.join(out,'automatic-preparation.png'),fullPage:true});
  await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await until(()=>entries.length===1,'first entry');
  const first=entries[0];assert.equal(first.status.fixed_step,0);
  assert.equal(first.view.navigation.disengagement.distance_m,distance);
  assert(first.view.gunnery.observation.ships.some(s=>s.contacts.some(c=>c.valid)),'first frame has real player contact in symmetric scenario');
  assert(first.view.gunnery.weapons.every(w=>w.shots===0));assert.equal(first.view.gunnery.ending,null);
  await page.locator('.tactical-ship-marker.enemy[data-contact-state="tracked"]').first().waitFor({state:'visible'});
  assert.equal(live.status.fixed_step,0,'canvas and target visible before advancing');
  await page.screenshot({path:path.join(out,'initial-contact.png'),fullPage:true});
  await button('开始交战').click();await until(()=>live.status.fixed_step>=60,'real simulation');
  await button('暂停交战').click();await until(()=>!live.status.running,'pause');
  await button('结束本场交战（测试）').click();await button('保存全部战后结果').click();
  await until(()=>live.settlement?.saved,'save first battle');
  const firstResult=structuredClone(live.settlement.result);
  await button('管理战后库存与下一场准备').click();await button('重新计算距离').waitFor();
  // Restart the actual Python process against the same isolated saved store.
  await page.goto('about:blank');await stopBackend();startBackend();await hello();packet=null;live=null;
  await go();await until(()=>packet?.contact_start.status==='ready','restart restores automatic layout');
  assert.equal(packet.scene.distance_mode,'automatic');
  for(const row of firstResult.ships){
    const restored=packet.ships.find(s=>s.instance_id===row.after.state.instance_id);assert(restored);
    assert.deepEqual(restored.state,row.after.state);
  }
  await button('按当前编队进入交战').click();await until(()=>entries.length===2,'second entry after restart');
  assert.equal(entries[1].status.fixed_step,0);assert.notEqual(entries[1].status.epoch,first.status.epoch);
  assert.equal(entries[1].view.gunnery.projectiles.length,0);
  await button('结束本场交战（测试）').click();await button('保存全部战后结果').click();await until(()=>live.settlement?.saved,'save second battle');
  await page.screenshot({path:path.join(out,'restart-settlement.png'),fullPage:true});
  checks.push('New automatic mode and incomplete-layout gate; explicit manual distance; catalog ships and real preparation.',
    'Computed distance equals deployment; initial observation at step 0 without shots; real simulation and pause.',
    'All-ship save, actual Python restart, exact restored states, automatic reentry and second save.');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'CONTACT_START_UI_PASS',scope:'Real React/Python in Edge; not native WebView',
    distance_m:distance,first_contact_count:first.view.gunnery.observation.ships.flatMap(s=>s.contacts).length,checks},null,2));
  console.log(JSON.stringify({out,distance_m:distance,checks}));
}catch(error){
  await writeFile(path.join(out,'last-state.json'),JSON.stringify({packet,live}));
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  throw error;
}finally{await browser?.close();await stopBackend();for(const wait of pending.values())clearTimeout(wait.timer);}
