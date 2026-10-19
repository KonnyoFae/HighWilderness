// P2a.1c real browser interactions: deterministic edge cases plus real sidecar.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const runtime=path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies');
const require=createRequire(path.join(runtime,'node/node_modules/package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.argv[2]),source=path.resolve(process.argv[3]);
await mkdir(out,{recursive:true});
const errors=[],checks=[],pending=new Map();let serial=0,latest;
const python=path.join(runtime,'python/python.exe');
const backend=spawn(python,['-X','utf8','-m','tools.tactical_7c4b_live',source,path.join(out,'live')],{windowsHide:true});
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{
  const value=JSON.parse(line),p=pending.get(value.request_id);if(!p)return;
  pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));
});
function request(req){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Request timeout: ${req.method}`)),60000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',
    session_id:null,expected_revision:null,...req,request_id})+'\n');
});}
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  for(const size of [{width:1366,height:768},{width:1920,height:1080}]){
    await page.setViewportSize(size);await page.goto('http://127.0.0.1:1423/target-directory-test.html');
    const panel=page.getByRole('region',{name:'目标名册'}),mode=page.getByLabel('验收场景');
    // section with aria-label is exposed as region; selectors stay within it.
    await panel.waitFor();
    const enemy=panel.locator('[data-target-key]').filter({hasText:'苍穹号'});
    assert(await enemy.isDisabled());assert(!(await panel.textContent()).includes('秒前观测'));
    await page.screenshot({path:path.join(out,`unknown-${size.width}.png`)});
    await mode.selectOption('tracked');await enemy.click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="验收命令计数"]').textContent==='1');
    await enemy.dblclick();assert.equal(await page.getByLabel('验收定位').textContent(),'1200,3400/cloud');
    const commandCount=await page.getByLabel('验收命令计数').textContent();
    await mode.selectOption('no_computer');await enemy.click();assert.equal(await page.getByLabel('验收命令计数').textContent(),commandCount);
    await mode.selectOption('lost');await enemy.dblclick();assert.equal(await page.getByLabel('验收命令计数').textContent(),commandCount);
    assert((await enemy.textContent()).includes('失联'));assert((await enemy.textContent()).includes('2.0 秒前观测'));
    await page.getByLabel('验收操作舰').selectOption('own-b');assert(await enemy.isDisabled());
    assert(!(await panel.textContent()).includes('秒前观测'));
    await page.getByLabel('验收操作舰').selectOption('own-a');await mode.selectOption('expired');assert(await enemy.isDisabled());
    await mode.selectOption('removed');await panel.getByRole('button',{name:/历史观测/}).click();
    assert((await panel.textContent()).includes('0 个活动目标'));assert((await enemy.textContent()).includes('失联'));
    await mode.selectOption('legacy');assert(!(await panel.textContent()).includes('苍穹号'));
    await mode.selectOption('dense');await panel.getByRole('button',{name:/炮弹/}).click();
    assert.equal(await panel.locator('[data-target-key]').count(),14);
    const pages=panel.getByRole('navigation',{name:'炮弹分页'});await pages.getByRole('button',{name:'下一页'}).click();
    assert((await panel.textContent()).includes('炮弹 #12'));assert.equal(await panel.locator('[data-target-key]').count(),14);
    await page.getByRole('button',{name:'航空',exact:true}).click();assert(await panel.isVisible());
    await page.screenshot({path:path.join(out,`dense-${size.width}.png`)});
    await panel.getByRole('button',{name:'有有效观测',exact:true}).click();assert.equal(await panel.locator('[data-target-key]').count(),0);
    await page.getByRole('button',{name:'新战局',exact:true}).click();assert.equal(await panel.locator('[data-target-key]').count(),2);
    const layout=await panel.evaluate(el=>({width:el.clientWidth,scrollWidth:el.scrollWidth,bottom:el.getBoundingClientRect().bottom}));
    assert(layout.scrollWidth<=layout.width+1);assert(layout.bottom<size.height);assert(layout.width<=365);
    checks.push({size,edgeCases:'passed',denseActive:1002,maxRenderedRows:14,layout});
  }
  await request({method:'system.hello',params:{client_name:'target-directory.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.realtime.aviation']}});
  const live=await browser.newPage({viewport:{width:1366,height:768}});live.setDefaultTimeout(60000);live.on('pageerror',e=>errors.push(e.message));
  await live.exposeFunction('__e3b_request',async req=>{const r=await request(req);if(r.interface==='gaotian.realtime-view/e3b-v1alpha1')latest=r;return r;});
  await live.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');
  await live.getByRole('button',{name:'按当前编队进入交战',exact:true}).click();
  await live.getByRole('button',{name:'开始交战',exact:true}).waitFor();
  const panel=live.getByRole('region',{name:'目标名册'});await panel.waitFor();
  assert(latest.view.gunnery.target_directory.entries.length>0);
  await live.getByRole('button',{name:'开始交战',exact:true}).click();
  await live.waitForFunction(()=>document.querySelector('[data-target-state="tracked"]')!==null);
  for(let attempt=0;attempt<100&&latest.status.fixed_step<30;attempt++)await new Promise(resolve=>setTimeout(resolve,200));
  assert(latest.status.fixed_step>=30,'Real battle must advance before page checks');
  await live.getByRole('button',{name:'暂停交战',exact:true}).click();
  await live.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='开始交战'&&!b.disabled));
  const pausedStep=latest.status.fixed_step;assert(pausedStep>0);
  for(const size of [{width:1366,height:768},{width:1920,height:1080}]){
    await live.setViewportSize(size);await live.screenshot({path:path.join(out,`live-${size.width}.png`)});
    await live.getByRole('button',{name:'航空',exact:true}).click();assert(await panel.isVisible());
    await live.getByRole('button',{name:'导弹',exact:true}).click();assert(await panel.isVisible());
    const fleets=live.locator('[data-fleet-ship]');if(await fleets.count()>1){await fleets.nth(1).click();assert(await panel.isVisible());}
  }
  checks.push({realSidecar:'passed',pausedStep,entries:latest.view.gunnery.target_directory.entries.length,
    revision:latest.view.gunnery.target_directory.revision,scope:'Real mixed battle and page; deterministic edge cases above use a separate component fixture.'});
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'browser.json'),JSON.stringify({checks,errors},null,2)+'\n');
  console.log(JSON.stringify({checks,errors}));
}finally{
  for(const p of pending.values())clearTimeout(p.timer);
  await browser.close();backend.stdin.end();
  await new Promise(resolve=>{const timer=setTimeout(()=>{backend.kill();resolve();},5000);backend.once('exit',()=>{clearTimeout(timer);resolve();});});
}
