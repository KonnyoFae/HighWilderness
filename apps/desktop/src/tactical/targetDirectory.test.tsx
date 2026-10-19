import {expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import type {ObservedContact,ObservationShip} from './FireControlPanel';
import type {TargetDescriptor,TargetDirectory} from './model';
import {targetKey,targetRows,targetPage,TARGET_PAGE_SIZE} from './targetDirectory';
import {TargetDirectoryPanel} from './TargetDirectoryPanel';

const entry=(id:string|number,category:TargetDescriptor['category']='ship'):TargetDescriptor=>
  ({key:targetKey(category,id),target_id:id,name:`名册 ${id}`,category});
const contact=(id:string|number,changes:Partial<ObservedContact>={}):ObservedContact=>
  ({id,kind:'ship',position_m:[10,20],velocity_mps:[3,4],height_layer:'cloud',valid:true,age_s:.2,
    sources:['radar'],status:'tracked',radar_source_available:true,...changes});
const ship=(contacts:ObservedContact[]):ObservationShip=>({ship_id:'own',contacts,devices:[],locked_target_id:null,lock_status:null});
const directory=(entries:TargetDescriptor[],remembered:TargetDescriptor[]=[]):TargetDirectory=>
  ({interface:'gaotian.target-directory/v1',epoch:'battle',revision:1,entries,remembered});

it('joins by family and original ID type, including bombs using missile observations',()=>{
  const d=directory([entry('same'),entry('same','aircraft'),entry(1,'bomb'),entry('1','shell')]);
  const rows=targetRows(d,ship([contact('same',{kind:'aircraft'}),contact(1,{kind:'missile'})]),{},3);
  expect(new Set(rows.map(r=>r.key)).size).toBe(4);
  expect(rows.map(r=>r.state)).toEqual(['unseen','tracked','tracked','unseen']);
  expect(rows[2].category).toBe('bomb');expect(rows[2].contact!.kind).toBe('missile');
});
it('unknown entries expose no pose and cannot lock; observer changes do not retain another ship contacts',()=>{
  const d=directory([entry('enemy')]);
  expect(targetRows(d,ship([contact('enemy')]),{},3)[0].canLock).toBe(true);
  for(const observer of [ship([]),undefined]){
    const row=targetRows(d,observer,{},3)[0];
    expect(row.state).toBe('unseen');expect(row.contact).toBeUndefined();expect(row.canLock).toBe(false);
  }
  expect(targetRows(d,ship([contact('enemy',{status:'no_computer'})]),{},3)[0]).toMatchObject({state:'tracked',canLock:false});
});
it('lost contacts retain measured history only until expiry; exits do not invent a destruction outcome',()=>{
  const bomb=entry(7,'bomb'),lost=contact(7,{kind:'missile',valid:false,age_s:2.9});
  const d=directory([],[bomb]);
  const rows=targetRows(d,ship([lost]),{},3);
  expect(rows[0]).toMatchObject({name:bomb.name,category:'bomb',active:false,state:'lost',canLock:false});
  expect(rows[0].contact!.position_m).toEqual([10,20]);
  expect(targetRows(d,ship([{...lost,age_s:3.1}]),{},3)).toEqual([]);
  expect(targetRows(d,ship([]),{},3)).toEqual([]);
  expect(targetRows(directory([bomb]),ship([]),{},3)[0].state).toBe('unseen');
});
it('legacy snapshots use only contacts, never all ship names; dense pages are bounded and clamp on removal',()=>{
  expect(targetRows(undefined,ship([]),{secret:'未发现敌舰'},3)).toEqual([]);
  const d=directory(Array.from({length:1000},(_,i)=>entry(i,'shell')));
  const rows=targetRows(d,ship([]),{},3);
  expect(rows.map(r=>r.target_id)).toEqual(d.entries.map(d=>d.target_id));
  expect(targetPage(rows,0).rows).toHaveLength(TARGET_PAGE_SIZE);
  expect(targetPage(rows,999).rows.at(-1)!.target_id).toBe(999);
  expect(targetPage(rows.slice(0,2),83)).toMatchObject({page:0,pages:1});
});
it('renders grey unknown rows without kinematics, and keeps dense projectile groups collapsed',()=>{
  const d=directory([entry('hidden'),entry('seen'),...Array.from({length:100},(_,i)=>entry(i,'shell'))]);
  const html=renderToStaticMarkup(<TargetDirectoryPanel directory={d} ship={ship([contact('seen')])}
    memorySeconds={3} names={{hidden:'不应取此真值名称'}} disabled={false} onCommand={()=>{}} onFocus={()=>{}}/>);
  expect(html).toContain('102 个活动目标');expect(html).toContain('名册 hidden');
  expect(html).toContain('data-target-state="unseen"');expect(html).toContain('未发现');
  expect(html.match(/data-target-key=/g)).toHaveLength(2);
  expect(html.match(/秒前观测/g)).toHaveLength(1);
  expect(html).not.toContain('不应取此真值名称');expect(html).not.toContain('已击毁');
});
