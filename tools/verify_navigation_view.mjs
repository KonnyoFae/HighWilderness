// Real catalog/canvas, explicit core refit, save/reopen and fleet qualification UI.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_NAVIGATION_OUT??`artifacts/tactical-navigation-${Date.now()}`);await mkdir(out,{recursive:true});
const file=path.join(out,'scic-outfit.json');
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store'),'--recovery-dir',path.join(out,'recovery')],{windowsHide:true});
let serial=0,browser,page,snapshot,packet,live;const pending=new Map(),errors=[],checks=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.request_id);if(p){pending.delete(r.request_id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(JSON.stringify(r.error)));}});
function request(method,params={},session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
  await writeFile(file,await readFile(path.resolve('artifacts/tactical-scic-1789892279722/scic-outfit.json')));
  await request('system.hello',{client_name:'navigation.ui',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:[]});
  const index=await request('resource.list'),source=index.resources.find(r=>r.id==='gtw.outfit.fixture.stage_f.conventional_crewed');
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1600,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:file,mode:req.params.method},req.params.session_id??null,req.params.expected_revision??null);
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=r;
    if(r.view&&r.status?.epoch)live=r;
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('导入栖装文件').click();
  await until(()=>packet?.scene.sides[1].ships.length===1,'SCIC import');
  await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
  await button('从目录加入我方').click();await until(()=>packet.scene.sides[1].ships.length===2,'escort');
  await page.getByRole('tab',{name:'敌方',exact:true}).click();await button('从目录加入敌方').click();
  await until(()=>packet.scene.sides[0].ships.length===1,'enemy');
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();
  await button('开始交战').click();await until(()=>live?.status.fixed_step>3,'simulation starts');
  const escort=live.view.navigation.ships[0].ship_id;
  await page.locator(`[data-fleet-ship="${escort}"]`).click();await button('指定航点').waitFor();
  const field=page.locator('.tactical-canvas').first(),box=await field.boundingBox();assert(box);
  const before=await field.boundingBox();await button('指定航点').click();
  await page.mouse.click(box.x+box.width*.4,box.y+box.height*.4);
  await until(()=>live.view.navigation.ships.find(s=>s.ship_id===escort)?.kind==='move','point order');
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='指定航点'&&!b.disabled));
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const seq=live.view.navigation.command_sequence;
  await page.keyboard.down('Shift');await page.mouse.click(box.x+box.width*.5,box.y+box.height*.38);await page.keyboard.up('Shift');
  await until(()=>live.view.navigation.command_sequence>seq,'append order');
  assert.equal(live.view.navigation.ships.find(s=>s.ship_id===escort).points.length,2);
  await page.getByLabel('所选舰艇航线',{exact:true}).waitFor();
  assert.deepEqual(await field.boundingBox(),before);
  await page.keyboard.press('Escape');await page.screenshot({path:path.join(out,'waypoints.png'),fullPage:true});
  await button('保持位置').click();await until(()=>live.view.navigation.ships[0].kind==='hold','hold');
  await button('归队').click();await until(()=>live.view.navigation.ships[0].kind==='formation','return');
  await page.locator('.fire-contact').first().click();
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='攻击火控所选敌舰'&&!b.disabled));
  await button('攻击火控所选敌舰').click();await until(()=>live.view.navigation.ships[0].kind==='attack','attack observed target');
  await button('归队').click();await until(()=>live.view.navigation.ships[0].kind==='formation','return from attack');
  await button('整队撤离机动').click();await until(()=>live.view.navigation.withdrawals.length===1,'retreat');
  const step=live.status.fixed_step;await until(()=>live.status.fixed_step>step+6,'retreat advances');
  assert(live.view.navigation.withdrawals[0].speed_mps>0);
  await button('暂停交战').click();await until(()=>!live.status.running,'pause acknowledged');const paused=live.status.fixed_step;
  await page.waitForTimeout(200);assert.equal(live.status.fixed_step,paused);
  await button('开始交战').click();await until(()=>live.status.fixed_step>paused+6,'resume');
  assert.equal(live.view.navigation.withdrawals.length,1,'resume neutral must not cancel retreat');
  await button('取消整队撤离机动').click();await until(()=>!live.view.navigation.withdrawals.length,'cancel retreat');
  await button('暂停交战').click();await page.screenshot({path:path.join(out,'fleet-commands.png'),fullPage:true});
  checks.push('Real saved SCIC flagship and escort: canvas waypoints, Shift append, visible route, hold, return, common-speed retreat, pause/resume and cancel; unchanged canvas bounds.');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'NAVIGATION_UI_PASS',scope:'Real React/Python in Edge; injected file dialog, not native WebView',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();if(backend.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
