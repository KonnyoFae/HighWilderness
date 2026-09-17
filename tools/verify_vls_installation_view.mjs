// Exercise the real editor and Python compiler; all saved files are isolated.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_VLS_INSTALL_OUT??`artifacts/vls-installation-${Date.now()}`);
await mkdir(out,{recursive:true});
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser',
  '--recovery-dir',path.join(out,'recovery'),'--settlement-dir',path.join(out,'store')],{windowsHide:true});
const pending=new Map(),errors=[],commands=[],checks=[];let serial=0,snapshot,browser,page;
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
function request(method,params={},session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);
  pending.set(request_id,{resolve,reject,timer});backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
  await request('system.hello',{client_name:'vls.installation.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:[]});
  const index=await request('resource.list');
  const source=index.resources.find(r=>r.id==='gtw.outfit.tactical.missile.vls');
  const prototype=index.module_options.find(o=>o.prototype.id==='gtw.module.launcher.5c.vls').prototype;
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1600,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:path.join(out,'vls-outfit.json'),mode:req.params.method},req.params.session_id,req.params.expected_revision);
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(r.interface?.startsWith('gaotian.editor-session/'))snapshot=r;
    if(req.method==='editor.command')commands.push(req.params);
    return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+15000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
  const settle=()=>page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  await page.goto('http://127.0.0.1:1423/e3b-test.html');
  await page.getByText('内置舾装示例',{exact:true}).click();await button(source.name).click();
  await page.getByLabel('画布模块选择',{exact:true}).selectOption('weapon_upper_port');
  await button('↖ 拖动部件').click();await button('移除所选模块').click();
  await until(()=>snapshot&&!snapshot.draft.modules.some(m=>m.id==='weapon_upper_port'),'remove existing VLS through UI');
  await button('＋ 添加部件').click();await page.getByRole('tab',{name:'武器',exact:true}).click();
  await page.locator('.module-card').filter({has:page.getByText(prototype.name,{exact:true})}).click();
  const canvas=page.getByRole('region',{name:'舾装二维画布',exact:true});
  async function at(x,y){
    await button('适应船壳').click();await settle();
    const box=await canvas.boundingBox(),vb=await canvas.getAttribute('viewBox');
    const [, ,width,height]=vb.split(' ').map(Number);
    const deckId=await page.getByLabel('舾装画布甲板',{exact:true}).inputValue();
    const regions=snapshot.preview.model.layout.hull.decks.find(d=>d.id===deckId).regions;
    const p=await page.evaluate(async({regions,width,height,x,y})=>{
      const {fit,screen}=await import('/src/editor/viewport.ts');return screen({x,y},fit(regions,width,height));
    },{regions,width,height,x,y});
    return {x:box.x+p.x*box.width/width,y:box.y+p.y*box.height/height};
  }
  await page.getByLabel('舾装画布甲板',{exact:true}).selectOption('deck.1');
  let p=await at(-5,-10);await page.mouse.move(p.x,p.y);await page.mouse.click(p.x,p.y);
  await until(()=>snapshot.draft.modules.some(m=>m.prototype.id===prototype.id),'place VLS on exposed upper deck');
  const vls=()=>snapshot.draft.modules.find(m=>m.prototype.id===prototype.id);
  const geometry=()=>snapshot.preview.model.layout.modules.find(m=>m.id===vls().id);
  assert.equal(vls().placement.deck_id,'deck.0');assert(snapshot.preview.valid);
  assert.deepEqual(geometry().internal_cells.map(c=>c[0]),[0,1]);assert.equal(geometry().top_cells[0][0],1);
  assert.equal(snapshot.preview.model.weapon_control.arcs.find(a=>a.instance_id===vls().id).status,'vertical_launch');
  assert.equal(await canvas.locator('[aria-label="武器水平射界"]').count(),0);
  assert.equal(await page.getByLabel('显示水平射界',{exact:true}).count(),0);
  await page.getByRole('complementary',{name:'部件与图层属性'}).getByText('垂直发射，不受水平射界限制',{exact:true}).waitFor();
  checks.push('VLS selection shows vertical launch, without a turret firing-arc toggle, red forbidden sector or false 360-degree blocked label.');
  checks.push('The catalog VLS is placed by clicking deck 1; the saved base is deck 0, with two internal cells and a deck-1 exposed top.');
  await button('↖ 拖动部件').click();
  await page.mouse.move(20,20);
  await page.getByRole('complementary',{name:'部件与图层属性'}).getByText('垂直发射，不受水平射界限制',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,'installed.png'),fullPage:true});
  for(const deck of ['deck.1','deck.0']){
    await page.getByLabel('舾装画布甲板',{exact:true}).selectOption(deck);
    const start=await at(-5,-10),end=await at(0,-10),before=snapshot.revision;
    await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:10});await page.mouse.up();
    await until(()=>snapshot.revision>before,'drag VLS');
    assert.equal(vls().placement.deck_id,'deck.0');assert.deepEqual(vls().placement.anchor_half_cell,[0,-4]);
    assert.equal(commands.at(-1).command,'outfit.move_grid');
    await button('撤销').click();await until(()=>vls().placement.anchor_half_cell[0]===-2,'undo VLS drag');
    assert(snapshot.preview.valid);
  }
  checks.push('Dragging from either occupied deck preserves the installation base; undo restores the legal original layout.');
  await button('＋ 添加部件').click();
  const before=commands.length;p=await at(-5,-10);await page.mouse.move(p.x,p.y);await page.mouse.click(p.x,p.y);
  await page.getByText('下方甲板不足，无法容纳该部件；请在有足够下层空间的露天甲板放置。',{exact:true}).first().waitFor();
  assert.equal(commands.length,before);assert(snapshot.preview.valid);
  checks.push('Attempting top-based placement on the lowest deck reports missing lower space without creating an invalid module.');
  await button('另存文件').click();await until(()=>snapshot.file_label==='vls-outfit.json'&&!snapshot.dirty,'save through real editor');
  const saved=snapshot.preview.model.layout;
  await button('返回编辑器入口').click();await button('打开舾装文件').click();
  await until(()=>snapshot.file_label==='vls-outfit.json'&&snapshot.preview.valid,'reopen saved VLS design');
  assert.deepEqual(snapshot.preview.model.layout,saved);assert.equal(vls().placement.deck_id,'deck.0');
  checks.push('Save and reopen preserve exact VLS geometry and pass the authoritative compiler.');
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'VLS_INSTALLATION_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();if(backend.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
