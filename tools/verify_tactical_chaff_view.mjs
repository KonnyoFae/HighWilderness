// P1a real React/Python workflow with isolated finite stocks; no native IPC claim.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES??path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'),'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.argv[2]);
await mkdir(out,{recursive:true});
const setup=spawnSync('python',['-X','utf8','-m','tools.tactical_chaff_fixture',out],{encoding:'utf8',windowsHide:true});
assert.equal(setup.status,0,setup.stderr);
const files=['backend/high_wilderness_sidecar/tactical_ew.py','apps/desktop/src/tactical/CountermeasurePanel.tsx',
  'apps/desktop/src/tactical/CountermeasurePanel.test.tsx','tools/tactical_chaff_fixture.py','tools/test_tactical_chaff.py','tools/test_tactical_ew.py','tools/verify_tactical_chaff_view.mjs'];
const hashes=await Promise.all(files.map(async f=>[f,createHash('sha256').update(await readFile(f)).digest('hex')]));
await writeFile(path.join(out,'source.json'),JSON.stringify({head:spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8',windowsHide:true}).stdout.trim(),
  files:Object.fromEntries(hashes)},null,2)+'\n');
const pending=new Map(),errors=[],checks=[],commands=[],snapshots={};
let backend,browser,page,serial=0,live,packet,dropDeploy=true;
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store')],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{
    const v=JSON.parse(line),p=pending.get(v.request_id);
    if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}
  });
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},30000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function stop(){if(backend?.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);backend.kill();});}
async function hello(){await request('system.hello',{client_name:'chaff.p1a.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.countermeasure']});}
const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
const chaff='countermeasure.chaff.small',own=()=>live?.direct_ship_id;
const ew=()=>live?.view.gunnery?.electronic_warfare;
const device=mid=>ew()?.devices.find(d=>d.ship_id===own()&&d.module_id===mid);
const observed=()=>live?.view.gunnery?.observation.ships.find(s=>s.ship_id===own());
const gun=()=>live?.view.gunnery?.weapons.find(w=>w.ship_id===own());
const capture=name=>{snapshots[name]=structuredClone(live);};
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1050}});page.setDefaultTimeout(30000);
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_wire_request',async req=>{
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')live=r;
    if(req.method==='tactical.realtime.countermeasure'){
      commands.push({input:req.params.input,response:r});
      if(dropDeploy){dropDeploy=false;throw new Error('测试：箔条已受理，回执丢失');}
    }
    return JSON.stringify(r);
  });
  await page.addInitScript(()=>{window.__e3b_request=async req=>JSON.parse(await window.__e3b_wire_request(req));});
  const button=name=>page.getByRole('button',{name,exact:true});
  const savePreparation=async()=>{
    await button('配置双方舰内物资').click();
    await button('核对资源与预装填').click();await button('保存准备').click();
    await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  };
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===2,'two prepared pure gun ships');
  await savePreparation();
  await button('按当前编队进入交战').click();await button('开始交战').click();
  const inspector=page.getByRole('complementary',{name:'火控',exact:true});
  await inspector.locator('summary').filter({hasText:/^电子对抗$/}).click();
  await until(()=>gun()?.quality==='normal'&&gun().shots>0&&device(chaff)?.status==='ready','actual gun fire and radar lock before deployment');
  assert.equal(device(chaff).detected,false);
  assert(await inspector.getByRole('button',{name:'投放小型箔条',exact:true}).isEnabled());
  assert(await inspector.getByRole('button',{name:'投放小型热能烟雾',exact:true}).isDisabled());
  assert(await inspector.getByRole('button',{name:'投放主动诱饵',exact:true}).isDisabled());
  assert(observed().devices.find(d=>d.module_id==='sensor_upper_starboard').used>0);
  capture('before');await page.screenshot({path:path.join(out,'before.png'),fullPage:true});
  await inspector.getByRole('button',{name:'投放小型箔条',exact:true}).click();
  await until(()=>device(chaff)?.shots===1&&ew().effects.some(e=>e.kind==='chaff'),'manual chaff with no missiles and lost reply');
  assert.equal(device(chaff).ready,0);assert.equal(commands.length,1);
  assert.equal(observed().devices.find(d=>d.module_id==='sensor_upper_starboard').used,0);
  assert.equal(gun().quality,'degraded');assert.equal(gun().lock_sources.length,0);
  assert(!observed().contacts.some(c=>c.kind==='ship'&&c.valid));
  assert(!live.view.gunnery.projectiles.some(p=>p.kind==='missile'));
  capture('blocked');await page.screenshot({path:path.join(out,'chaff.png'),fullPage:true});
  checks.push('No missiles: button enabled, one command/one discharge despite lost reply, radar and gun lock lost while chaff is active.');
  const irName=observed().devices.find(d=>d.module_id==='infrared.backup').name;
  await inspector.locator('summary').filter({hasText:/^感知设备$/}).click();
  await inspector.locator('section.observation-card').filter({has:page.getByText(irName,{exact:true})}).getByRole('button',{name:'开启',exact:true}).click();
  await until(()=>observed()?.contacts.some(c=>c.kind==='ship'&&c.valid),'IR contact survives chaff');
  assert(observed().contacts.filter(c=>c.kind==='ship'&&c.valid).every(c=>!c.radar_source_available));
  assert.equal(observed().devices.find(d=>d.module_id==='sensor_upper_starboard').used,0);
  capture('infrared');
  await button('暂停交战').click();await until(()=>!live.status.running,'paused');
  const pausedStep=live.status.fixed_step;
  assert(await inspector.getByRole('button',{name:'投放大型箔条',exact:true}).isDisabled());
  await page.waitForTimeout(300);assert.equal(live.status.fixed_step,pausedStep);assert.equal(device(chaff).shots,1);
  capture('paused');await button('开始交战').click();await until(()=>live.status.running,'resumed');
  assert.equal(device(chaff).shots,1);
  checks.push('Actual IR toggle restores legal contact without radar illumination; pause disables deployment and freezes steps; resume does not repeat the command.');
  await button('结束本场交战（测试）').click();await button('保存全部战后结果').click();await button('结算已保存').waitFor();
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  const saved=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.chaff.player').state;
  assert.equal(saved.weapons.find(w=>w.module_id===chaff).ready_rounds,0);
  // Finish preparation's initial refresh before simulating a closed app restart.
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='配置双方舰内物资'&&!b.disabled));
  await page.goto('about:blank');await until(()=>pending.size===0,'drained requests before restart');
  await stop();start();await hello();
  const restored=(await request('tactical.preparation.scene_read',{})).ships.find(s=>s.instance_id==='instance.chaff.player').state;
  assert.deepEqual(restored,saved);
  packet=null;await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').waitFor();await until(()=>packet?.ships.length===2,'restored preparation page');
  await savePreparation();
  await button('按当前编队进入交战').click();
  await inspector.locator('summary').filter({hasText:/^电子对抗$/}).click();
  await until(()=>device(chaff)?.ready===0,'next battle retains spent chaff');
  assert.equal(device(chaff).shots,0);assert.equal(ew().effects.length,0);
  capture('next_battle');await page.screenshot({path:path.join(out,'next-battle.png'),fullPage:true});
  checks.push('Explicit test end and all-ship save retain spent chaff; restart preserves exact inventory; next battle has no old cloud and no free chaff.');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'P1A_CHAFF_BROWSER_PASS',scope:'JSON text test bridge, real React and Python; not native IPC or full battle acceptance',checks,commands,snapshots,saved},null,2)+'\n');
  console.log(JSON.stringify({out,checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}
  await writeFile(path.join(out,'diagnostic.json'),JSON.stringify({error:String(error),errors,checks,commands,snapshots,live},null,2)+'\n');
  throw error;
}finally{await browser?.close();await stop();}
