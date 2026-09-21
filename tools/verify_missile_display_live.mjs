// Production React + real automatic interceptor/layer pursuit. Isolated stores;
// fixture seeds the incoming missile, subsequent combat uses normal authority.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_MISSILE_DISPLAY_OUT??`artifacts/projectile-display-d3-live-${Date.now()}`);
await mkdir(out,{recursive:true});const store=path.join(out,`store-${Date.now()}`);
const args=['-X','utf8','-m','tools.missile_coordination_browser_fixture','--mode','defense','--settlement-dir',store];
execFileSync('python',[...args,'--setup'],{windowsHide:true});
const backend=spawn('python',args,{windowsHide:true});
let browser,page,serial=0,live;const pending=new Map(),errors=[],definitions=new Map(),missilePackets=[],ends=[];
backend.stderr.on('data',data=>errors.push(String(data)));
createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
function request(req){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${req.method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,
    session_id:null,expected_revision:null,...req})+'\n');
});}
try{
  await request({method:'system.hello',params:{client_name:'display.d3.live',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.read']}});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1500,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  await page.exposeFunction('__e3b_request',async req=>{
    const result=await request(req);
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1'){
      live=result;const packet=result.view.projectile_stream;
      if(packet){
        for(const p of packet.starts)definitions.set(p.id,p);
        missilePackets.push(...(packet.missiles??[]));ends.push(...packet.ends);
      }
    }
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const deadline=Date.now()+40000;while(!fn()&&Date.now()<deadline)await page.waitForTimeout(50);assert(fn(),label);};
  await page.goto('http://127.0.0.1:1431/e3b-test.html?entry=tactical');
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();
  await button('开始交战').click();
  await until(()=>[...definitions.values()].some(p=>p.missile_identity?.interceptor),'real automatic interceptor emerges');
  await until(()=>missilePackets.some(([id,,states])=>definitions.get(id)?.missile_identity?.interceptor&&states.some(s=>s.maneuver_state==='diving')),'real interceptor descends');
  await until(()=>live.view.time_s>=2,'descent visible beyond launch');
  await button('暂停交战').click();await until(()=>!live.status.running,'pause');
  await page.getByText('视图与观察层',{exact:true}).click();await page.getByRole('button',{name:'观察上层',exact:true}).click();
  for(let i=0;i<9;i++)await page.getByRole('button',{name:'战术缩小',exact:true}).click();
  await page.getByText('视图与观察层',{exact:true}).click();await page.waitForTimeout(150);
  await page.screenshot({path:path.join(out,'interceptor-descent.png'),fullPage:true});
  await button('开始交战').click();
  await until(()=>live.view.gunnery.point_defense.recent.some(e=>e.projectile_id===10000&&e.intercepted),'real interception');
  await button('暂停交战').click();await until(()=>!live.status.running,'pause terminal');
  await page.getByText('视图与观察层',{exact:true}).click();await page.getByRole('button',{name:'观察云层',exact:true}).click();
  for(let i=0;i<5;i++)await page.getByRole('button',{name:'战术缩小',exact:true}).click();
  await page.getByText('视图与观察层',{exact:true}).click();await page.waitForTimeout(150);
  assert(ends.some(p=>p.id===10000));
  const ids=new Set([...definitions.values()].filter(p=>p.missile_identity?.interceptor).map(p=>p.id));
  assert(ends.some(p=>ids.has(p.id)));
  const states=missilePackets.filter(([id])=>ids.has(id)).flatMap(([, ,states])=>states);
  assert(states.some(s=>s.height_layer==='cloud'));
  assert.deepEqual(errors,[]);assert(!(await page.locator('body').innerText()).includes('弹体显示数据缺失'));
  await page.screenshot({path:path.join(out,'interception-complete.png'),fullPage:true});
  await button('结束本场交战').click();await button('保存全部战后结果').click();await until(()=>live.settlement?.saved,'settlement saved');
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',interceptorIds:[...ids],packets:missilePackets.length,
    phases:[...new Set(states.map(s=>s.phase))],layers:[...new Set(states.map(s=>s.height_layer))],ends,
    scope:'A seeded incoming missile and real automatic interception, descent, actual contact, pause/resume and saved settlement through production UI. This is not a fleet load test.'},null,2));
  console.log(JSON.stringify({status:'PASS',packets:missilePackets.length,interceptors:ids.size}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw error;}
finally{await browser?.close();backend.kill();for(const p of pending.values())clearTimeout(p.timer);}
