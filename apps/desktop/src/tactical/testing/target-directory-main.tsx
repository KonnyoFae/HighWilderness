// Development-only deterministic UI cases; this does not simulate a battle.
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {TargetDirectoryPanel} from '../TargetDirectoryPanel';
import {targetKey} from '../targetDirectory';
import type {TargetDirectory,TargetDescriptor} from '../model';
import type {ObservationShip,ObservedContact} from '../FireControlPanel';
import '../../styles.css';
import '../battleWorkspace.css';

const descriptor=(id:string|number,category:TargetDescriptor['category'],name:string):TargetDescriptor=>
  ({key:targetKey(category,id),target_id:id,category,name});
const vessels=[descriptor('enemy','ship','苍穹号'),descriptor('aircraft','aircraft','E1 预警电子战机')];
const shells=Array.from({length:1000},(_,i)=>descriptor(i,'shell',`炮弹 #${i}`));
const measurement:ObservedContact={id:'enemy',kind:'ship',position_m:[1200,3400],velocity_mps:[3,4],height_layer:'cloud',
  valid:true,age_s:.2,sources:['雷达'],status:'tracked',radar_source_available:true};
function Fixture(){
  const [mode,setMode]=useState('unseen'),[observer,setObserver]=useState('own-a'),[tab,setTab]=useState('火炮');
  const [commands,setCommands]=useState(0),[focus,setFocus]=useState('未定位'),[epoch,setEpoch]=useState(0);
  const entries=mode==='removed'?[]:mode==='dense'?[...vessels,...shells]:vessels;
  const directory:TargetDirectory={interface:'gaotian.target-directory/v1',epoch:`scene.${epoch}`,revision:1,entries,remembered:mode==='removed'?vessels:[]};
  const hasContact=observer==='own-a'&&!['unseen','dense','legacy'].includes(mode);
  const contact={...measurement,valid:!['lost','expired','removed'].includes(mode),age_s:mode==='expired'?4:mode==='lost'||mode==='removed'?2:.2,status:mode==='no_computer'?'no_computer':'tracked'};
  const ship:ObservationShip={ship_id:observer,locked_target_id:null,lock_status:null,devices:[],contacts:hasContact?[contact]:[]};
  return <main className="app-shell tactical-app">
    <div className="editor-row"><label>场景<select aria-label="验收场景" value={mode} onChange={e=>setMode(e.target.value)}>
      {['unseen','tracked','lost','expired','no_computer','removed','dense','legacy'].map(v=><option key={v}>{v}</option>)}</select></label>
      <label>操作舰<select aria-label="验收操作舰" value={observer} onChange={e=>setObserver(e.target.value)}><option>own-a</option><option>own-b</option></select></label>
      <button onClick={()=>{setEpoch(epoch+1);setCommands(0);setFocus('未定位');setMode('unseen');}}>新战局</button>
      <output aria-label="验收命令计数">{commands}</output><output aria-label="验收定位">{focus}</output>
    </div>
    <section className="canvas-workspace" style={{position:'relative',height:'calc(100dvh - 100px)',minHeight:400}}>
      <p>目标名册边界与密集列表夹具，仅验证显示和交互。</p>
      <aside className="battle-dock dock-fire"><div className="battle-dock-body">
        <div className="dock-heading"><h3>火控</h3><small>操作舰 · {observer}</small></div>
        <TargetDirectoryPanel key={epoch} directory={mode==='legacy'?undefined:directory} ship={ship} names={{enemy:'不可用于未知目标的姿态名称'}}
          memorySeconds={3} disabled={false} onCommand={()=>setCommands(v=>v+1)} onFocus={c=>setFocus(`${c.position_m.join(',')}/${c.height_layer}`)}/>
        <nav className="battle-tabs" aria-label="武器面板">{['火炮','导弹','航空'].map(v=><button key={v} aria-pressed={tab===v} onClick={()=>setTab(v)}>{v}</button>)}</nav>
        <div className="battle-inspector battle-subpanel">{tab}操作区</div>
      </div></aside>
    </section>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
