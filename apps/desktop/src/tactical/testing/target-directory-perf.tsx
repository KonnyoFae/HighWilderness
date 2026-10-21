// Isolated production-build benchmark. Data comes from the frozen test battle.
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {TargetDirectoryPanel} from '../TargetDirectoryPanel';
import {FireControlPanel} from '../FireControlPanel';
import type {ObservationShip} from '../FireControlPanel';
import type {TargetDirectory} from '../model';
import '../../styles.css';
import '../battleWorkspace.css';

interface Frame {step:number;directory:TargetDirectory;ship:ObservationShip;names:Record<string,string>;memorySeconds:number}
type Variant='legacy'|'collapsed'|'expanded';
const root=createRoot(document.getElementById('root')!);
const noop=()=>{};
function FrameView({frame,variant}:{frame:Frame;variant:Variant}){
  return <main className="app-shell tactical-app tactical-workspace"><section className="panel tactical-panel battle-workspace canvas-workspace" style={{position:'relative',height:'calc(100dvh - 48px)'}}>
    <aside className="battle-dock dock-fire"><div className="battle-dock-body">
      <div className="dock-heading"><h3>火控</h3><small>目标列表更新测试</small></div>
      {variant==='legacy'?<FireControlPanel tab="fire_control" compact ship={frame.ship} names={frame.names} disabled onCommand={noop} onFocus={noop}/>:
        <TargetDirectoryPanel directory={frame.directory} ship={frame.ship} names={frame.names} memorySeconds={frame.memorySeconds} disabled onCommand={noop} onFocus={noop}/>}
      <nav className="battle-tabs"><button>火炮</button><button>航空</button><button>导弹</button></nav>
    </div></aside>
  </section></main>;
}
let active:Variant|undefined;
function render(frame:Frame,variant:Variant){
  if(active!==variant){flushSync(()=>root.render(null));active=variant;}
  flushSync(()=>root.render(<FrameView frame={frame} variant={variant}/>));
}
declare global {interface Window {
  __directoryBench:{
    mount(frame:Frame,variant:Variant):void;
    update(frame:Frame,variant:Variant):{commitMs:number;layoutMs:number;rows:number;nodes:number;width:number;scrollWidth:number};
    clear():void;
  }
}}
window.__directoryBench={
  mount(frame,variant){render(frame,variant);
    if(variant==='expanded')for(const el of document.querySelectorAll<HTMLButtonElement>('.target-group-heading[aria-expanded="false"]'))el.click();
  },
  update(frame,variant){
    const start=performance.now();render(frame,variant);const committed=performance.now();
    const panel=document.querySelector<HTMLElement>('.fire-control-targets')!;
    const width=panel.clientWidth,scrollWidth=panel.scrollWidth,layout=performance.now();
    return {commitMs:committed-start,layoutMs:layout-committed,rows:panel.querySelectorAll('.fire-contact').length,
      nodes:panel.querySelectorAll('*').length,width,scrollWidth};
  },
  clear(){flushSync(()=>root.render(null));active=undefined;},
};
