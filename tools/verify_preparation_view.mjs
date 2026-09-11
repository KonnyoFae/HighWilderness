// Real React + Python integration; file dialog is a test grant, not native UI QA.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile,copyFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_PREPARATION_OUT??'artifacts/x1a3-preparation-browser');
await mkdir(out,{recursive:false});
const file=path.join(out,'saved-outfit.json');
await copyFile(process.env.HW_PREPARATION_INPUT??'artifacts/x1a1-preparation-contract/saved-outfit.json',file);
let backend,browser,page,serial=0,loseDraft=false,loseCommit=false,lastPacket,scene=null,loseEntry=false,loseSettlement=false;
let chosenFile=file,lastEditor;
const pending=new Map(),errors=[],checks=[];
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',path.join(out,'.local','store')],{windowsHide:true});
  backend.stderr.on('data',d=>errors.push(String(d)));
  createInterface({input:backend.stdout}).on('line',line=>{
    const value=JSON.parse(line),p=pending.get(value.request_id);
    if(p){pending.delete(value.request_id);clearTimeout(p.timer);value.ok?p.resolve(value.result):p.reject(new Error(JSON.stringify(value.error)));}
  });
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}`));},20000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,session_id,expected_revision,method,params})+'\n');
});}
async function hello(){await request('system.hello',{client_name:'x1a3.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.commit']});}
async function stop(){const process=backend;await new Promise(resolve=>{process.once('exit',resolve);process.kill();});}
start();
try{
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});
  page=await browser.newPage({viewport:{width:1480,height:1100}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:chosenFile,mode:req.params.method??'open'},req.params.session_id??null,req.params.expected_revision??null);
    const value=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method.startsWith('editor.')&&value?.draft)lastEditor=value;
    const realtime=value.interface==='gaotian.realtime-view/e3b-v1alpha1'?value:req.method==='tactical.realtime.prepared_entry'?value.scene:null;
    if(realtime)scene={...realtime,view:{...realtime.view,static:realtime.view.static??(scene?.status.epoch===realtime.status.epoch?scene.view.static:null)}};
    if(['tactical.preparation.open','tactical.preparation.read'].includes(req.method))lastPacket=value;
    if(loseDraft&&req.method==='tactical.preparation.draft'){loseDraft=false;throw new Error('fixture: draft reply lost');}
    if(loseCommit&&req.method==='tactical.preparation.commit'){loseCommit=false;throw new Error('fixture: commit reply lost');}
    if(loseEntry&&req.method==='tactical.realtime.deploy_prepared'){loseEntry=false;throw new Error('fixture: entry reply lost');}
    if(loseSettlement&&req.method==='tactical.realtime.save'){loseSettlement=false;throw new Error('fixture: settlement reply lost');}
    return value;
  });
  const url=process.env.HW_PREPARATION_URL??'http://127.0.0.1:1421/e3b-test.html';
  const button=name=>page.getByRole('button',{name,exact:true});
  async function saved(){await page.waitForFunction(()=>[...document.querySelectorAll('[role="status"]')].some(el=>el.textContent==='草稿已保存，尚未扣费。'));}
  await page.goto(url);
  if(process.env.HW_FILLING_EDITOR==='1'){
    const original=JSON.parse(await readFile(path.join(path.dirname(process.env.HW_PREPARATION_INPUT),'old-hull.json'),'utf8'));
    chosenFile=path.join(out,'ui-hull.json');await writeFile(chosenFile,JSON.stringify(original));
    await button('打开文件').click();
    const choice=page.getByLabel('本层边缘填充',{exact:true});await choice.waitFor();
    assert.equal(await choice.inputValue(),'gtw.filling.none');
    await choice.selectOption('gtw.filling.rack');
    await page.getByRole('region',{name:'本层填充容量',exact:true}).waitFor();
    await button('撤销').click();await page.getByRole('region',{name:'本层填充容量',exact:true}).waitFor({state:'detached'});
    assert.equal(await choice.inputValue(),'gtw.filling.none');
    await button('重做').click();await page.getByRole('region',{name:'本层填充容量',exact:true}).waitFor();
    await page.getByLabel('当前甲板',{exact:true}).selectOption(original.decks[1].id);
    assert.equal(await choice.inputValue(),'gtw.filling.none');
    await page.getByLabel('当前甲板',{exact:true}).selectOption(original.decks[0].id);
    assert.equal(await choice.inputValue(),'gtw.filling.rack');
    await page.screenshot({path:path.join(out,'hull-filling.png'),fullPage:true});
    await button('保存').click();await page.getByText('与源资源一致',{exact:false}).waitFor();
    const saved=JSON.parse(await readFile(chosenFile,'utf8'));
    assert.equal(saved.schema,'gaotian.hull/v2alpha1');
    assert.equal(saved.decks[0].filling.id,'gtw.filling.rack');
    assert.equal(saved.decks[1].filling.id,'gtw.filling.none');
    await button('关闭会话').click();await button('打开文件').click();await choice.waitFor();
    assert.equal(await choice.inputValue(),'gtw.filling.rack');
    assert(lastEditor.preview.valid);
    await button('关闭会话').click();chosenFile=file;
    await button('打开文件').click();await page.getByRole('region',{name:'舾装二维画布',exact:true}).waitFor();
    await page.getByRole('region',{name:'本层填充容量',exact:true}).waitFor();
    assert(lastEditor.preview.model.filling_decks.some(d=>d.cargo_capacity_cm3>0&&d.pieces.length>0));
    await page.screenshot({path:path.join(out,'outfit-filling.png'),fullPage:true});
    await button('关闭会话').click();
    checks.push('H5b real hull selector migrates v1 explicitly; undo/redo and independent layer choice; save/reopen retains rack; outfit shows filling geometry and capacity');
  }
  await button('战前准备').click();
  await button('导入已保存的栖装文件').click();
  await page.getByLabel('选择准备舰船 1',{exact:true}).waitFor();
  const sources=await page.getByLabel('准备目录设计').locator('option').evaluateAll(elements=>elements.map(e=>({value:e.value,label:e.textContent})));
  await page.getByLabel('准备目录设计').selectOption(sources.find(s=>s.label.includes('常规有人')).value);
  await button('从目录设计添加舰船').click();
  await page.getByLabel('选择准备舰船 2',{exact:true}).waitFor();
  await button('准备所选舰船').click();
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).waitFor();
  const ids=lastPacket.draft.ships.map(s=>s.instance_id);
  for(let i=0;i<2;i++){
    await page.getByLabel('当前准备舰船').selectOption(ids[i]);
    await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('20');
    if(lastPacket.ships[i].resources.holds.length)await page.getByLabel('特殊合金装载目标',{exact:true}).fill(String(i+2));
    await button('本舰武器各预装一批').click();await saved();
    if(process.env.HW_SPECIAL_AMMO==='1'){
      await page.getByLabel('武器 1 弹药种类',{exact:true}).selectOption('recipe.x1a.special_armor_piercing');await saved();
    }
  }
  checks.push('saved custom outfit grant and catalog design create distinct selectable ships; switching retains per-ship quantities and preload choices');
  loseDraft=true;
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('21');
  await page.getByRole('alert').filter({hasText:'draft reply lost'}).waitFor();
  assert(await page.getByLabel('弹药库 1 装载目标',{exact:true}).isDisabled());
  await button('保存准备草稿').click();await saved();
  const list=await request('tactical.preparation.library',{});
  assert.equal(list.drafts.length,1);
  const before=await request('tactical.preparation.read',{preparation_id:list.drafts[0].preparation_id});
  assert.equal(before.supply.ammunition_resources,1000);
  assert(before.ships.every(s=>s.state.magazines.every(m=>m.quantity===0)));
  checks.push('autosave overwrites one draft without inventory mutation; lost draft reply locks editing and exact retry succeeds');
  await button('舰艇编辑').click();await button('战前准备').click();
  assert.equal(await page.getByLabel('弹药库 1 装载目标',{exact:true}).inputValue(),'21');
  await page.goto('about:blank');await stop();start();await hello();await page.goto(url);
  await button('战前准备').click();await button('打开准备 1 · 草稿').click();
  await page.getByLabel('弹药库 1 装载目标',{exact:true}).waitFor();
  assert.deepEqual(lastPacket.draft,before.draft);
  checks.push('view switching retains form; actual sidecar restart restores exact multi-ship draft and unchanged stocks');
  await page.getByLabel('武器 1 预装填批次',{exact:true}).fill('2');await saved();
  await button('核对资源与预装填').click();
  await page.getByRole('alert').filter({hasText:'剩余待发容量不足以装下所选整批弹药'}).waitFor();
  assert(await button('保存准备').isDisabled());
  await page.getByLabel('武器 1 预装填批次',{exact:true}).fill('1');await saved();
  checks.push('invalid whole-batch preload identifies the weapon and blocks commit; correcting the choice restores preview');
  await button('核对资源与预装填').click();
  await page.getByRole('heading',{name:'准备变动预览',exact:true}).waitFor();
  await page.screenshot({path:path.join(out,'preview.png'),fullPage:true});
  loseCommit=true;await button('保存准备').click();
  await page.getByRole('alert').filter({hasText:'commit reply lost'}).waitFor();
  assert(await button('返回舰船列表').isDisabled());
  assert(await page.getByLabel('弹药库 1 装载目标',{exact:true}).isDisabled());
  const committed=await request('tactical.preparation.read',{preparation_id:before.draft.preparation_id});
  assert(committed.receipt);
  await button('重试保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await page.getByText('本次准备后供给：弹药资源 959 点 · 特殊合金 95 份',{exact:true}).waitFor();
  await page.getByText(/现有待发 1 \/ 1 发 · (普通弹|穿甲弹)/).waitFor();
  const final=await request('tactical.preparation.read',{preparation_id:before.draft.preparation_id});
  assert.deepEqual(final,committed);
  assert.equal(final.receipt.supply_after.ammunition_resources,959);
  for(let i=0;i<2;i++){
    const state=final.receipt.ships[i].after.state;
    assert.equal(state.magazines.reduce((n,m)=>n+m.quantity,0),20+i-state.weapons.length*5);
    assert(state.weapons.every(w=>w.ready_rounds===1));
    if(process.env.HW_SPECIAL_AMMO==='1'){
      assert.equal(state.weapons[0].recipe_id,'recipe.x1a.special_armor_piercing');
      assert.equal(state.cargo[0].quantity,i+1);
    }
  }
  const cargo=before.draft.ships.reduce((n,s)=>n+s.cargo.reduce((n,c)=>n+c.quantity,0),0);
  assert.equal(final.receipt.supply_after.cargo[0].quantity,100-cargo);
  checks.push('whole-fleet preview matches saved stock/cargo/ready rounds; lost commit reply locks actions and retry does not duplicate cost or revisions');
  await page.screenshot({path:path.join(out,'saved.png'),fullPage:true});
  await page.reload();await button('战前准备').click();await button('打开准备 1 · 已保存').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  assert.deepEqual(lastPacket.receipt,final.receipt);assert.deepEqual(errors,[]);
  checks.push('completed receipt remains queryable after reload');
  if(process.env.HW_PREPARATION_BATTLE==='1'){
    const until=async(predicate,message)=>{const deadline=Date.now()+16000;while(!predicate()&&Date.now()<deadline)await page.waitForTimeout(50);assert(predicate(),message);};
    loseEntry=true;await button('以所选舰为旗舰进入交战').click();
    await page.locator('.tactical-canvas canvas').waitFor();
    await page.getByText(/结构耐久.*由结构体积与材料冗余决定/).waitFor();
    await until(()=>scene?.view.static?.ships.length===3,'prepared 2 ships + technical enemy');
    const entry=structuredClone(scene),gun=()=>scene.view.gunnery.weapons.find(w=>w.ship_id===scene.direct_ship_id);
    assert(entry.view.static.ships.some(s=>s.modules.some(m=>m.id==='custom.cargo')));
    assert.equal(entry.view.static.scenario_id,'gtw.prepared.skirmish.v1');
    assert(entry.view.static.ships.every(s=>s.structural_durability?.policy_id==='gaotian.structural-redundancy/v1'&&s.structural_durability.maximum_points>0));
    assert(await button('返回战前准备').isDisabled());
    await button('开始试航').click();
    await page.getByLabel('所控火炮',{exact:true}).selectOption(gun().module_id);
    if(process.env.HW_SPECIAL_AMMO==='1'){
      assert.equal(gun().loaded_recipe_id,'recipe.x1a.special_armor_piercing');
      await page.getByLabel('下一批装填弹种',{exact:true}).selectOption('recipe.x1a.ordinary');
      await until(()=>gun().selected_recipe_id==='recipe.x1a.ordinary','ordinary next batch selected');
    }
    await button('瞄准红方测试舰').click();
    if(process.env.HW_SPECIAL_AMMO==='1'){
      await until(()=>gun().shots>=1&&gun().loading_recipe_id==='recipe.x1a.ordinary','AP fired while next ordinary batch loads');
      await page.getByLabel('下一批装填弹种',{exact:true}).selectOption('recipe.x1a.special_armor_piercing');
      await until(()=>gun().loading_recipe_id==='recipe.x1a.special_armor_piercing','unfinished ordinary batch replaced by AP');
      await page.screenshot({path:path.join(out,'special-reload.png'),fullPage:true});
    }
    await until(()=>gun().shots>=2&&scene.view.gunnery.damage.hits>0&&scene.view.ships.some(s=>s.id!=='ship.web.red'&&s.hull_integrity<1),'real fire, automatic reload and damage to prepared player ship');
    if(process.env.HW_SPECIAL_AMMO==='1'){
      assert(scene.view.gunnery.damage.recent.some(h=>h.projectile_type==='projectile.s1.armor_piercing'));
      checks.push('S1 AP preload debits ammunition and special alloy; UI selects next ordinary batch without replacing the loaded AP, then switches unfinished reload back to AP; real AP projectile hits');
    }
    await button('暂停试航').click();
    await page.screenshot({path:path.join(out,'prepared-battle.png'),fullPage:true});
    await button('结束交战 / 撤离').click();
    await until(()=>scene.settlement?.result,'ending stages result');
    const battleA=structuredClone(scene.settlement.result);
    if(process.env.HW_SPECIAL_AMMO==='1')assert(battleA.ships.some(s=>s.changes.some(c=>c.resource==='cargo:cargo.special_alloy'&&c.reason==='reload'&&c.delta<0)));
    for(const row of final.receipt.ships){const corresponding=battleA.ships.find(s=>s.before.state.instance_id===row.after.state.instance_id);assert.deepEqual(corresponding.before,row.after);}
    await page.goto('about:blank');await stop();start();await hello();scene=null;await page.goto(url);
    await button('战术视角').click();await button('实时试航（实验）').click();
    await page.getByRole('button',{name:/查看结算 1.*待保存/}).click();
    loseSettlement=true;await button('保存全部战后结果').click();
    await page.getByRole('alert').filter({hasText:'settlement reply lost'}).waitFor();
    await button('保存全部战后结果').click();await button('结算已保存').waitFor();
    await button('战前准备').click();
    await page.getByLabel('选择准备舰船 1',{exact:true}).check();await page.getByLabel('选择准备舰船 2',{exact:true}).check();
    await button('准备所选舰船').click();await page.getByLabel('弹药库 1 装载目标',{exact:true}).waitFor();
    for(const s of lastPacket.ships){const ended=battleA.ships.find(v=>v.after.state.instance_id===s.instance_id);assert.deepEqual(s.state,ended.after.state);}
    await button('核对资源与预装填').click();await page.getByRole('heading',{name:'准备变动预览',exact:true}).waitFor();
    await button('保存准备').click();await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
    await button('以所选舰为旗舰进入交战').click();await page.locator('.tactical-canvas canvas').waitFor();
    await until(()=>scene?.view.static&&scene.status.epoch!==entry.status.epoch,'new prepared battle');
    for(const s of scene.view.ships.filter(s=>s.id!=='ship.web.red')){
      const ended=battleA.ships.find(v=>v.after.ship_id===s.id);
      assert.equal(s.hull_integrity,ended.after.state.hull_integrity_fraction);
      assert.deepEqual(s.modules.map(m=>[m.id,m.durability]),ended.after.state.modules.map(m=>[m.module_id,m.durability_points]));
    }
    assert(scene.view.gunnery.weapons.every(w=>w.target_ship_id===null&&w.shots===0));
    await page.screenshot({path:path.join(out,'second-prepared-battle.png'),fullPage:true});
    await button('结束交战 / 撤离').click();await button('保存全部战后结果').click();
    await until(()=>scene.settlement?.saved,'second settlement saved');
    await button('返回战前准备').click();await page.getByRole('button',{name:'准备所选舰船',exact:true}).waitFor();
    const after=await request('tactical.preparation.library',{});assert(after.ships.every(s=>!s.blocked));
    await writeFile(path.join(out,'battle-a.json'),JSON.stringify(battleA,null,2));
    await writeFile(path.join(out,'battle-b.json'),JSON.stringify(scene.settlement.result,null,2));
    checks.push('prepared custom fleet enters actual battle; lost entry reply resolves without new scene; auto fire/reload/hits; pending settlement survives backend restart; lost save reply retries; same damaged ships reprepare and enter next battle; return releases claims');
  }
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'PASS',scope:'React + Python stdio in headless Edge; file grant injected; native dialog/WebView not covered',checks,receipt:final.receipt,errors},null,2));
  console.log(JSON.stringify({status:'PASS',checks}));
}catch(error){
  if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});console.error(await page.locator('body').innerText());}
  await writeFile(path.join(out,'failure.json'),JSON.stringify({error:String(error),errors},null,2));throw error;
}finally{await browser?.close();backend.kill();}
