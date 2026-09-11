import { useEffect, useRef, useState } from "react";
import type { HullCommand, ModuleOption, OutfitInstance, SessionSnapshot } from "./model";
import { nextId } from "./interaction";
import { fit, gridLines, lowerDeck, screen, world, zoom } from "./viewport";
import type { Camera, Point } from "./viewport";
import { compatibleHosts, defaultRotation, footprint, gridHint, hostedDescendants, mountKind, nearestSlot, placementPoint, sidePreview, visibleAtLevel } from "./outfitCanvas";
import type { OutfitLayout, LayoutModule } from "./outfitCanvas";
import { arcText } from "./weaponGroups";
import { arcVisibleAtLevel, WeaponArcOverlay } from "./WeaponArcOverlay";
import type { WeaponControl } from "./weaponGroups";
import { FillingSummary } from "./FillingSummary";
import type { FillingView } from "./FillingSummary";

const WIDTH = 1000, HEIGHT = 560;
export function OutfitViewport({ session, options, option, selected, onSelect, busy, onCommand, onLocalDraft, operationError }: {
  session: SessionSnapshot; options: ModuleOption[]; option?: ModuleOption; selected: string; onSelect: (id: string) => void;
  busy: boolean; onCommand: HullCommand; onLocalDraft: (value: boolean) => void; operationError?: string;
}) {
  const layoutValue = session.preview.model.layout as OutfitLayout | undefined;
  const layout = layoutValue?.interface === "gaotian.outfit-layout/v1alpha1" ? layoutValue : undefined;
  const decks = layout?.hull.decks ?? session.hull_binding?.hull.decks ?? [];
  const [deckId, setDeckId] = useState(decks.find(d => d.is_base)?.id ?? "");
  const deck = decks.find(d => d.id === deckId) ?? decks[0];
  const filling = (session.preview.model.filling_decks as FillingView[] | undefined)?.find(d => d.deck_id === deck?.id);
  const below = lowerDeck(decks, deck);
  const space = layout?.decks.find(d => d.id === deck?.id);
  const modules = session.draft.modules ?? [];
  const instance = modules.find(m => m.id === selected);
  const control = session.preview.model.weapon_control as WeaponControl | undefined;
  const selectedArc = control && ["gaotian.weapon-control-view/v1alpha1", "gaotian.weapon-control-view/v2alpha1"].includes(control.interface) ? control.arcs.find(a => a.instance_id === selected) : undefined;
  const [showArc, setShowArc] = useState(true);
  const prototypeOf = (m?: OutfitInstance) => options.find(o => o.prototype.id === m?.prototype.id && o.prototype.version === m?.prototype.version);
  const [mode, setMode] = useState<"select" | "place" | "move">("select");
  const [rotation, setRotation] = useState(() => defaultRotation(option));
  const [cursor, setCursor] = useState<Point | null>(null);
  const [moving, setMoving] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  const [layers, setLayers] = useState({ internal: true, top: true, side: true, body: false, clearance: true });
  const [camera, setCamera] = useState<Camera>(() => fit(decks.flatMap(d => d.regions), WIDTH, HEIGHT));
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ point: Point; camera: Camera; target: string; pan: boolean; moved: boolean; anchor?: Point } | null>(null);
  const activeInstance = modules.find(m => m.id === moving) ?? (mode === "move" ? instance : undefined);
  const activeOption = activeInstance ? prototypeOf(activeInstance) : option;
  const activeRotation = mode === "move" ? rotation : activeInstance?.placement.rotation_deg ?? rotation;
  const kind = activeOption ? mountKind(activeOption) : "grid";
  const locked = busy || pending;
  const local = moving !== null || pending;
  useEffect(() => { onLocalDraft(local); return () => onLocalDraft(false); }, [local, onLocalDraft]);
  useEffect(() => { setCamera(fit([...(below?.regions ?? []), ...(deck?.regions ?? [])], WIDTH, HEIGHT)); setCursor(null); }, [deck?.id, session.session_id]);
  useEffect(() => { if (mode !== "move") setRotation(defaultRotation(option)); }, [option?.sha256]);
  useEffect(() => { if (selected && !instance) { onSelect(""); setMode("select"); } }, [session.revision]);
  function point(e: { clientX: number; clientY: number }): Point {
    const rect = svg.current!.getBoundingClientRect();
    return { x: (e.clientX - rect.left) * WIDTH / rect.width, y: (e.clientY - rect.top) * HEIGHT / rect.height };
  }
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => { e.preventDefault(); setCamera(c => zoom(c, point(e), Math.exp(-Math.max(-100, Math.min(100, e.deltaY)) * .002))); };
    el.addEventListener("wheel", wheel, { passive: false }); return () => el.removeEventListener("wheel", wheel);
  }, []);
  const errors = new Map((layout?.errors ?? []).map(e => [e.instance_id, e.message]));
  const conflicts = new Set((layout?.conflicts ?? []).flatMap(c => c.instance_ids));
  const views = layout?.modules ?? [];
  const selectedView = views.find(v => v.id === selected);
  const arcVisible = arcVisibleAtLevel(selectedArc, selectedView, deck?.level);
  const children = instance ? hostedDescendants(modules, instance.id) : [];
  function anchorOf(m: OutfitInstance): Point | undefined {
    const view = views.find(v => v.id === m.id);
    if (view) return { x: view.anchor_m[0], y: view.anchor_m[1] };
    if (m.placement.anchor_half_cell) return { x: m.placement.anchor_half_cell[0] * 2.5, y: m.placement.anchor_half_cell[1] * 2.5 };
    if (m.placement.kind === "side") {
      const slots = layout?.decks.find(d => d.id === m.placement.deck_id)?.side_mount_slots ?? [];
      const slot = slots.find(s => s.region_id === m.placement.region_id && s.edge_index === m.placement.edge_index && s.slot_index === m.placement.start_slot_index);
      const o = prototypeOf(m);
      if (slot && o) return sidePreview(slots, slot, o, m.placement.rotation_deg ?? 0)?.anchor;
    }
    return undefined;
  }
  useEffect(() => {
    if (!instance) return;
    const v = views.find(v => v.id === instance.id);
    const targetDeck = decks.find(d => d.level === v?.base_deck_level) ?? decks.find(d => d.id === instance.placement.deck_id);
    if (targetDeck && !(v && deck && visibleAtLevel(v,deck.level))) setDeckId(targetDeck.id);
    const a = anchorOf(instance);
    if (a) setCamera(c => { const p = screen(a,c); return p.x < 30 || p.x > WIDTH-30 || p.y < 30 || p.y > HEIGHT-30 ? {...c,x:WIDTH/2-a.x*c.scale,y:HEIGHT/2+a.y*c.scale} : c; });
  }, [selected]);
  function pick(p: Point, exclude = "", hostsOnly = false) {
    const hosts = activeOption ? compatibleHosts(activeOption, modules, options, exclude).map(m => m.id) : [];
    const ordered = [...modules].reverse();
    if (!hostsOnly && selected) ordered.sort((a,b) => Number(b.id === selected) - Number(a.id === selected));
    for (const m of ordered) {
      if (m.id === exclude || hostsOnly && !hosts.includes(m.id)) continue;
      const view = views.find(v => v.id === m.id), anchor = anchorOf(m);
      if (!anchor || !deck || !(view ? visibleAtLevel(view,deck.level) : decks.find(d => d.id === m.placement.deck_id)?.level === deck.level)) continue;
      const a = screen(anchor, camera);
      if (Math.hypot(a.x - p.x, a.y - p.y) < 11) return m.id;
      if (view && [...view.internal_cells, ...view.top_cells].some(([level, x, y]) => level === deck?.level && Math.abs(world(p, camera).x - x * 5) <= 2.5 && Math.abs(world(p, camera).y - y * 5) <= 2.5)) return m.id;
      if (view?.body_spatial_keys.some(([level, x, y]) => level === deck?.level && Math.hypot(world(p, camera).x - x, world(p, camera).y - y) < 2.5)) return m.id;
    }
    return "";
  }
  async function send(command: string, args: Record<string, unknown>, selectAfter?: string) {
    if (locked || pendingRef.current) return;
    pendingRef.current = true; setPending(true); setMessage("");
    try { if (await onCommand(command, args)) { if (selectAfter !== undefined) onSelect(selectAfter); setMode("select"); }
      else setMessage("操作未通过检查，当前布局未改变；请按错误说明调整位置或朝向。"); }
    finally { pendingRef.current = false; setPending(false); setMoving(null); }
  }
  function submit(p: Point, target?: OutfitInstance, preserveBase = false) {
    const o = target ? prototypeOf(target) : option;
    if (!o || !deck || locked) return;
    const k = mountKind(o), r = target && mode === "move" ? rotation : target?.placement.rotation_deg ?? rotation;
    const id = target?.id ?? nextId(o.prototype.category, modules.map(m => m.id));
    const args: Record<string, unknown> = { instance_id: id };
    if (!target) args.prototype = { id: o.prototype.id, version: o.prototype.version };
    if (k === "grid") {
      const a = placementPoint(world(p, camera), o, r);
      Object.assign(args, { deck_id: preserveBase && target ? target.placement.deck_id : deck.id, anchor_half_cell: [a.x / 2.5, a.y / 2.5], rotation_deg: r });
    } else if (k === "side") {
      const slot = nearestSlot(space?.side_mount_slots ?? [], world(p, camera), 14 / camera.scale);
      if (!slot) { setMessage("请点击紫色船边槽位，侧挂模块只能安装在槽位上。"); setMoving(null); return; }
      Object.assign(args, { deck_id: deck.id, region_id: slot.region_id, edge_index: slot.edge_index, start_slot_index: slot.slot_index, rotation_deg: r });
    } else {
      const host = pick(p, id, true);
      if (!host) { setMessage("请点击提供空闲嵌入槽的宿主模块。"); setMoving(null); return; }
      args.host_instance_id = host;
    }
    void send(target && k === "hosted" ? "outfit.rehost" : `outfit.${target ? "move" : "place"}_${k}`, args, id);
  }
  function rotateSelected() {
    if (!instance) return;
    const o = prototypeOf(instance), allowed = o?.prototype.installation.allowed_rotations_deg ?? [0];
    const r = allowed[(allowed.indexOf(instance.placement.rotation_deg ?? 0) + 1) % allowed.length];
    if (instance.placement.kind === "grid") void send("outfit.rotate_grid", { instance_id: instance.id, rotation_deg: r });
    else if (instance.placement.kind === "side") {
      const { kind: _, ...placement } = instance.placement;
      void send("outfit.move_side", { instance_id: instance.id, ...placement, rotation_deg: r });
    }
  }
  const ghost = cursor && activeOption && (mode !== "select" || moving) && kind === "grid" ? placementPoint(cursor, activeOption, activeRotation) : null;
  const ghostCells = ghost && activeOption ? footprint(activeOption, ghost, activeRotation) : [];
  const hint = ghost && activeOption && layout ? gridHint(layout, moving && activeInstance?.placement.deck_id || deck?.id || "", activeOption, ghost, activeRotation, activeInstance?.id) : "";
  const slotHover = cursor && kind === "side" && (mode !== "select" || moving) ? nearestSlot(space?.side_mount_slots ?? [], cursor, 14 / camera.scale) : undefined;
  const sideGhost = slotHover && activeOption ? sidePreview(space?.side_mount_slots ?? [], slotHover, activeOption, activeRotation) : undefined;
  const lo = world({ x: 0, y: HEIGHT }, camera), hi = world({ x: WIDTH, y: 0 }, camera);
  const poly = (points: number[][]) => points.map(([x, y]) => { const p = screen({ x, y }, camera); return `${p.x},${p.y}`; }).join(" ");
  function cell(x: number, y: number, color: string, key: string, opacity = .5, dashed = false) {
    const p = screen({ x: x - 2.5, y: y + 2.5 }, camera);
    if (p.x < -5 * camera.scale || p.x > WIDTH || p.y < -5 * camera.scale || p.y > HEIGHT) return null;
    return <rect key={key} x={p.x} y={p.y} width={5 * camera.scale} height={5 * camera.scale} fill={color} fillOpacity={opacity} stroke={color} strokeWidth={1.2} strokeDasharray={dashed ? "4 3" : undefined} />;
  }
  function geometry(v: LayoutModule) {
    const bad = errors.has(v.id) || conflicts.has(v.id), active = v.id === selected;
    const tint = (normal: string) => bad ? "#ff7777" : active ? "#eeffbc" : normal;
    return <g key={v.id} opacity={moving === v.id ? .3 : 1}>
      {layers.internal && v.internal_cells.filter(c => c[0] === deck?.level).map(([,x,y], i) => cell(x*5,y*5,tint("#69c8a9"),`i${i}`))}
      {layers.top && v.top_cells.filter(c => c[0] === deck?.level).map(([,x,y], i) => cell(x*5,y*5,tint("#e9c66c"),`t${i}`, .35))}
      {(layers.body || layers.side && v.placement_kind === "side") && v.body_spatial_keys.filter(c => c[0] === deck?.level).map(([,x,y],i) => cell(x,y,tint("#ac9ce8"),`b${i}`, .25))}
      {layers.clearance && v.clearance_spatial_keys.filter(c => c[0] === deck?.level || v.base_deck_level === deck?.level && c[0] === deck.level+1).map(([band,x,y],i) => <g key={`c${i}`}><title>净空高度带 {band}</title>{cell(x,y,tint("#ee946c"),"c", .08,true)}</g>)}
    </g>;
  }
  return <div className="hull-workspace outfit-workspace">
    {operationError && <p className="editor-error" role="alert">{operationError}</p>}
    <div className="viewport-toolbar">
      <label>甲板 <select aria-label="舾装画布甲板" value={deck?.id ?? ""} disabled={locked || moving !== null} onChange={e => { setDeckId(e.target.value); setCursor(null); }}>
        {decks.map(d => <option key={d.id} value={d.id}>第 {d.level} 层{d.is_base ? " · 基底" : ""} · {d.id}</option>)}
      </select></label>
      <button disabled={locked} aria-pressed={mode === "select"} onClick={() => { setMode("select"); setMessage(""); }}>选择 / 拖动</button>
      <button disabled={locked || !option || !layout} aria-pressed={mode === "place"} onClick={() => { if (mode === "move") setRotation(defaultRotation(option)); setMode("place"); setMessage(""); }}>放置所选原型</button>
      <label>{mode === "move" ? "移动后朝向" : "放置朝向"} <select aria-label="画布放置朝向" value={rotation} disabled={locked} onChange={e => setRotation(Number(e.target.value))}>
        {(activeOption?.prototype.installation.allowed_rotations_deg ?? [0]).map(r => <option key={r} value={r}>{r}°</option>)}
      </select></label>
      <button onClick={() => setCamera(fit(deck?.regions ?? [],WIDTH,HEIGHT))}>适应船壳</button>
      <button aria-label="放大舾装画布" onClick={() => setCamera(c=>zoom(c,{x:WIDTH/2,y:HEIGHT/2},1.25))}>＋</button>
      <button aria-label="缩小舾装画布" onClick={() => setCamera(c=>zoom(c,{x:WIDTH/2,y:HEIGHT/2},.8))}>−</button>
    </div>
    <div className="outfit-canvas-columns"><div>
      <svg ref={svg} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none" className="outfit-canvas" role="region" aria-label="舾装二维画布" tabIndex={0}
        onContextMenu={e=>e.preventDefault()} onKeyDown={e=> {
          if (e.key === "Escape") { drag.current=null; setMoving(null); setMode("select"); setCursor(null); setMessage(""); }
          if (!locked && e.key.toLowerCase() === "r") { e.preventDefault(); if (mode === "select") rotateSelected(); else { const a=activeOption?.prototype.installation.allowed_rotations_deg ?? [0]; setRotation(a[(a.indexOf(rotation)+1)%a.length]); } }
          if (!locked && mode === "select" && e.key === "Delete" && selected) { e.preventDefault(); void send("outfit.remove",{instance_id:selected},""); }
        }} onPointerDown={e=> {
          if (locked || !layout || e.button !== 0 && e.button !== 1) return;
          e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId);
          const p=point(e), target=e.button===0 && mode==="select" ? pick(p) : "";
          const m=modules.find(m=>m.id===target);
          drag.current={point:p,camera,target,pan:e.button===1 || mode==="select" && !target,moved:false,anchor:m && anchorOf(m)};
          if (target) onSelect(target);
        }} onPointerMove={e=> {
          const p=point(e), start=drag.current;
          if (start && !locked) {
            const dx=p.x-start.point.x, dy=p.y-start.point.y;
            if (Math.hypot(dx,dy)>4) start.moved=true;
            if (start.moved && start.pan) setCamera({...start.camera,x:start.camera.x+dx,y:start.camera.y+dy});
            else if (start.moved && start.target) {
              setMoving(start.target);
              const m=modules.find(m=>m.id===start.target);
              if (m?.placement.kind==="grid" && start.anchor) { setCursor({x:start.anchor.x+dx/start.camera.scale,y:start.anchor.y-dy/start.camera.scale}); return; }
            }
          }
          setCursor(world(p,camera));
        }} onPointerUp={e=> {
          const start=drag.current; drag.current=null;
          if (start && !locked) {
            if (start.moved && start.target) {
              const m=modules.find(m=>m.id===start.target);
              const p=point(e);
              if(m?.placement.kind==="grid" && start.anchor) submit(screen({x:start.anchor.x+(p.x-start.point.x)/start.camera.scale,y:start.anchor.y-(p.y-start.point.y)/start.camera.scale},camera),m,true);
              else submit(p,m);
            } else if (!start.pan && !start.moved && mode!=="select") submit(point(e),mode==="move" ? instance : undefined);
            else if(!start.moved && start.pan && e.button===0) onSelect("");
          }
          setMoving(null);
          if(e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        }} onPointerCancel={()=> {drag.current=null;setMoving(null);setCursor(null);}}
        onLostPointerCapture={()=> {if(drag.current){drag.current=null;setMoving(null);setCursor(null);}}}
        onPointerLeave={()=> {if(!drag.current)setCursor(null);}}>
        <rect width={WIDTH} height={HEIGHT} fill="#081b20" />
        {gridLines(lo.x,hi.x,camera.scale).map(l=><line key={`x${l.value}`} x1={screen({x:l.value,y:0},camera).x} x2={screen({x:l.value,y:0},camera).x} y1={0} y2={HEIGHT} stroke={l.boundary?"#294850":"#152c32"} />)}
        {gridLines(lo.y,hi.y,camera.scale).map(l=><line key={`y${l.value}`} x1={0} x2={WIDTH} y1={screen({x:0,y:l.value},camera).y} y2={screen({x:0,y:l.value},camera).y} stroke={l.boundary?"#294850":"#152c32"} />)}
        {below?.regions.map(r=><polygon key={`lower${r.id}`} points={poly(r.vertices_m)} fill="#88acd9" fillOpacity={.12} stroke="#88acd9" strokeOpacity={.45} />)}
        {deck?.regions.map(r=><polygon key={r.id} points={poly(r.vertices_m)} fill="#397b72" fillOpacity={.13} stroke="#8fcab7" strokeWidth={2} />)}
        {filling?.configuration.id !== 'gtw.filling.none' && filling?.pieces?.map((p,i)=><polygon key={`filling${i}`} points={poly(p.vertices_m)} fill="#dfa552" fillOpacity={.25} pointerEvents="none"><title>本层边缘填充空间</title></polygon>)}
        {showArc && arcVisible && selectedArc?.status === "hull_occlusion_resolved" && selectedArc.base_deck_level !== null && decks.filter(d=>d.level > selectedArc.base_deck_level!).flatMap(d=>d.regions.map(r=><polygon key={`obstruction${d.id}${r.id}`} points={poly(r.vertices_m)} fill="#ff6868" fillOpacity={.1} stroke="#ff8888" strokeOpacity={.8} strokeDasharray="5 4" pointerEvents="none"><title>上层船壳投影：第 {d.level} 层 · {r.id}</title></polygon>))}
        {space?.internal_cells.map(([x,y],i)=>cell(x*5,y*5,"#76d9ac",`space${i}`,.05))}
        {layers.top && space?.exposed_top_cells.map(([x,y],i)=>{const p=screen({x:x*5,y:y*5},camera);return <circle key={`top${i}`} cx={p.x} cy={p.y} r={1.8} fill="#d9bc71" />;})}
        {layers.side && space?.side_mount_slots.map((s,i)=><line key={`slot${i}`} x1={screen({x:s.start_m[0],y:s.start_m[1]},camera).x} y1={screen({x:s.start_m[0],y:s.start_m[1]},camera).y} x2={screen({x:s.end_m[0],y:s.end_m[1]},camera).x} y2={screen({x:s.end_m[0],y:s.end_m[1]},camera).y} stroke={s===slotHover?"#fff4b0":"#bc91d1"} strokeWidth={s===slotHover?7:3} strokeDasharray="8 2" />)}
        {views.map(geometry)}
        <WeaponArcOverlay arc={selectedArc} module={selectedView} level={deck?.level} camera={camera} show={showArc} />
        {modules.filter(m=>!views.some(v=>v.id===m.id) && m.placement.deck_id===deck?.id).map(m=> {
          const a=anchorOf(m), o=prototypeOf(m);
          return a && o ? <g key={`invalid${m.id}`}>{footprint(o,a,m.placement.rotation_deg ?? 0).map((p,i)=>cell(p.x,p.y,"#ff7777",String(i),.2,true))}</g> : null;
        })}
        {modules.map(m=> {
          const v=views.find(v=>v.id===m.id), a=anchorOf(m);
          if(!a || !deck || !(v ? visibleAtLevel(v,deck.level) : decks.find(d=>d.id===m.placement.deck_id)?.level===deck.level))return null;
          const p=screen(a,camera), bad=errors.has(m.id)||conflicts.has(m.id), hosted=m.placement.kind==="hosted";
          return <g key={`label${m.id}`}><title>{prototypeOf(m)?.prototype.name} · {m.id}{bad?" · 安装冲突":""}</title>
            {m.placement.kind==="grid" && <line x1={p.x} y1={p.y} x2={p.x+Math.sin((m.placement.rotation_deg ?? 0)*Math.PI/180)*17} y2={p.y-Math.cos((m.placement.rotation_deg ?? 0)*Math.PI/180)*17} stroke={bad?"#ffaaaa":"#eefacc"} strokeWidth={2.5}/>}
            <circle cx={p.x} cy={p.y} r={m.id===selected?8:5} fill={bad?"#ff6666":hosted?"#ccabff":"#b0ead5"} stroke={m.id===selected?"#ffffff":"#153835"} strokeWidth={2}/>
            <text x={p.x+10} y={p.y+(hosted?17:-8)} fill={bad?"#ffaaaa":"#edf4d8"} fontSize={13} paintOrder="stroke" stroke="#081b20" strokeWidth={3}>{m.id}</text>
          </g>;
        })}
        {ghostCells.map((p,i)=>cell(p.x,p.y,hint?"#ff7777":"#fce399",`ghost${i}`,.25,true))}
        {sideGhost?.body.map((p,i)=>cell(p.x,p.y,"#fce399",`sideGhost${i}`,.25,true))}
        {sideGhost?.clearance.map((p,i)=><g key={`sideClear${i}`} pointerEvents="none"><title>待放置侧挂净空 / 尾焰</title>{cell(p.x,p.y,"#ee946c","cell",.15,true)}<line x1={screen(sideGhost.anchor,camera).x} y1={screen(sideGhost.anchor,camera).y} x2={screen(p,camera).x} y2={screen(p,camera).y} stroke="#ee946c" strokeDasharray="4 3"/></g>)}
        {ghost && <g stroke="#fff1bd"><line x1={screen(ghost,camera).x-8} x2={screen(ghost,camera).x+8} y1={screen(ghost,camera).y} y2={screen(ghost,camera).y}/><line x1={screen(ghost,camera).x} x2={screen(ghost,camera).x} y1={screen(ghost,camera).y-8} y2={screen(ghost,camera).y+8}/></g>}
        <text x={12} y={22} fill="#94bab2" fontSize={13}>舰艏 ↑ · 原点为 CIC 格心</text>
      </svg>
      <FillingSummary value={filling} />
      <div className="viewport-status"><span>{mode==="place"?`放置：${option?.prototype.name}`:mode==="move"?`移动：${selected}`:"选择或拖动已有模块"}</span><span>{ghost?`X ${ghost.x} · Y ${ghost.y} m`:"强制网格 · 安装格 5 m"}</span></div>
      <p className="viewport-help">点选原型后点“放置所选原型”，再点击画布。拖动模块移动；R 旋转，Delete 移除，Esc 取消。空白处或中键拖动平移，滚轮缩放。金色虚线为待提交外形，提交后由后台检查。</p>
      {below && <p className="viewport-help lower-deck-legend">半透明下层参考：{below.id}</p>}
      {!layout && <p role="alert" className="editor-error">请重启后台并重新打开设计，以加载舾装画布数据。</p>}
      {message && <p role="status" className="editor-error">{message}</p>}
      {ghost && <p className={hint?"editor-error":"viewport-help"}>{hint ? `放置提示：${hint}` : "外形预览：提交后检查完整净空和整舰条件。"}</p>}
      {kind === "side" && (mode !== "select" || moving) && <p className="viewport-help">侧挂朝向相对于船边；橙色虚线显示净空 / 尾焰，应位于船壳外。点击船边槽放置，后台检查实际遮挡和占用。{slotHover && !sideGhost ? "此处连续槽位不足。" : ""}</p>}
    </div><aside className="hull-properties">
      <h3>画布图层</h3>{(Object.keys(layers) as (keyof typeof layers)[]).map(k=><label className="outfit-layer" key={k}><input type="checkbox" checked={layers[k]} onChange={e=>setLayers({...layers,[k]:e.target.checked})}/>{{internal:"内部占用",top:"顶挂 / 露天格",side:"侧挂 / 船边槽",body:"本体空间",clearance:"净空 / 尾焰"}[k]}</label>)}
      <h3>已选择</h3><select aria-label="画布模块选择" className="outfit-module-select" value={selected} disabled={locked} onChange={e=>{onSelect(e.target.value);setMode("select");}}><option value="">请选择模块</option>{modules.map(m=><option key={m.id} value={m.id}>{m.id} · {prototypeOf(m)?.prototype.name}</option>)}</select><p>{instance ? `${prototypeOf(instance)?.prototype.name ?? instance.id} · ${instance.id}`:"点击画布上的模块"}</p>
      {instance && <div className="region-list"><button disabled={locked} onClick={()=>{setMode("move");setRotation(instance.placement.rotation_deg ?? defaultRotation(prototypeOf(instance)));setMessage("");}}>点选新位置 / 宿主</button><button disabled={locked || instance.placement.kind==="hosted"} onClick={rotateSelected}>旋转所选模块</button><button disabled={locked} onClick={()=>void send("outfit.remove",{instance_id:instance.id},"")}>{children.length ? `移除模块及 ${children.length} 个嵌入模块` : "移除所选模块"}</button></div>}
      {children.length > 0 && <p>随宿主一并移除：{children.map(m=>m.id).join("、")}。撤销可一起恢复。</p>}
      {instance?.placement.kind === "side" && <p>当前侧挂朝向：{instance.placement.rotation_deg ?? 0}°。可点“点选新位置 / 宿主”，调整“移动后朝向”再选槽位。</p>}
      {instance?.placement.kind === "side" && instance.placement.rotation_deg !== defaultRotation(prototypeOf(instance)) && <button disabled={locked} onClick={()=>{
        const { kind: _, ...placement } = instance.placement;
        void send("outfit.move_side", { instance_id: instance.id, ...placement, rotation_deg: defaultRotation(prototypeOf(instance)) });
      }}>使用推荐侧挂朝向</button>}
      {instance?.placement.kind === "hosted" && <p>宿主：{instance.placement.host_instance_id}{!modules.some(m=>m.id===instance.placement.host_instance_id) ? "（已不存在）。请选择新宿主，或移除此旧模块。" : ""}</p>}
      {kind==="hosted" && activeOption && <><h3>可用宿主</h3>{compatibleHosts(activeOption,modules,options,activeInstance?.id).map(m=><button key={m.id} disabled={locked} onClick={()=>{const a=anchorOf(m);if(a)submit(screen(a,camera),activeInstance);}}>{m.id}</button>)}<p>点击画布中提供空闲槽位的宿主；嵌入模块与宿主共用位置。</p></>}
      {selectedArc && <><h3>武器射界</h3><label className="outfit-layer"><input type="checkbox" checked={showArc} onChange={e=>setShowArc(e.target.checked)}/>显示水平射界</label><p>{arcText(selectedArc)}</p>
        {selectedArc.status === "requires_higher_deck_hull_raycast" && <p role="status" className="editor-error">当前后台尚未加载遮挡计算，因此无法显示射界圈。请在页面底部点“显式重启”，再恢复当前草稿。</p>}
        {showArc && selectedArc.origin_m && selectedArc.base_deck_level !== null && <p>{arcVisible ? `所选武器安装于第 ${selectedArc.base_deck_level} 层，射界按该层计算。` : `所选武器在本层不可见；请切回第 ${selectedArc.base_deck_level} 层查看射界。`}</p>}
        <p>红色虚线轮廓为上层船壳投影；红色扇区禁射。扇区不表示射程。</p></>}
      <h3>布局检查</h3><p>{layout?.conflicts.length ?? 0} 处占用 / 净空冲突 · {layout?.errors.length ?? 0} 个安装错误</p>
      {layout?.errors.map(e=><button className="diagnostic-item" key={e.instance_id} onClick={()=>onSelect(e.instance_id)}>{e.instance_id}：{e.message}</button>)}
      {layout?.conflicts.map((c,i)=><button key={i} className="diagnostic-item" onClick={()=>onSelect(c.instance_ids[0])}>{c.instance_ids.join(" / ")}：{{internal:"内部重叠",top:"顶挂重叠",side:"侧挂槽重叠",body:"本体重叠",clearance:"净空冲突"}[c.layer] ?? c.layer}</button>)}
      <p>红色：冲突模块。绿色：内部；金色：顶挂；紫色：侧挂；橙色虚线：净空。整体设计仍须通过下方升力等检查。</p>
    </aside></div>
  </div>;
}
