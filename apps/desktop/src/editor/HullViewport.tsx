import { Application, Graphics } from "../rendering/pixi";
import { useEffect, useRef, useState } from "react";
import { HullInspector } from "./HullInspector";
import { currentEdgeSpace } from "./edgeSpace";
import { FillingSummary } from "./FillingSummary";
import type { FillingView } from "./FillingSummary";
import { closedRegion, deferredCommit, nextId } from "./interaction";
import { drawingPoint, sameBoundary, symmetricDrawing, symmetrizeRegion } from "./symmetry";
import type { SourceSide } from "./symmetry";
import type { HullCommand, HullRegion, MaterialOption, SessionSnapshot } from "./model";
import { fit, gridLines, lowerDeck, pick, screen, snap, world, zoom } from "./viewport";
import type { Camera, Point, Selection } from "./viewport";

export function HullViewport({ session, busy, materials, onCommand, onLocalDraft, active = true }: {
  session: SessionSnapshot; busy: boolean; materials: MaterialOption[]; onCommand: HullCommand; onLocalDraft: (busy: boolean) => void; active?: boolean;
}) {
  const [deckId, setDeckId] = useState((session.draft.decks ?? [])[0]?.id ?? "");
  const deck = (session.draft.decks ?? []).find(d => d.id === deckId) ?? (session.draft.decks ?? [])[0];
  const below = lowerDeck((session.draft.decks ?? []), deck);
  const visibleRegions = [...(below?.regions ?? []), ...(deck?.regions ?? [])];
  const [drawing, setDrawing] = useState(false);
  const [symmetric, setSymmetric] = useState(true);
  const [editMessage, setEditMessage] = useState("");
  const [symmetryPreview, setSymmetryPreview] = useState<HullRegion | null>(null);
  const [drawPoints, setDrawPoints] = useState<Point[]>([]);
  const [moving, setMoving] = useState<{ region: string; vertex: number; point: Point } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showSpace, setShowSpace] = useState(true);
  const [showEdgeSpace, setShowEdgeSpace] = useState(true);
  const deferred = useRef(deferredCommit());
  const alive = useRef(true);
  const structureMaterial = materials.find(m => m.category === "structure");
  const armorMaterial = materials.find(m => m.category === "base_armor");
  const localDraft = drawing || moving !== null || submitting || symmetryPreview !== null;
  const edgeSpace = currentEdgeSpace(session.preview, deck?.id, localDraft);
  const locked = busy || submitting;
  useEffect(() => { onLocalDraft(localDraft); }, [localDraft, onLocalDraft]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; deferred.current.cancel(); onLocalDraft(false); }; }, [onLocalDraft]);
  function cancelLocal() {
    if (busy) return;
    deferred.current.cancel(); setMoving(null); setSubmitting(false); setDrawing(false); setDrawPoints([]); setSymmetryPreview(null); setEditMessage(""); drag.current = null;
  }
  function appendPoint(p: Point) {
    if (locked || drawPoints.length >= (symmetric ? 2049 : 4096)) return;
    if (drawPoints.at(-1)?.x === p.x && drawPoints.at(-1)?.y === p.y) return;
    try {
      const candidate = drawingPoint(drawPoints, p, symmetric);
      setDrawPoints(previous => [...previous, candidate]); setEditMessage("");
    } catch (error) { setEditMessage(String((error as Error).message)); }
  }
  async function closeDrawing() {
    if (!deck || !armorMaterial || locked || drawPoints.length < 3) return;
    const id = nextId(`${deck.id}.region`, deck.regions.map(r => r.id));
    const armor = { material: { id: armorMaterial.id, version: armorMaterial.version }, thickness_m: 0 };
    let region: HullRegion;
    try { region = symmetric ? symmetricDrawing(id, drawPoints, armor) : closedRegion(id, drawPoints, armor); }
    catch (error) { setEditMessage((error as Error).message); return; }
    setSubmitting(true); setEditMessage("");
    const ok = await onCommand("hull.add_region", { deck_id: deck.id, region });
    if (!alive.current) return;
    setSubmitting(false);
    if (ok) { setDrawPoints([]); setDrawing(false); select({ region: id, vertex: null }); }
  }
  function previewSymmetry(side: SourceSide) {
    if (!region || localDraft || locked) return;
    try {
      const candidate = symmetrizeRegion(region, side);
      if (sameBoundary(region, candidate)) { setEditMessage("当前轮廓和边装甲已对称，无需修改。"); return; }
      setSymmetryPreview(candidate);
      setEditMessage(`以${side === "left" ? "左侧" : "右侧"}为准：金色预览为替换后的轮廓和对应边装甲，应用后可撤销。`);
    } catch (error) { setEditMessage((error as Error).message); }
  }
  async function applySymmetry() {
    if (!deck || !symmetryPreview || locked) return;
    setSubmitting(true);
    const ok = await onCommand("hull.replace_region", { deck_id: deck.id, region: symmetryPreview });
    if (!alive.current) return;
    setSubmitting(false);
    if (ok) { setSymmetryPreview(null); setEditMessage("已应用对称轮廓和边装甲，可撤销。"); select({ region: symmetryPreview.id, vertex: null }); }
  }
  async function addDeck() {
    if (!structureMaterial || locked || localDraft) return;
    const levels = (session.draft.decks ?? []).map(d => d.level);
    let level = 0; while (levels.includes(level)) level++;
    const id = nextId("deck", (session.draft.decks ?? []).map(d => d.id));
    if (await onCommand("hull.add_deck", { deck_id: id, level, material: { id: structureMaterial.id, version: structureMaterial.version } })) {
      setDeckId(id); select(null);
    }
  }
  const [selected, select] = useState<Selection | null>(null);
  const [hover, setHover] = useState<Selection | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [camera, setCamera] = useState<Camera>({ x: 300, y: 220, scale: 4 });
  const [size, setSize] = useState({ width: 600, height: 440 });
  const [ready, setReady] = useState(false);
  const [failure, setFailure] = useState("");
  const container = useRef<HTMLDivElement>(null);
  const renderer = useRef<Application | null>(null);
  const graphics = useRef<Graphics | null>(null);
  const drag = useRef<{ point: Point; camera: Camera; moved: boolean; button: number; target: Selection | null } | null>(null);
  const requestedFocus = useRef<string | null>(null);
  const currentCamera = useRef(camera); currentCamera.current = camera;
  const region = deck?.regions.find(r => r.id === selected?.region);
  const vertex = selected?.vertex != null ? region?.vertices_m[selected.vertex] : undefined;

  useEffect(() => {
    let disposed = false;
    const app = new Application();
    const host = container.current!;
    let observer: ResizeObserver | undefined;
    let resize: (() => void) | undefined;
    void app.init({ preference: "webgl", width: 600, height: 440, background: 0x081b20,
      antialias: true, resolution: window.devicePixelRatio || 1, autoDensity: true, autoStart: false }).then(() => {
      if (disposed) { app.destroy(true, { children: true }); return; }
      renderer.current = app;
      const g = new Graphics(); graphics.current = g; app.stage.addChild(g);
      host.appendChild(app.canvas);
      resize = () => {
        const width = host.clientWidth, height = host.clientHeight;
        if (!width || !height) return;
        app.renderer.resolution = window.devicePixelRatio || 1;
        app.renderer.resize(width, height); setSize({ width, height });
        app.render();
      };
      observer = new ResizeObserver(resize);
      window.addEventListener("resize", resize);
      observer.observe(host); setReady(true);
    }).catch(error => { if (!disposed) setFailure(String(error)); });
    return () => {
      disposed = true; observer?.disconnect();
      if (resize) window.removeEventListener("resize", resize);
      if (renderer.current === app) { renderer.current = null; graphics.current = null; app.destroy(true, { children: true }); }
    };
  }, []);

  useEffect(() => {
    const focus = requestedFocus.current; requestedFocus.current = null;
    setCamera(fit(focus ? (deck?.regions.filter(r => r.id === focus) ?? []) : visibleRegions, size.width, size.height));
    setHover(null); setCursor(null);
    // Revision changes preserve the camera; opening/changing decks resets the view.
  }, [session.session_id, deck?.id, below?.id, size.width, size.height]);

  useEffect(() => {
    const host = container.current!;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = host.getBoundingClientRect();
      setCamera(c => zoom(c, { x: event.clientX - rect.left, y: event.clientY - rect.top }, Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.002)));
    };
    host.addEventListener("wheel", wheel, { passive: false });
    return () => host.removeEventListener("wheel", wheel);
  }, []);

  useEffect(() => {
    const g = graphics.current, app = renderer.current;
    if (!active || !g || !app) return;
    g.clear();
    const lo = world({ x: 0, y: size.height }, camera), hi = world({ x: size.width, y: 0 }, camera);
    for (const line of gridLines(lo.x, hi.x, camera.scale)) {
      const px = screen({ x: line.value, y: 0 }, camera).x;
      g.moveTo(px, 0).lineTo(px, size.height).stroke({ color: line.boundary ? 0x365b64 : 0x172e35, width: 1 });
    }
    for (const line of gridLines(lo.y, hi.y, camera.scale)) {
      const py = screen({ x: 0, y: line.value }, camera).y;
      g.moveTo(0, py).lineTo(size.width, py).stroke({ color: line.boundary ? 0x365b64 : 0x172e35, width: 1 });
    }
    g.moveTo(camera.x, 0).lineTo(camera.x, size.height).moveTo(0, camera.y).lineTo(size.width, camera.y)
      .stroke({ color: 0x527f89, width: 1 });
    if (drawing && symmetric) g.moveTo(camera.x, 0).lineTo(camera.x, size.height).stroke({ color: 0xffd58b, alpha: 0.65, width: 2 });
    // Reference geometry is drawn underneath and never passed to hover/pick.
    for (const r of below?.regions ?? []) {
      const points = r.vertices_m.map(([x, y]) => screen({ x, y }, camera));
      if (points.length < 2) continue;
      g.poly(points.flatMap(p => [p.x, p.y]), true);
      g.fill({ color: 0x88acd9, alpha: 0.12 });
      g.stroke({ color: 0xa4bddd, alpha: 0.5, width: 1.5 });
    }
    for (const r of deck?.regions ?? []) {
      const points = r.vertices_m.map(([x, y], i) => screen(moving?.region === r.id && moving.vertex === i ? moving.point : { x, y }, camera));
      if (points.length < 2) continue;
      const active = r.id === selected?.region;
      g.poly(points.flatMap(p => [p.x, p.y]), true);
      if (session.preview.valid) g.fill({ color: active ? 0x65ccb0 : 0x347c76, alpha: active ? 0.32 : 0.18 });
      g.stroke({ color: moving || !session.preview.valid ? 0xf7ab77 : active ? 0xa6ffe0 : r.id === hover?.region ? 0xe1d29a : 0x60a8a6, width: active ? 2.5 : 1.5 });
      points.forEach((p, i) => g.circle(p.x, p.y, active && selected?.vertex === i ? 6 : 3).fill(active && selected?.vertex === i ? 0xffd58b : 0x96c9c0));
    }
    if (showEdgeSpace && edgeSpace) {
      for (const piece of edgeSpace.pieces) {
        const points = piece.vertices_m.map(([x, y]) => screen({ x, y }, camera));
        g.poly(points.flatMap(p => [p.x, p.y]), true).fill({ color: 0xf4a261, alpha: 0.48 });
      }
    }
    if (showSpace && session.preview.valid && !localDraft) {
      const compiled = (session.preview.model.decks as DeckView[] | undefined)?.find(d => d.id === deck?.id)?.compiled_installation_space;
      for (const [x, y] of compiled?.internal_cells ?? []) {
        const p = screen({ x: x * 5 - 2.5, y: y * 5 + 2.5 }, camera);
        if (p.x + camera.scale * 5 < 0 || p.x > size.width || p.y + camera.scale * 5 < 0 || p.y > size.height) continue;
        g.rect(p.x, p.y, camera.scale * 5, camera.scale * 5).fill({ color: 0x66dca9, alpha: 0.08 }).stroke({ color: 0x66dca9, alpha: 0.3, width: 1 });
      }
      for (const [x, y] of compiled?.exposed_top_cells ?? []) {
        const p = screen({ x: x * 5, y: y * 5 }, camera);
        g.circle(p.x, p.y, 2).fill({ color: 0xdec57e, alpha: 0.65 });
      }
      for (const slot of compiled?.side_mount_slots ?? []) {
        const a = screen({ x: slot.start_m[0], y: slot.start_m[1] }, camera), b = screen({ x: slot.end_m[0], y: slot.end_m[1] }, camera);
        g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ color: 0xd59cda, alpha: 0.6, width: 3 });
      }
    }
    if (symmetryPreview) {
      const points = symmetryPreview.vertices_m.map(([x,y]) => screen({x,y}, camera));
      g.poly(points.flatMap(p => [p.x,p.y]), true).fill({color: 0xffd58b, alpha: 0.12}).stroke({color: 0xffd58b, width: 2.5});
    }
    if (drawing && drawPoints.length) {
      let candidate: Point | null = null;
      if (cursor) { try { candidate = drawingPoint(drawPoints, snap(cursor), symmetric); } catch { /* Invalid candidate is not drawn as an accepted point. */ } }
      const path = [...drawPoints, ...(candidate ? [candidate] : [])];
      const points = path.map(p => screen(p, camera));
      g.poly(points.flatMap(p => [p.x, p.y]), false).stroke({ color: 0xffd58b, width: 2 });
      if (symmetric) {
        const mirrored = path.map(p => screen({x: -p.x, y: p.y}, camera));
        g.poly(mirrored.flatMap(p => [p.x,p.y]), false).stroke({color: 0xb6a0ff, width: 2});
        mirrored.forEach(p => g.circle(p.x,p.y,3).fill(0xb6a0ff));
      }
      drawPoints.forEach(p => { const q = screen(p, camera); g.circle(q.x, q.y, 4).fill(0xffd58b); });
    }
    if (cursor) {
      const p = screen(drawing && symmetric && !drawPoints.length ? {x: 0, y: snap(cursor).y} : snap(cursor), camera);
      g.moveTo(p.x - 7, p.y).lineTo(p.x + 7, p.y).moveTo(p.x, p.y - 7).lineTo(p.x, p.y + 7).stroke({ color: 0xffd58b, width: 1.5 });
    }
    app.render();
  }, [active, camera, deck, below, selected, hover, cursor, size, ready, session.preview, showSpace, showEdgeSpace, edgeSpace, localDraft, drawing, drawPoints, moving, symmetric, symmetryPreview]);

  function locate(path: string) {
    if (localDraft || locked) return;
    const match = /decks\[(\d+)\](?:\.regions\[(\d+)\])?(?:\.vertices_m\[(\d+)\])?/.exec(path);
    const target = match ? (session.draft.decks ?? [])[Number(match[1])] : undefined;
    if (!target) return;
    const r = target.regions[Number(match?.[2] ?? 0)];
    requestedFocus.current = target.id !== deck?.id ? r?.id ?? null : null;
    setDeckId(target.id);
    setCamera(fit(r ? [r] : target.regions, size.width, size.height));
    select(r ? { region: r.id, vertex: match?.[3] ? Number(match[3]) : null } : null);
  }
  function point(event: React.PointerEvent): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  const location = cursor && (drawing && symmetric && !drawPoints.length ? {x: 0, y: snap(cursor).y} : snap(cursor));
  return <div className="hull-workspace">
    <div className="viewport-toolbar">
      <label>当前甲板 <select aria-label="当前甲板" value={deck?.id ?? ""} disabled={localDraft || locked} onChange={e => { setDeckId(e.target.value); select(null); }}>
        {(session.draft.decks ?? []).map(d => <option key={d.id} value={d.id}>{d.id} · 第 {d.level} 层{d.is_base ? " · 基底" : ""}</option>)}
      </select></label>
      <button onClick={() => setCamera(fit(visibleRegions, size.width, size.height))}>适应船壳</button>
      <button aria-label="放大画布" onClick={() => setCamera(c => zoom(c, { x: size.width / 2, y: size.height / 2 }, 1.25))}>＋</button>
      <button aria-label="缩小画布" onClick={() => setCamera(c => zoom(c, { x: size.width / 2, y: size.height / 2 }, 0.8))}>−</button>
      <button disabled={locked || localDraft || !structureMaterial} onClick={() => void addDeck()}>添加甲板</button>
      <button className="secondary" disabled={locked || localDraft || !deck} onClick={() => void onCommand("hull.remove_deck", { deck_id: deck?.id })}>删除当前甲板</button>
      <label>绘制方式 <select aria-label="绘制方式" value={symmetric ? "symmetric" : "full"} disabled={localDraft || locked} onChange={e => { setSymmetric(e.target.value === "symmetric"); setEditMessage(""); }}><option value="symmetric">对称绘制（画一侧）</option><option value="full">完整轮廓（逐点绘制）</option></select></label>
      {!drawing ? <button disabled={locked || localDraft || !deck || !armorMaterial} onClick={() => { setDrawing(true); select(null); setEditMessage(""); }}>绘制区域</button>
        : <><button disabled={locked || drawPoints.length < 3 || (symmetric && drawPoints.at(-1)?.x !== 0)} onClick={() => void closeDrawing()}>{symmetric ? "生成对称船壳" : "闭合并提交区域"}</button>
        <button disabled={locked || !drawPoints.length} onClick={() => setDrawPoints(points => points.slice(0, -1))}>撤回绘图点</button></>}
      {symmetryPreview && <button disabled={locked} onClick={() => void applySymmetry()}>应用对称替换</button>}
      {localDraft && <button disabled={busy} onClick={cancelLocal}>取消本地编辑</button>}
      <label><input type="checkbox" checked={showSpace} onChange={e => setShowSpace(e.target.checked)} /> 安装空间</label>
      <label><input type="checkbox" checked={showEdgeSpace} onChange={e => setShowEdgeSpace(e.target.checked)} /> 边缘余量</label>
      <span>端点强制吸附 · 2.5 m</span>
      {below && <span className="lower-deck-legend">下层参考：{below.id} · 第 {below.level} 层（半透明）</span>}
      {deck && deck.level > 0 && !below && <span className="lower-deck-missing">缺少第 {deck.level - 1} 层，无法显示下层支撑参考</span>}
    </div>
    <div className="viewport-columns">
      <div><div ref={container} className="hull-canvas" role="region" aria-label="船壳二维画布" tabIndex={0}
        onContextMenu={e => e.preventDefault()}
        onKeyDown={e => {
          if (e.key === "Escape") { cancelLocal(); select(null); }
          if (drawing && e.key === "Enter") { e.preventDefault(); void closeDrawing(); }
          if (drawing && e.key === "Backspace" && !locked) { e.preventDefault(); setDrawPoints(points => points.slice(0,-1)); }
        }}
        onPointerDown={e => {
          if (e.button !== 0 && e.button !== 1 || locked || symmetryPreview) return;
          e.preventDefault(); e.currentTarget.focus();
          if (drawing && e.button === 0) {
            const p = snap(world(point(e), camera)), first = drawPoints[0];
            if (drawPoints.length >= 3 && first.x === p.x && first.y === p.y) void closeDrawing(); else appendPoint(p);
            return;
          }
          e.currentTarget.setPointerCapture(e.pointerId);
          const target = e.button === 0 ? pick(deck?.regions ?? [], point(e), camera) : null;
          drag.current = { point: point(e), camera, moved: false, button: e.button, target };
          if (target?.vertex != null) select(target);
        }}
        onPointerMove={e => {
          const p = point(e), start = drag.current;
          if (start && !locked) {
            const dx = p.x - start.point.x, dy = p.y - start.point.y;
            if (Math.hypot(dx, dy) > 4) start.moved = true;
            if (start.moved) {
              if (start.target?.vertex != null) setMoving({ region: start.target.region, vertex: start.target.vertex, point: snap(world(p, camera)) });
              else setCamera({ ...start.camera, x: start.camera.x + dx, y: start.camera.y + dy });
            }
          }
          setCursor(world(p, currentCamera.current)); setHover(pick(deck?.regions ?? [], p, currentCamera.current));
        }}
        onPointerUp={e => {
          const start = drag.current; drag.current = null;
          if (start && !locked) {
            if (start.moved && start.target?.vertex != null && deck) {
              const target = start.target, p = snap(world(point(e), camera));
              setMoving({ region: target.region, vertex: target.vertex!, point: p }); setSubmitting(true);
              deferred.current.schedule(() => { void onCommand("hull.move_vertex", { deck_id: deck.id, region_id: target.region, vertex_index: target.vertex, point_m: [p.x, p.y] })
                .finally(() => { if (alive.current) { setMoving(null); setSubmitting(false); } }); });
            } else if (!start.moved && start.button === 0) select(pick(deck?.regions ?? [], point(e), camera));
          }
          if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onPointerCancel={() => { drag.current = null; if (!submitting) setMoving(null); }}
        onLostPointerCapture={() => { if (drag.current) { drag.current = null; setMoving(null); } }}
        onPointerLeave={() => { if (!drag.current) { setCursor(null); setHover(null); } }} />
        <div className="viewport-status"><span>{location ? `X ${location.x.toFixed(1)} · Y ${location.y.toFixed(1)} m` : "移动指针查看坐标"}</span>
          <span>{camera.scale.toFixed(1)} px/m · 安装格 5 m · 绘图步长 2.5 m{camera.scale * 2.5 < 3 ? "（远景简化）" : ""}</span></div>
        {drawing && symmetric && <p className="editor-summary">对称绘制：首点自动落在金色中线 X=0；沿左侧或右侧绘制，再回到中线另一点。紫色为自动镜像，最后点击“生成对称船壳”。</p>}
        {editMessage && <p role="status" className="editor-summary">{editMessage}</p>}
        <p className="muted viewport-help">滚轮缩放 · 拖动端点修改 · 拖动空白处/中键平移 · 绘图时点击落点、Enter 闭合、Esc 取消</p>
        {localDraft && <p role="status" className="editor-summary">{drawing ? `本地绘图：${drawPoints.length} 个点，闭合后提交` : symmetryPreview ? "对称替换预览，应用后提交" : "本地拖动草稿，松开后检查并提交"}。尚未持久保存；安装空间待提交后更新。</p>}
        {!session.preview.valid && <p className="editor-error">正在显示当前非法草稿轮廓；权威派生结果暂不可用。</p>}
        {failure && <p role="alert" className="editor-error">画布初始化失败：{failure}</p>}
      </div>
      <aside className="hull-properties" aria-label="选择属性">
        <p className="muted">亮线：五米安装格边界；暗线：半格辅助线。原点为安装格中心。</p><h3>选择属性</h3><p>{deck?.id ?? "没有甲板"}{deck?.is_base ? " · 基底层" : ""}</p>
        <p>结构材料：{deck?.structure_material.id.split(".").at(-1)} · v{deck?.structure_material.version}</p>
        {region ? <><strong>{region.id}</strong><p>{region.vertices_m.length} 个端点</p>
          {vertex && <p>端点 {(selected?.vertex ?? 0) + 1}<br />X {vertex[0]} m · Y {vertex[1]} m</p>}</> : <p className="muted">在画布中选择区域或端点，也可使用下方列表。</p>}
        <div className="region-list">{deck?.regions.map(r => <button className="secondary" key={r.id} aria-pressed={selected?.region === r.id}
          disabled={localDraft || locked} onClick={() => select({ region: r.id, vertex: null })}>{r.id}</button>)}</div>
        <HullInspector deck={deck} region={region} selection={selected} materials={materials} busy={locked || moving !== null || symmetryPreview !== null}
          drawing={drawing} onCommand={onCommand} onPoint={appendPoint} onSymmetry={previewSymmetry} />
        <HullDerived session={session} deckId={deck?.id} localDraft={localDraft} />
        <section aria-label="边缘填充空间"><h3>边缘填充空间</h3>
          {edgeSpace ? <>
            <p>边缘面积 {edgeSpace.area_m2.toLocaleString(undefined, { maximumFractionDigits: 2 })} m²</p>
            <p>毛体积 {edgeSpace.gross_volume_m3.toLocaleString(undefined, { maximumFractionDigits: 2 })} m³</p>
            {edgeSpace.area_m2 === 0 && <p>本层没有安装整格之外的边缘余量。</p>}
            <p className="muted">橙色区域是完整安装格之外的船内余量。毛体积尚未扣除装甲、结构与储存设施，不能作为实际燃料容量。</p>
            <FillingSummary value={(session.preview.model.decks as {id: string; filling?: FillingView}[] | undefined)?.find(d => d.id === deck?.id)?.filling} />
            {!deck?.filling && <p className="muted">选择填充后显示净空间与新增质量；旧设计默认不额外填充。</p>}
          </> : <p className="muted">{localDraft || !session.preview.valid ? "当前草稿的边缘空间待合法提交后更新。" : "当前预览未提供边缘空间，请重新打开会话或更新后台。"}</p>}
        </section>
        <h3>检查结果</h3>
        {!session.preview.diagnostics.length && <p className="muted">{localDraft ? "本地草稿等待提交检查。" : "当前船壳通过合法性检查。"}</p>}
        {session.preview.diagnostics.map((d, i) => <button key={i} className="diagnostic-item" disabled={localDraft || locked} onClick={() => locate(d.path)}>
          {d.message}<small>{d.path}</small></button>)}
        <p className="muted">绿色格：内部空间 · 金色点：露天格 · 紫色线：侧挂槽。几何与支撑由后台校验。</p>
      </aside>
    </div>
  </div>;
}

interface DeckView { id: string; compiled_installation_space: { internal_cells: [number,number][]; exposed_top_cells: [number,number][]; side_mount_slots: { start_m: [number,number]; end_m: [number,number] }[] } }
function HullDerived({ session, deckId, localDraft }: { session: SessionSnapshot; deckId: string | undefined; localDraft: boolean }) {
  const preview = session.preview.valid ? session.preview : session.last_valid_preview;
  const model = preview?.model;
  const compiled = (model?.decks as DeckView[] | undefined)?.find(d => d.id === deckId)?.compiled_installation_space;
  const derived = model?.derived as { hull_mass_kg?: number; hull_inertia_kg_m2?: number; geometry?: { length_m: number; beam_m: number } } | undefined;
  return <section aria-label="船壳派生性能"><h3>安装空间与派生</h3>
    <p className="muted">{localDraft || !session.preview.valid ? `最近合法结果 · 修订 ${session.last_valid_revision ?? "无"}` : `当前权威结果 · 修订 ${session.revision}`}</p>
    {compiled ? <p>内部格 {compiled.internal_cells.length} · 露天格 {compiled.exposed_top_cells.length} · 侧挂槽 {compiled.side_mount_slots.length}</p> : <p>当前甲板暂无合法安装空间结果。</p>}
    {derived && <><p>船壳质量 {((derived.hull_mass_kg ?? 0) / 1000).toFixed(2)} t</p><p>长 {derived.geometry?.length_m} m · 宽 {derived.geometry?.beam_m} m</p>
      <p>惯量 {derived.hull_inertia_kg_m2?.toLocaleString()} kg·m²</p></>}
  </section>;
}
