// Real preparation, combat projection and persistence in an isolated sidecar.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn,execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_ARMOR_OUT ?? `artifacts/hull-armor-a3-${Date.now()}`);
await mkdir(out,{recursive:true});
const file=path.join(out,'outfit.json');
const document=execFileSync('python',['-X','utf8','-c',
  process.env.HW_ARMOR_JOINT ? 'import json;from tools.verify_hull_armor_joint import design,ResourceIndex,ROOT;print(json.dumps(design(ResourceIndex(ROOT),25,60).archive()["document"],ensure_ascii=False))' :
  'import json;from tools.test_sloped_armor_damage import design,ResourceIndex,ROOT;print(json.dumps(design(ResourceIndex(ROOT),60).archive()["document"],ensure_ascii=False))'],{windowsHide:true});
await writeFile(file,document);
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store')],{windowsHide:true});
let serial=0,browser,page,packet,scene;const pending=new Map(),errors=[],checks=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{
  const v=JSON.parse(line),p=pending.get(v.request_id);
  if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}
});
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
async function until(fn,label){const end=Date.now()+20000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);}
try{
  await request('system.hello',{client_name:'armor.a3.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.scene_read']});
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:file,mode:'open'});
    const v=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=v;
    if(v.interface==='gaotian.realtime-view/e3b-v1alpha1')scene={...v,view:{...v.view,static:v.view.static??scene?.view.static}};
    return v;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:1421/e3b-test.html?entry=tactical');
  await button('导入栖装文件').click();await until(()=>packet?.geometry.ships.length===1,'sloped player imported');
  await page.getByRole('tab',{name:'敌方',exact:true}).click();await button('导入栖装文件').click();
  await until(()=>packet?.geometry.ships.length===2,'sloped enemy imported');
  for(const side of ['敌方','我方']){
    const canvas=page.getByRole('region',{name:`${side}编队画布`,exact:true});
    await page.getByLabel(`${side}显示甲板`,{exact:true}).selectOption('0');
    assert(await canvas.locator('title').filter({hasText:'外飘装甲 60°'}).count()>0);
  }
  assert(packet.geometry.ships.every(s=>s.decks[0].regions[0].armor_faces.length===7));
  await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  checks.push('Both real imported designs pass preparation; original hull and seven flared faces are visible on both canvases.');
  await button('配置双方舰内物资').click();
  await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();await button('开始交战').waitFor();
  await until(()=>scene?.view?.ships.length===2,'two sloped ships enter');
  const geometry=structuredClone(scene.view.static);
  assert(geometry.ships.every(s=>s.decks[0].regions[0].armor_outline_m.length===7));
  // First double click focuses; the second selects detail zoom (existing policy).
  await page.waitForFunction(()=>document.querySelector('.tactical-canvas')?.dataset.camera);
  const row=page.locator('[data-fleet-ship]').first();
  await row.dblclick();await row.dblclick();
  await button('开始交战').click();await until(()=>scene.status.fixed_step>30||scene.view.fixed_step>30,'real fixed steps run');
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused');
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});
  assert.deepEqual(scene.view.static,geometry);
  checks.push('Battle accepts both designs; cached full armor outlines survive real fixed steps unchanged.');
  await button('结束本场交战').click();await button('保存全部战后结果').click();await until(()=>scene.settlement.saved,'settlement saved');
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  checks.push('Actual all-side settlement saves and returns to preparation. Damage/depleted-edge restoration is separately tested by the Python suite.');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'report.json'),JSON.stringify({status:'PASS',checks},null,2));
  console.log(JSON.stringify({out,checks}));
}catch(error){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}console.error(error);process.exitCode=1;}
finally{await browser?.close();backend.kill();for(const p of pending.values())clearTimeout(p.timer);}
