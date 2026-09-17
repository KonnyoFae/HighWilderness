import { useEffect, useRef, useState } from 'react';
import type { TacticalStatic } from './model';
import { moduleFootprints, shipPoints } from './viewport';
import { sideName } from './preparationScene';
import type { FormationShip, PreparationScene } from './preparationScene';

type Box = {x:number;y:number;width:number;height:number};
export function PreparationCanvas({fleet,geometry,selected,moduleId,disabled,onSelect,onMove}: {
  fleet:PreparationScene['sides'][number];geometry:TacticalStatic;selected:string;moduleId:string|null;disabled:boolean;
  onSelect:(ship:string,module:string|null)=>void;onMove:(ship:string,x:number,y:number)=>void;
}) {
  const svg=useRef<SVGSVGElement>(null);
  const [level,setLevel]=useState<number|null>(null),[camera,setCamera]=useState<Box|null>(null);
  const [moving,setMoving]=useState<FormationShip|null>(null);
  const drag=useRef<{ship:FormationShip;start:DOMPoint;module:string|null;changed:boolean}|null>(null);
  const levels=[...new Set(geometry.ships.filter(s=>fleet.ships.some(m=>m.instance_id===s.id)).flatMap(s=>s.decks.map(d=>d.level)))].sort((a,b)=>b-a);
  const deck=level!==null&&levels.includes(level)?level:levels[0]??0;
  const points=fleet.ships.flatMap(member=>{
    const ship=geometry.ships.find(s=>s.id===member.instance_id);if(!ship)return [];
    const c=Math.cos(member.heading_rad),s=Math.sin(member.heading_rad);
    return shipPoints(ship).map(p=>({x:member.x_m+p.x*c-p.y*s,y:-member.y_m-p.x*s-p.y*c}));
  });
  const minX=Math.min(0,...points.map(p=>p.x)), maxX=Math.max(0,...points.map(p=>p.x));
  const minY=Math.min(0,...points.map(p=>p.y)), maxY=Math.max(0,...points.map(p=>p.y));
  const fit={x:minX-30,y:minY-30,width:Math.max(180,maxX-minX+60),height:Math.max(100,maxY-minY+60)};
  const box=camera??fit;
  useEffect(()=>setCamera(null),[fleet.ships.length]);
  function local(e:{clientX:number;clientY:number}) {
    return new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.current!.getScreenCTM()!.inverse());
  }
  function zoom(factor:number) {
    const width=Math.max(20,Math.min(200000,box.width*factor)),height=width*box.height/box.width;
    setCamera({x:box.x+(box.width-width)/2,y:box.y+(box.height-height)/2,width,height});
  }
  return <section className={`formation-view ${fleet.id}`} aria-label={`${sideName(fleet.id)}编队画布`}>
    <header><strong>{sideName(fleet.id)}编队 <small>{fleet.ships.length} 艘</small></strong><div>
      <label>甲板 <select aria-label={`${sideName(fleet.id)}显示甲板`} value={deck} onChange={e=>setLevel(Number(e.target.value))}>
        {levels.map(n=><option key={n} value={n}>{n}</option>)}</select></label>
      <button aria-label={`${sideName(fleet.id)}放大`} onClick={()=>zoom(.7)}>＋</button>
      <button aria-label={`${sideName(fleet.id)}缩小`} onClick={()=>zoom(1.4)}>－</button>
      <button onClick={()=>setCamera(null)}>适应编队</button></div></header>
    <svg ref={svg} viewBox={`${box.x} ${box.y} ${box.width} ${box.height}`} aria-label={`${sideName(fleet.id)}舰艇与模块`} role="img"
      onWheel={e=>{if(e.ctrlKey)e.preventDefault();zoom(e.deltaY>0?1.1:.9);}}
      onPointerMove={e=>{if(!drag.current)return;const p=local(e),d=drag.current;
        const dx=p.x-d.start.x,dy=p.y-d.start.y;
        if(Math.hypot(dx,dy)>box.width/500)d.changed=true;
        if(d.changed)setMoving({...d.ship,x_m:d.ship.x_m+dx,y_m:d.ship.y_m-dy});}}
      onPointerUp={e=>{const d=drag.current;if(!d)return;svg.current?.releasePointerCapture(e.pointerId);drag.current=null;
        if(d.changed&&moving)onMove(d.ship.instance_id,Math.round(moving.x_m),Math.round(moving.y_m));
        else onSelect(d.ship.instance_id,d.module);
        setMoving(null);}}
      onPointerCancel={()=>{drag.current=null;setMoving(null);}}>
      <defs><pattern id={`formation-grid-${fleet.id}`} width="50" height="50" patternUnits="userSpaceOnUse"><path d="M 50 0 L 0 0 0 50" fill="none" stroke="#74849b" strokeOpacity=".25" strokeWidth=".5"/></pattern></defs>
      <rect x={box.x} y={box.y} width={box.width} height={box.height} fill={`url(#formation-grid-${fleet.id})`}/>
      {fleet.ships.map(member=>{
        const shape=geometry.ships.find(s=>s.id===member.instance_id);if(!shape)return null;
        const pose=moving?.instance_id===member.instance_id?moving:member;
        const chosen=selected===member.instance_id;
        const color=fleet.id==='enemy'?'#ed977e':'#75c9d6';
        function down(e:React.PointerEvent, module:string|null) {
          e.stopPropagation();onSelect(member.instance_id,module);
          if(disabled||e.button!==0)return;
          setCamera(box);drag.current={ship:member,start:local(e),module,changed:false};
          svg.current!.setPointerCapture(e.pointerId);
        }
        return <g key={member.instance_id} transform={`translate(${pose.x_m} ${-pose.y_m})`}>
          <g transform={`rotate(${-pose.heading_rad*180/Math.PI}) scale(1,-1)`} onPointerDown={e=>down(e,null)}
            role="button" aria-label={`选择${sideName(fleet.id)}舰艇 ${shape.name}`} tabIndex={0} onKeyDown={e=>{if(e.key==='Enter')onSelect(member.instance_id,null);}}>
            {shape.decks.filter(d=>d.level===deck).flatMap(d=>d.regions.map(r=><polygon key={`${d.id}:${r.id}`} points={r.vertices_m.map(p=>p.join(',')).join(' ')}
              fill={chosen?'#25434f':'#25303d'} stroke={color} strokeWidth={chosen?2:1} vectorEffect="non-scaling-stroke"/>))}
            {shape.modules.map(m=>{
              const cells=moduleFootprints(m).filter(c=>c.level===deck);
              if(!cells.length&&m.deck_level!==deck)return null;
              const fill=chosen&&moduleId===m.id?'#ffd27d':m.category==='weapon'?'#c4a163':'#547d83';
              return <g key={m.id} role="button" aria-label={`选择部件 ${shape.name} ${m.name}`} tabIndex={0}
                onPointerDown={e=>down(e,m.id)} onKeyDown={e=>{if(e.key==='Enter'){e.stopPropagation();onSelect(member.instance_id,m.id);}}}>
                <title>{m.name}</title>
                {cells.length?cells.map((c,i)=><rect key={i} x={c.x-c.size/2} y={c.y-c.size/2} width={c.size} height={c.size} fill={fill} stroke="#152532" strokeWidth=".4"/>):
                  <circle cx={m.anchor_m[0]} cy={m.anchor_m[1]} r="2.5" fill={fill}/>}
              </g>;
            })}
          </g>
          <text y={Math.max(12,...shipPoints(shape).map(p=>Math.hypot(p.x,p.y)))+9} textAnchor="middle" fill={color}
            fontSize={Math.max(4,Math.min(12,box.width/65))} pointerEvents="none">{fleet.flagship_instance_id===member.instance_id?'★ ':''}{shape.name}</text>
        </g>;
      })}
    </svg>
    {!fleet.ships.length&&<p className="formation-empty">从左侧加入{sideName(fleet.id)}舰艇</p>}
    <footer>50 米网格 · 拖动舰艇排布 · 点选部件查看 · 滚轮缩放</footer>
  </section>;
}
