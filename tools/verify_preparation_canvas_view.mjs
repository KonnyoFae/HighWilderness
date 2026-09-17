// Production React/Python round trip in isolated storage; no fake inventory or deployment.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(path.join(process.env.HW_BROWSER_MODULES,'package.json'));
const {chromium}=require('playwright');
const out=path.resolve(process.env.HW_PREPARATION_CANVAS_OUT??`artifacts/tactical-preparation-5a-${Date.now()}`);
await mkdir(out,{recursive:true});
const store=path.join(out,'store'),pending=new Map(),errors=[],checks=[];
let backend,browser,page,serial=0,packet,scene,form,loseImport=true,slowImport=true,loseCommit=true,loseEntry=true;
const file=path.resolve('artifacts/x1a1-preparation-contract/saved-outfit.json');
function start(){
  backend=spawn('python',['-X','utf8','-m','backend.high_wilderness_sidecar','--instance-id','backend.e3bbrowser','--settlement-dir',store],{windowsHide:true});
  backend.stderr.on('data',v=>errors.push(String(v)));
  createInterface({input:backend.stdout}).on('line',line=>{const v=JSON.parse(line),p=pending.get(v.request_id);
    if(p){pending.delete(v.request_id);clearTimeout(p.timer);v.ok?p.resolve(v.result):p.reject(new Error(JSON.stringify(v.error)));}});
}
function request(method,params,session_id=null,expected_revision=null){return new Promise((resolve,reject)=>{
  const request_id=`req.${++serial}`,timer=setTimeout(()=>{pending.delete(request_id);reject(new Error(`Timeout ${method}: ${errors.join('')}`));},30000);
  pending.set(request_id,{resolve,reject,timer});
  backend.stdin.write(JSON.stringify({interface:'gaotian.web-bridge/v1alpha1',kind:'request',backend_instance_id:'backend.e3bbrowser',request_id,
    method,params,session_id,expected_revision})+'\n');
});}
async function stop(){const child=backend;if(child.exitCode===null)await new Promise(resolve=>{child.once('exit',resolve);child.kill();});}
async function hello(){await request('system.hello',{client_name:'preparation.5a.browser',client_version:'1',supported_interfaces:['gaotian.web-bridge/v1alpha1'],required_capabilities:['tactical.preparation.scene_read']});}
const until=async(fn,label,timeout=30000)=>{const end=Date.now()+timeout;while(!fn()&&Date.now()<end)await page.waitForTimeout(50);assert(fn(),label);};
start();
try {
  await hello();browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.exposeFunction('__e3b_request',async req=>{
    if(req.method==='__choose_file')return request('editor.bind_file',{host_path:file,mode:'open'});
    if(req.method==='tactical.preparation.import'&&slowImport){slowImport=false;await new Promise(r=>setTimeout(r,1500));}
    const result=await request(req.method,req.params,req.session_id??null,req.expected_revision??null);
    if(req.method==='tactical.preparation.scene_read')packet=result;
    if(req.method==='tactical.preparation.scene_save'&&packet)packet={...packet,scene:result};
    if(req.method==='tactical.preparation.open'||req.method==='tactical.preparation.read')form=result;
    if(req.method==='tactical.preparation.draft'&&form)form={...form,draft:result};
    if(result.interface==='gaotian.realtime-view/e3b-v1alpha1')scene={...result,view:{...result.view,static:result.view.static??scene?.view.static}};
    if(req.method==='tactical.preparation.import'&&loseImport){loseImport=false;throw new Error('测试：导入完成后的回执丢失');}
    if(req.method==='tactical.preparation.commit'&&loseCommit){loseCommit=false;throw new Error('测试：准备保存后的回执丢失');}
    if(req.method==='tactical.realtime.deploy_encounter'&&loseEntry){loseEntry=false;throw new Error('测试：入战成功后的回执丢失');}
    return result;
  });
  const button=name=>page.getByRole('button',{name,exact:true});
  const url=process.env.HW_PREPARATION_CANVAS_URL??'http://127.0.0.1:1423/e3b-test.html?entry=tactical';
  await page.goto(url);await button('导入栖装文件').waitFor();
  await button('导入栖装文件').click();await page.getByRole('status').filter({hasText:'正在加载「'}).waitFor();
  await page.getByRole('tab',{name:'敌方',exact:true}).click();
  await button('重试本次操作').click();await until(()=>packet?.scene.sides[1].ships.length===1,'retry adds exactly one player ship on captured side');
  assert.equal(packet.scene.sides[0].ships.length,0);
  await until(()=>packet.geometry.ships.length===1,'geometry refreshed');
  await page.getByRole('tab',{name:'敌方',exact:true}).click();await button('导入栖装文件').click();
  await until(()=>packet?.geometry.ships.length===2,'enemy appears');
  await page.getByRole('tab',{name:'我方',exact:true}).click();await button('导入栖装文件').click();
  await until(()=>packet?.geometry.ships.length===3,'second player appears');
  const ids=packet.scene.sides.flatMap(s=>s.ships.map(m=>m.instance_id));assert.equal(new Set(ids).size,3);
  checks.push('Slow file import shows loading; side switching preserves captured destination; lost reply retries once without duplicating ships. Both fleets are simultaneously visible.');
  assert.equal(packet.scene.sides[0].ships.length,1);assert.equal(packet.scene.sides[1].ships.length,2);
  const enemyBox=await page.getByRole('region',{name:'敌方编队画布'}).boundingBox(),playerBox=await page.getByRole('region',{name:'我方编队画布'}).boundingBox();
  assert(enemyBox.y<playerBox.y);assert(playerBox.y+playerBox.height<=1000);
  await page.getByText('编队位置与旗舰',{exact:true}).click();
  await page.getByLabel('舰艇横向（米）',{exact:true}).fill('220');await page.getByLabel('舰艇横向（米）',{exact:true}).press('Tab');
  await until(()=>packet.scene.sides[1].ships[1].x_m===220,'formation edited');
  const dragTarget=page.getByRole('region',{name:'我方编队画布'}).getByRole('button',{name:/^选择我方舰艇 /}).last().locator('polygon').first();
  const dragBox=await dragTarget.boundingBox();assert(dragBox);
  await page.mouse.move(dragBox.x+dragBox.width/2,dragBox.y+dragBox.height/2);await page.mouse.down();
  await page.mouse.move(dragBox.x+dragBox.width/2+25,dragBox.y+dragBox.height/2+10,{steps:5});await page.waitForTimeout(100);await page.mouse.up();
  await until(()=>packet.scene.sides[1].ships[1].x_m!==220,'canvas drag changes saved layout');
  await page.getByLabel('舰艇横向（米）',{exact:true}).fill('220');await page.getByLabel('舰艇横向（米）',{exact:true}).press('Tab');
  await until(()=>packet.scene.sides[1].ships[1].x_m===220,'restore x after drag');
  await page.getByLabel('舰艇纵向（米）',{exact:true}).fill('0');await page.getByLabel('舰艇纵向（米）',{exact:true}).press('Tab');
  await until(()=>packet.scene.sides[1].ships[1].y_m===0,'restore y after drag');
  await page.getByLabel('舰艇航向（度）',{exact:true}).fill('15');await page.getByLabel('舰艇航向（度）',{exact:true}).press('Tab');
  await until(()=>Math.abs(packet.scene.sides[1].ships[1].heading_rad-Math.PI/12)<1e-8,'heading edited');
  await page.getByLabel('初始交战距离（公里）',{exact:true}).fill('0');await button('应用距离').click();
  await page.getByRole('alert').filter({hasText:'0.001—1000'}).waitFor();assert.equal(packet.scene.distance_m,1000);
  await page.getByLabel('初始交战距离（公里）',{exact:true}).fill('.6');await button('应用距离').click();
  await until(()=>packet.scene.distance_m===600,'near distance');
  await button('配置双方舰内物资').click();await until(()=>form?.ships.length===3,'shared supply draft');
  const controls=page.getByRole('complementary',{name:'战前部件面板'});
  async function draftSaved(){await until(()=>form?.draft&&true,'draft');await page.getByRole('status').filter({hasText:'草稿已保存，尚未扣费。'}).waitFor();}
  for(const fleet of ['enemy','player']){
    const rows=page.locator(`.preparation-roster.${fleet} button`);const n=await rows.count();
    for(let i=0;i<n;i++){
      await rows.nth(i).click();await controls.getByRole('button',{name:'货舱',exact:true}).click();
      await page.getByLabel('弹药库 1 装载目标',{exact:true}).fill('80');await draftSaved();
      await controls.getByRole('button',{name:'火炮',exact:true}).click();await button('本舰武器各预装一批').click();await draftSaved();
      await page.getByLabel('武器 1 弹药种类',{exact:true}).selectOption('recipe.x1a.ordinary');await draftSaved();
    }
  }
  const selected=packet.geometry.ships.find(s=>s.id===packet.scene.sides[1].ships[1].instance_id);
  const m=selected.modules.find(m=>m.category==='weapon');
  const svgModule=page.getByRole('region',{name:'我方编队画布'}).getByRole('button',{name:`选择部件 ${selected.name} ${m.name}`,exact:true}).last();
  await svgModule.locator('rect').first().click();
  assert.equal(await controls.getByRole('button',{name:'火炮',exact:true}).getAttribute('aria-pressed'),'true');
  await page.locator('.selected-preparation-module').waitFor();
  await page.getByText('编队位置与旗舰',{exact:true}).click();
  await controls.evaluate(e=>e.scrollTop=0);
  await page.screenshot({path:path.join(out,'preparation.png'),fullPage:true});
  await button('核对资源与预装填').click();await button('保存准备').click();await button('重试保存准备').click();
  await page.getByRole('heading',{name:'已保存准备结果',exact:true}).waitFor();
  await button('按当前编队进入交战').click();
  await button('开始交战').waitFor();await until(()=>scene?.view?.ships.length===3,'three real ships deployed');
  const map=scene.view.static.resources.instance_mapping;assert.equal(map.length,3);assert.deepEqual(new Set(map.map(s=>s.instance_id)),new Set(ids));
  const requestScene=scene.view.static.resources.encounter;
  const poses=requestScene.sides.map(s=>s.ships[0].deployment);assert.equal(poses[0].y_m-poses[1].y_m,600);
  assert(scene.view.gunnery.weapons.every(w=>w.ready_rounds>0));
  checks.push('Formation position and heading persist; invalid distance rejected; module selection opens matching tab; a shared all-ship preparation commits idempotently and deploys exactly three saved ships.');
  await button('开始交战').click();await until(()=>scene.view.gunnery.damage.hits>0,'actual gunfire hits');
  await button('暂停交战').click();await until(()=>!scene.status.running,'paused');
  const hits=scene.view.gunnery.damage.hits;
  await button('结束本场交战').click();await button('保存全部战后结果').click();await until(()=>scene.settlement.saved,'all-side settlement saved');
  const saved=structuredClone(scene.settlement.result);assert.equal(saved.ships.length,3);
  await button('管理战后库存与下一场准备').click();await button('配置双方舰内物资').waitFor();
  await until(()=>packet.ships.every(s=>s.revision>=2),'updated ship states returned');
  const layout=structuredClone(packet.scene);
  await stop();start();await hello();await page.reload();await button('配置双方舰内物资').waitFor();
  assert.deepEqual(packet.scene,layout);
  for(const ship of packet.ships)assert.deepEqual(ship.state,saved.ships.find(s=>s.after.state.instance_id===ship.instance_id).after.state);
  await page.getByLabel('初始交战距离（公里）',{exact:true}).fill('50');await button('应用距离').click();await until(()=>packet.scene.distance_m===50000,'far distance saved');
  await button('按当前编队进入交战').click();await until(()=>scene?.view?.static?.resources?.encounter?.world_revision>layout.revision,'second scene');
  const second=scene.view.static.resources.encounter;assert.equal(second.sides[0].ships[0].deployment.y_m-second.sides[1].ships[0].deployment.y_m,50000);
  assert.equal(second.sides[1].ships[1].deployment.x_m,220);assert.equal(second.sides[1].ships[1].deployment.heading_rad,Math.PI/12);
  checks.push('Real gunfire and three-ship settlement; return, sidecar restart and second entry retain both sides’ damage/ammunition, identity and formation while flagship distance changes from 600 m to 50 km.');
  assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await writeFile(path.join(out,'result.json'),JSON.stringify({status:'TACTICAL_PREPARATION_5A_UI_PASS',hits,checks},null,2));
  console.log(JSON.stringify({out,checks,hits}));
}catch(e){if(page){await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});await writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText());}throw e;}
finally{await browser?.close();await stop();}
