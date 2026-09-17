// Place every new countermeasure from the real editor catalog; save and reopen.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_EW_INSTALL_OUT??`artifacts/ew-installation-${Date.now()}`);await mkdir(out,{recursive:true});
const backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'store'),'--recovery-dir',path.join(out,'recovery')],{windowsHide:true});
let serial=0,browser,page,snapshot;const pending=new Map(),errors=[],checks=[];
backend.stderr.on('data',v=>errors.push(String(v)));
createInterface({input:backend.stdout}).on('line',line=>{const r=JSON.parse(line),p=pending.get(r.request_id);if(p){pending.delete(r.request_id);clearTimeout(p.timer);r.ok?p.resolve(r.result):p.reject(new Error(JSON.stringify(r.error)));}});
function request(method,params={},session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>reject(new Error(`Timeout ${method}`)),30000);pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,method,params,session_id,expected_revision})+'\n');
});}
try{
  await request('system.hello',{client_name:'ew.installation',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:[]});
  const index=await request('resource.list'),source=index.resources.find(r=>r.id==='gtw.outfit.5d.radar');
  browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1600,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:path.join(out,'countermeasures.json'),mode:req.params.method},req.params.session_id,req.params.expected_revision);
    const r=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);if(r.interface?.startsWith('gaotian.editor-session/'))snapshot=r;return r;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const until=async(fn,label)=>{const end=Date.now()+15000;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
  await page.goto('http://127.0.0.1:1423/e3b-test.html');await page.getByText('内置舾装示例',{exact:true}).click();await button(source.name).click();
  await until(()=>snapshot?.draft?.id==='gtw.outfit.5d.radar','open example');
  await button('＋ 添加部件').click();await page.getByRole('tab',{name:'武器',exact:true}).click();await page.getByLabel('舾装画布甲板',{exact:true}).selectOption('deck.0');
  const canvas=page.getByRole('region',{name:'舾装二维画布',exact:true});
  for(const [i,name] of ['小型箔条发射器','大型箔条发射器','小型热能烟雾发射器','大型热能烟雾发射器','主动诱饵发射器'].entries()){
    await page.locator('.module-card').filter({has:page.getByText(name,{exact:true})}).click();
    await button('适应船壳').click();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    const box=await canvas.boundingBox(),vb=(await canvas.getAttribute('viewBox')).split(' ').map(Number);
    const regions=snapshot.preview.model.layout.hull.decks.find(d=>d.id==='deck.0').regions;
    const p=await page.evaluate(async({regions,width,height,y})=>{const {fit,screen}=await import('/src/editor/viewport.ts');return screen({x:0,y},fit(regions,width,height));},{regions,width:vb[2],height:vb[3],y:i*5});
    await page.mouse.click(box.x+p.x*box.width/vb[2],box.y+p.y*box.height/vb[3]);
    await until(()=>snapshot.draft.modules.filter(m=>m.prototype.id.startsWith('gtw.module.ew.')).length===i+1,'place '+name);
    assert(snapshot.preview.valid);
    const placed=snapshot.draft.modules.find(m=>m.prototype.id.startsWith('gtw.module.ew.')&&m.placement.anchor_half_cell[0]===0&&m.placement.anchor_half_cell[1]===i*2);
    const m=snapshot.preview.model.layout.modules.find(m=>m.id===placed?.id);
    assert(m);assert.equal(m.internal_cells.length,0);assert.equal(m.top_cells.length,1);assert.equal(m.clearance_spatial_keys.length,0);
    checks.push(name+' placed through real catalog and canvas, with no internal space or turret clearance.');
  }
  await button('↖ 拖动部件').click();await page.mouse.move(20,20);await page.screenshot({path:path.join(out,'installed.png'),fullPage:true});
  await button('另存文件').click();await until(()=>snapshot.file_label==='countermeasures.json'&&!snapshot.dirty,'save');
  const saved=structuredClone(snapshot.preview.model.layout);
  await button('返回编辑器入口').click();await button('打开舾装文件').click();await until(()=>snapshot.file_label==='countermeasures.json','reopen');
  assert.deepEqual(snapshot.preview.model.layout,saved);assert(snapshot.preview.valid);assert.deepEqual(errors,[]);
  checks.push('All five placements retain exact geometry and prototype versions on save/reopen.');
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'EW_INSTALLATION_UI_PASS',checks},null,2));console.log(JSON.stringify({out,checks}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();if(backend.exitCode===null)await new Promise(r=>{backend.once('exit',r);backend.kill();});}
