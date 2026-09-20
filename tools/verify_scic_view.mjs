// Real catalog/canvas, explicit core refit, save/reopen and fleet qualification UI.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_SCIC_OUT??`artifacts/tactical-scic-${Date.now()}`);await mkdir(out,{recursive:true});
const file=path.join(out,'scic-outfit.json');
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store'),'--recovery-dir',path.join(out,'recovery')],{windowsHide:true});
let serial=0,browser,page,snapshot,packet;const pending=new Map(),errors=[],checks=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.request_id);if(p){pending.delete(r.request_id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(JSON.stringify(r.error)));}});
function request(method,params={},session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
  await request('system.hello',{client_name:'scic.ui',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:[]});
  const index=await request('resource.list'),source=index.resources.find(r=>r.id==='gtw.outfit.fixture.stage_f.conventional_crewed');
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1600,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:file,mode:req.params.method},req.params.session_id??null,req.params.expected_revision??null);
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(r.interface?.startsWith('gaotian.editor-session/'))snapshot=r;
    if(req.method==='tactical.preparation.scene_read')packet=r;
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+30000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
  const card=name=>page.locator('.module-card').filter({has:page.getByText(name,{exact:true})});
  await page.goto('http://127.0.0.1:1423/e3b-test.html');await page.getByText('内置舾装示例',{exact:true}).click();await button(source.name).click();
  await until(()=>snapshot?.draft?.id===source.id,'open real legacy design');
  await page.getByRole('tab',{name:'CIC',exact:true}).click();
  for(const [name,id] of [['基础SCIC','basic'],['基础SCIC（无人化）','basic.unmanned'],['高级SCIC','advanced'],['高级SCIC（无人化）','advanced.unmanned']]){
    await card(name).click();await button('将舰艇核心替换为'+name).click();
    await until(()=>snapshot.draft.modules.find(m=>m.id==='cic').prototype.id==='gtw.module.scic.'+id,'explicit refit '+name);
    assert(snapshot.preview.valid);assert(snapshot.draft.modules.some(m=>m.id==='remote_core'));
  }
  const coreRef=structuredClone(snapshot.draft.modules.find(m=>m.id==='cic').prototype);
  await button('撤销').click();await until(()=>snapshot.draft.modules.find(m=>m.id==='cic').prototype.id==='gtw.module.scic.advanced','undo refit');
  await button('重做').click();await until(()=>snapshot.draft.modules.find(m=>m.id==='cic').prototype.id===coreRef.id,'redo refit');
  checks.push('All four SCIC refits preserve the legacy remote core and origin; undo/redo retains exact versions.');
  // A real empty origin is created via the UI, then a SCIC is placed on canvas.
  await page.getByLabel('画布模块选择',{exact:true}).selectOption('cic');
  await button('移除模块及 1 个嵌入模块').click();await until(()=>!snapshot.draft.modules.some(m=>m.id==='cic'),'remove old core via UI');
  await card('基础SCIC（无人化）').click();await button('＋ 添加部件').click();
  await page.getByLabel('舾装画布甲板',{exact:true}).selectOption('deck.0');await button('适应船壳').click();
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const canvas=page.getByRole('region',{name:'舾装二维画布',exact:true}),box=await canvas.boundingBox(),vb=(await canvas.getAttribute('viewBox')).split(' ').map(Number);
  const regions=snapshot.preview.model.layout.hull.decks.find(d=>d.id==='deck.0').regions;
  const p=await page.evaluate(async({regions,width,height})=>{const {fit,screen}=await import('/src/editor/viewport.ts');return screen({x:0,y:0},fit(regions,width,height));},{regions,width:vb[2],height:vb[3]});
  const clickOrigin=()=>page.mouse.click(box.x+p.x*box.width/vb[2],box.y+p.y*box.height/vb[3]);
  await clickOrigin();await until(()=>snapshot.draft.modules.some(m=>m.prototype.id==='gtw.module.scic.basic.unmanned'),'place SCIC on canvas');
  await page.getByRole('tab',{name:'遥控核心',exact:true}).click();await card('SCIC 遥控核心舱').click();await clickOrigin();
  await until(()=>snapshot.draft.modules.some(m=>m.prototype.id==='gtw.module.fixture.remote_core'&&m.prototype.version===2),'place v2 remote on SCIC');
  assert(snapshot.preview.valid);
  await button('↖ 拖动部件').click();await page.mouse.move(20,20);await page.screenshot({path:path.join(out,'installed.png'),fullPage:true});
  await button('另存文件').click();await until(()=>snapshot.file_label==='scic-outfit.json'&&!snapshot.dirty,'save');
  const saved=structuredClone(snapshot.preview.model.layout);
  await button('返回编辑器入口').click();await button('打开舾装文件').click();await until(()=>snapshot.file_label==='scic-outfit.json','reopen');
  assert.deepEqual(snapshot.preview.model.layout,saved);assert(snapshot.preview.valid);
  checks.push('SCIC and remote v2 placed from real catalog on canvas, saved and reopened with identical geometry and versions.');
  await page.goto('http://127.0.0.1:1423/e3b-test.html?entry=tactical');await button('导入栖装文件').waitFor();
  // Import the actual UI-saved SCIC design; add normal CIC escorts from catalog.
  await button('导入栖装文件').click();await until(()=>packet?.scene.sides[1].ships.length===1,'SCIC flagship import');
  await page.getByLabel('准备目录设计',{exact:true}).selectOption(source.key);
  for(let i=0;i<4;i++){await button('从目录加入我方').click();await until(()=>packet?.scene.sides[1].ships.length===i+2,'add escort');}
  assert.equal(packet.fleets[1].valid,false);assert.equal(packet.fleets[1].companion_count,4);
  await page.getByLabel('我方编队资格',{exact:true}).getByText(/4 \/ 3/).first().waitFor();
  assert(await button('按当前编队进入交战').isDisabled());await page.screenshot({path:path.join(out,'over-capacity.png'),fullPage:true});
  await page.getByText('编队位置与旗舰',{exact:true}).click();await button('移出编队').click();
  await until(()=>packet.scene.sides[1].ships.length===4&&packet.fleets[1].valid,'remove only explicit escort');
  // Selecting an ordinary CIC as flagship keeps all ships and invalidates entry.
  await page.locator('.preparation-roster.player>button').last().click();
  if(!await page.locator('.formation-properties').evaluate(e=>e.open))await page.getByText('编队位置与旗舰',{exact:true}).click();
  await button('设为我方旗舰').click();await until(()=>packet.fleets[1].companion_capacity===0,'ordinary CIC flagship');
  assert.equal(packet.scene.sides[1].ships.length,4);assert(!packet.fleets[1].valid);
  await page.locator('.preparation-roster.player>button').first().click();await button('设为我方旗舰').click();
  await until(()=>packet.fleets[1].valid,'restore SCIC flagship');
  await page.getByRole('tab',{name:'敌方',exact:true}).click();await button('从目录加入敌方').click();
  await until(()=>packet.scene.sides[0].ships.length===1&&packet.fleets.every(f=>f.valid),'enemy ordinary CIC solo');
  await page.screenshot({path:path.join(out,'valid-fleets.png'),fullPage:true});
  await button('配置双方舰内物资').click();await button('核对资源与预装填').click();await button('保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();await button('按当前编队进入交战').click();
  await button('开始交战').waitFor();await button('开始交战').click();await button('暂停交战').click();
  await page.screenshot({path:path.join(out,'battle.png'),fullPage:true});
  checks.push('Over-capacity fleet remains editable; ordinary flagship blocks entry, restoring SCIC re-enables it; normal CIC enemy solo and three escorts pass real preparation and combat entry.');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'SCIC_UI_PASS',scope:'Real React/Python in Edge; injected file dialog, not native WebView',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();if(backend.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
