import { Application, Container, Graphics } from "../rendering/pixi";
import { useEffect, useMemo, useRef, useState } from "react";
import { screen, world } from "../editor/viewport";
import type { Camera, Point } from "../editor/viewport";
import type { TacticalView } from "./model";
import { bodyToWorld, fitScene, moduleFootprints, pickModules, pickShip, shipPoints, zoomScene } from "./viewport";
import type { ShipGeometry } from "./viewport";
import type { GunInteraction } from "./gunnery";
import { PresentationTimeline } from './presentation';
import { HEIGHT_LAYERS, initialObservationLayer, isHeightLayer, layerName, LAYER_NAMES, viewOnLayer } from './layers';
import type { HeightLayer } from './layers';
import { distanceGrid, distanceLabel, gridLabels, scaleReference } from './distanceGrid';
import { TacticalAtmosphere } from './atmosphere';

type ShipObjects = { root: Container; selection: Graphics; modules: { id: string; max: number; graphic: Graphics }[] };
function buildShip(ship: ShipGeometry, light: boolean): ShipObjects {
  const root = new Container(), selection = new Graphics();
  const color = ship.side_id === "side.blue" ? (light ? 0x205c7e : 0x70c9f1) : (light ? 0x963f35 : 0xf18e80);
  const modules: ShipObjects["modules"] = [];
  const footprints = ship.modules.map(m => ({ module: m, cells: moduleFootprints(m) }));
  const levels = [...new Set([...ship.decks.map(d => d.level), ...footprints.flatMap(m => m.cells.map(c => c.level))])].sort((a, b) => a - b);
  for (const level of levels) {
    const hull = new Graphics(); root.addChild(hull);
    for (const region of ship.decks.filter(d => d.level === level).flatMap(d => d.regions)) {
      hull.poly(region.vertices_m.flat(), true).fill({ color, alpha: .22 }).stroke({ color, width: .65, alpha: .85 });
      selection.poly(region.vertices_m.flat(), true).stroke({ color: light ? 0x896114 : 0xffe6a2, width: 1.6 });
    }
    for (const { module, cells } of footprints) {
      const graphic = new Graphics();
      for (const p of cells.filter(p => p.level === level)) {
        const tint = p.kind === "top" ? (light ? 0x806326 : 0xead693) : p.kind === "body" ? (light ? 0x675082 : 0xb3a0db) : color;
        graphic.rect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size)
          .fill({ color: tint, alpha: p.kind === "internal" ? .32 : .58 }).stroke({ color: tint, width: .3, alpha: .8 });
      }
      // Embedded modules have no independent occupancy; mark their supplied anchor.
      if (!cells.length && module.deck_level === level) graphic.circle(module.anchor_m[0], module.anchor_m[1], 1).fill(0xffffff);
      root.addChild(graphic); modules.push({ id: module.id, max: module.max_durability, graphic });
    }
  }
  const bow = Math.max(0, ...shipPoints(ship).map(p => p.y)) + 8;
  root.addChild(new Graphics().moveTo(0, bow - 8).lineTo(0, bow).moveTo(-3, bow - 4).lineTo(0, bow).lineTo(3, bow - 4).stroke({ color, width: 1.4 }));
  root.addChild(selection); selection.visible = false;
  return { root, selection, modules };
}

export function TacticalViewport({ view, active, selected, onSelect, gunControl, compact = false }: {
  view: TacticalView; active: boolean; selected: string | null; onSelect: (id: string | null) => void;
  gunControl?: GunInteraction;
  compact?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const renderer = useRef<Application | null>(null);
  const scene = useRef<Container | null>(null);
  const atmosphere = useRef<TacticalAtmosphere | null>(null);
  const grid = useRef<Graphics | null>(null), vectors = useRef<Graphics | null>(null);
  const objects = useRef(new Map<string, ShipObjects>());
  const [ready, setReady] = useState(false), [failure, setFailure] = useState("");
  const [artFailure, setArtFailure] = useState(false);
  const [size, setSize] = useState({ width: 800, height: 540 });
  const [pickLevel, setPickLevel] = useState<string>("all");
  const [observationLayer, setObservationLayer] = useState<HeightLayer>(() => initialObservationLayer(view, gunControl?.ownShipId ?? selected));
  const [candidates, setCandidates] = useState<{ shipId: string; moduleId: string; name: string; level: number }[]>([]);
  const candidateMode = useRef<"weapon" | "target">("target");
  useEffect(() => { setCandidates([]); }, [gunControl?.weaponId, gunControl?.selectionKey, gunControl?.mode, gunControl?.attackLayer, observationLayer]);
  const [camera, setCamera] = useState<Camera>(() => fitScene(view, 800, 540));
  const fitted = useRef(false);
  const timeline = useRef(new PresentationTimeline());
  const displayed = useRef(view);
  const fullDisplay = useRef(view);
  const labels = useRef(new Map<string, HTMLSpanElement>());
  const draw = useRef<(now: number) => void>(() => {});
  const gridCache = useRef('');
  const latest = useRef({ view, camera, size, selected, gunControl, observationLayer });
  latest.current = { view, camera, size, selected, gunControl, observationLayer };
  const drag = useRef<{ pointer: number; start: Point; camera: Camera; moved: boolean; button: number } | null>(null);

  useEffect(() => {
    fitted.current = false; setCandidates([]); drag.current = null;
    const layer = initialObservationLayer(view, gunControl?.ownShipId ?? selected);
    setObservationLayer(layer); setCamera(fitScene(viewOnLayer(view, layer), size.width, size.height));
  }, [view.snapshot.backend_instance_id, view.snapshot.scene_id]);
  useEffect(() => { setCandidates([]); drag.current = null; latest.current.gunControl?.onLeave(); }, [observationLayer]);
  useEffect(() => {
    if (active) timeline.current.push(view, performance.now());
  }, [active, view]);

  useEffect(() => {
    if (!active) return;
    let disposed = false;
    const app = new Application(), element = host.current!;
    let observer: ResizeObserver | undefined;
    setFailure(""); setArtFailure(false);
    void app.init({ preference: "webgl", width: size.width, height: size.height, background: 0x081b20,
      antialias: true, resolution: window.devicePixelRatio || 1, autoDensity: true, autoStart: false }).then(() => {
      if (disposed) { app.destroy(true, { children: true, context: true }); return; }
      renderer.current = app;
      grid.current = new Graphics(); scene.current = new Container(); vectors.current = new Graphics();
      const weather = new TacticalAtmosphere(); atmosphere.current = weather; gridCache.current = '';
      app.stage.addChild(weather.background, grid.current, scene.current, weather.foreground, vectors.current);
      void weather.load().then(ok => {
        if (!disposed) { setArtFailure(!ok); if (!document.hidden) draw.current(performance.now()); }
      });
      element.appendChild(app.canvas);
      const resize = () => {
        const width = element.clientWidth, height = element.clientHeight;
        if (!width || !height) return;
        const oldWidth = app.screen.width, oldHeight = app.screen.height;
        app.renderer.resolution = window.devicePixelRatio || 1;
        app.renderer.resize(width, height); setSize({ width, height });
        const wasFitted = fitted.current;
        setCamera(c => wasFitted ? { ...c, x: c.x + (width - oldWidth) / 2, y: c.y + (height -oldHeight) / 2 } :
          fitScene(viewOnLayer(latest.current.view, latest.current.observationLayer), width, height));
        fitted.current = true;
      };
      observer = new ResizeObserver(resize); observer.observe(element);
      resize(); setReady(true);
    }).catch(error => { if (!disposed) setFailure(`画布初始化失败：${String(error)}`); });
    return () => {
      disposed = true; observer?.disconnect(); drag.current = null; setReady(false);
      timeline.current.clear();
      if (renderer.current === app) {
        atmosphere.current?.destroy(); atmosphere.current = null;
        renderer.current = null; scene.current = null; grid.current = null; vectors.current = null;
        objects.current.clear(); app.destroy(true, { children: true, context: true });
      }
    };
  }, [active]);

  useEffect(() => {
    if (!ready || !scene.current) return;
    for (const child of scene.current.removeChildren()) child.destroy({ children: true, context: true });
    objects.current.clear();
    for (const ship of view.geometry.ships) {
      const object = buildShip(ship, observationLayer === 'upper'); scene.current.addChild(object.root); objects.current.set(ship.id, object);
    }
  }, [ready, view.geometry, observationLayer]);

  draw.current = (now: number) => {
    const app = renderer.current, root = scene.current, g = grid.current, v = vectors.current;
    if (!active || !ready || !app || !root || !g || !v) return;
    const source = timeline.current.sample(now) ?? latest.current.view;
    const { camera, size, selected, gunControl, observationLayer } = latest.current;
    const view = viewOnLayer(source, observationLayer), light = observationLayer === 'upper';
    fullDisplay.current = source;
    displayed.current = view;
    atmosphere.current?.draw(observationLayer, camera, size.width, size.height, view.snapshot.time_s);
    root.position.set(camera.x, camera.y); root.scale.set(camera.scale, -camera.scale);
    const key = `${observationLayer}:${camera.x}:${camera.y}:${camera.scale}:${size.width}:${size.height}`;
    if (gridCache.current !== key) {
      gridCache.current = key; g.clear();
      for (const line of distanceGrid(camera, size.width, size.height)) {
        if (line.axis === 'x') g.moveTo(line.pixel, 0).lineTo(line.pixel, size.height);
        else g.moveTo(0, line.pixel).lineTo(size.width, line.pixel);
        g.stroke({ color: line.spacing === 5000 ? (light ? 0x574c30 : 0xf4d9a0) : (light ? 0x315862 : 0xa5c9d3),
          width: line.spacing === 5000 ? 1.8 : line.spacing === 500 ? 1.1 : .7, alpha: line.alpha });
      }
    }
    v.clear();
    for (const [id, object] of objects.current) {
      const pose = view.snapshot.ships.find(s => s.id === id);
      object.root.visible = !!pose;
      const label = labels.current.get(id);
      if (label) label.hidden = !pose;
      if (!pose) continue;
      object.root.position.set(pose.position_m[0], pose.position_m[1]); object.root.rotation = pose.heading_rad;
      if (label) {
        const at = screen({ x: pose.position_m[0], y: pose.position_m[1] }, camera);
        label.style.transform = `translate(${at.x+18}px, ${at.y}px)`;
      }
      object.selection.visible = id === selected;
      for (const m of object.modules) {
        const durability = pose.modules.find(p => p.id === m.id)?.durability;
        m.graphic.alpha = durability === undefined || m.max <= 0 ? 1 : .2 + .8 * Math.max(0, Math.min(1, durability / m.max));
      }
      const start = screen({ x: pose.position_m[0], y: pose.position_m[1] }, camera);
      const end = screen({ x: pose.position_m[0] + pose.velocity_mps[0] * 5, y: pose.position_m[1] + pose.velocity_mps[1] * 5 }, camera);
      if (Math.hypot(end.x - start.x, end.y - start.y) > 2) {
        const a = Math.atan2(end.y - start.y, end.x - start.x);
        v.moveTo(start.x, start.y).lineTo(end.x, end.y).lineTo(end.x - 7 * Math.cos(a - .4), end.y - 7 * Math.sin(a - .4))
          .moveTo(end.x, end.y).lineTo(end.x - 7 * Math.cos(a + .4), end.y - 7 * Math.sin(a + .4)).stroke({ color: light ? 0x25624a : 0xa3efb9, width: 1.5 });
      }
    }
    for (const gun of view.snapshot.gunnery?.weapons ?? []) {
      const origin = screen({ x: gun.origin_m[0], y: gun.origin_m[1] }, camera);
      const controlled = gun.ship_id === gunControl?.ownShipId && (gunControl.weaponIds ?? [gunControl.weaponId]).includes(gun.module_id);
      const length = Math.max(16, 15*camera.scale);
      const color = controlled ? (light ? 0x885919 : 0xffdd81) : (light ? 0x6c5740 : 0xeed0a2);
      if (view.snapshot.ships.some(s => s.id === gun.ship_id)) {
        v.circle(origin.x, origin.y, controlled ? 7 : 4).stroke({ color, width: 2 });
        v.moveTo(origin.x, origin.y).lineTo(origin.x+gun.direction[0]*length, origin.y-gun.direction[1]*length).stroke({ color, width: 3 });
      }
      if (controlled && gun.aim_point_m) {
        const aim = screen({ x: gun.aim_point_m[0], y: gun.aim_point_m[1] }, camera);
        const tint = gun.mode === "auto" && gun.quality === "degraded" ? (light ? 0x9c4c24 : 0xffa85c) : (light ? 0x26683d : 0x9dffbf);
        v.moveTo(origin.x, origin.y).lineTo(aim.x, aim.y).stroke({ color: tint, width: 1, alpha: .3 });
        v.circle(aim.x, aim.y, 8).moveTo(aim.x-12, aim.y).lineTo(aim.x+12, aim.y)
          .moveTo(aim.x, aim.y-12).lineTo(aim.x, aim.y+12).stroke({ color: tint, width: 1.5 });
      }
      if (controlled && gun.target_ship_id && gun.target_module_id) {
        const geometry = view.geometry.ships.find(s => s.id === gun.target_ship_id);
        const pose = view.snapshot.ships.find(s => s.id === gun.target_ship_id);
        const module = geometry?.modules.find(m => m.id === gun.target_module_id);
        if (pose && module) {
          const p = screen(bodyToWorld({ x: module.anchor_m[0], y: module.anchor_m[1] }, pose), camera);
          v.rect(p.x-9, p.y-9, 18, 18).stroke({ color: light ? 0x93372c : 0xff947d, width: 2 });
        }
      }
    }
    for (const p of view.snapshot.gunnery?.projectiles ?? []) {
      const at = screen({ x: p.position_m[0], y: p.position_m[1] }, camera);
      const before = screen({ x: p.previous_m[0], y: p.previous_m[1] }, camera);
      if (light) v.moveTo(before.x, before.y).lineTo(at.x, at.y).stroke({ color: 0x544525, width: 4, alpha: .8 });
      v.moveTo(before.x, before.y).lineTo(at.x, at.y).stroke({ color: 0xfff2b8, width: 2 });
      v.circle(at.x, at.y, 2).fill(0xffffff);
    }
    for (const hit of view.snapshot.gunnery?.damage?.recent ?? []) {
      const age = view.snapshot.fixed_step-hit.step;
      if (age < 0 || age > 45) continue;
      const at = screen({ x: hit.position_m[0], y: hit.position_m[1] }, camera);
      v.circle(at.x, at.y, 5+age*.15).stroke({ color: hit.outcome === "penetrated" || hit.outcome === "module" ? 0xff7040 : 0xaadfff, width: 2, alpha: 1-age/46 });
    }
    app.render();
    // Lightweight display diagnostics also let browser checks compare frame and
    // publication cadence without feeding display positions back into commands.
    if (host.current) {
      host.current.dataset.displayStep = String(view.snapshot.fixed_step);
      host.current.dataset.snapshotStep = String(latest.current.view.snapshot.fixed_step);
    }
  };

  useEffect(() => { if (!document.hidden) draw.current(performance.now()); }, [active, ready, view, selected, camera, size, gunControl?.weaponId, gunControl?.selectionKey, observationLayer]);
  useEffect(() => {
    if (!active || !ready) return;
    let frame = 0;
    const animateBattleFrame = (now: number) => { draw.current(now); frame = requestAnimationFrame(animateBattleFrame); };
    const visibility = () => {
      cancelAnimationFrame(frame);
      timeline.current.clear();
      if (!document.hidden) {
        timeline.current.push(latest.current.view, performance.now());
        draw.current(performance.now());
        if (!latest.current.view.snapshot.paused) frame = requestAnimationFrame(animateBattleFrame);
      }
    };
    if (!document.hidden && !view.snapshot.paused) frame = requestAnimationFrame(animateBattleFrame);
    document.addEventListener('visibilitychange', visibility);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('visibilitychange', visibility); };
  }, [active, ready, view.snapshot.paused, view.snapshot.scene_id, view.snapshot.backend_instance_id]);

  useEffect(() => {
    if (!active) return;
    const element = host.current!;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      setCamera(c => zoomScene(c, { x: event.clientX - rect.left, y: event.clientY - rect.top }, Math.exp(-Math.max(-120, Math.min(120, event.deltaY)) * .003)));
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [active]);
  const point = (e: { clientX: number; clientY: number }) => {
    const rect = host.current!.getBoundingClientRect(); return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };
  const fit = (id?: string) => {
    const source = fullDisplay.current, layer = source.snapshot.ships.find(s => s.id === id)?.height_layer;
    if (id && isHeightLayer(layer)) setObservationLayer(layer);
    setCamera(fitScene(id ? source : viewOnLayer(source, observationLayer), size.width, size.height, id));
  };
  const step = scaleReference(camera.scale);
  const lines = useMemo(() => distanceGrid(camera, size.width, size.height), [camera, size]);
  const references = useMemo(() => gridLabels(lines, camera.scale, size.width, size.height), [lines, camera.scale, size]);
  const selectedPose = view.snapshot.ships.find(s => s.id === selected);
  const ownLayer = view.snapshot.ships.find(s => s.id === gunControl?.ownShipId)?.height_layer;
  const visibleCount = view.snapshot.ships.filter(s => s.height_layer === observationLayer).length;
  const attackLayer = gunControl?.attackLayer ?? ownLayer;
  const canvasWeaponsEnabled = gunControl?.enabled && gunControl.canAim !== false && attackLayer === observationLayer;
  const selectCandidate = (candidate: typeof candidates[number]) => {
    if (!gunControl?.enabled) return;
    if (candidateMode.current === "weapon") gunControl.onWeapon(candidate.moduleId);
    else gunControl.onTarget(candidate.shipId, candidate.moduleId);
    setCandidates([]);
  };
  return <div className="tactical-viewport" data-observation-layer={observationLayer}>
    <div className="tactical-layer-bar">
      <nav aria-label="观察高度层"><span>观察层</span>{HEIGHT_LAYERS.map(layer => <button key={layer}
        aria-label={`观察${LAYER_NAMES[layer]}`} aria-pressed={observationLayer === layer} onClick={() => setObservationLayer(layer)}>
        {LAYER_NAMES[layer]} <small>{view.snapshot.ships.filter(s => s.height_layer === layer).length}</small>
      </button>)}</nav>
      <span className="layer-context">所选舰：{layerName(selectedPose?.height_layer)}{gunControl && ` · 火炮作用层：${layerName(attackLayer)}`}</span>
    </div>
    <div className="viewport-toolbar">
      <button disabled={!visibleCount} onClick={() => fit()}>适应本层</button>
      <button disabled={!selected} onClick={() => fit(selected!)}>聚焦所选舰</button>
      <button aria-label="战术放大" onClick={() => setCamera(c => zoomScene(c, { x: size.width / 2, y: size.height / 2 }, 1.5))}>＋</button>
      <button aria-label="战术缩小" onClick={() => setCamera(c => zoomScene(c, { x: size.width / 2, y: size.height / 2 }, 1 / 1.5))}>−</button>
      {!compact && <span className="muted">船艏箭头 · 绿色线表示 5 秒速度向量</span>}
      {gunControl && <label>舰内点选甲板<select aria-label="舰内点选甲板" value={pickLevel} onChange={e => { setPickLevel(e.target.value); setCandidates([]); }}>
        <option value="all">全部甲板（重叠时选择）</option>
        {[...new Set(view.geometry.ships.flatMap(s => s.decks.map(d => d.level)))].sort().map(level => <option key={level} value={level}>第 {level} 甲板</option>)}
      </select></label>}
    </div>
    {candidates.length > 0 && gunControl?.enabled && <div className="editor-row" aria-label="重叠模块候选">
      <span>选择{candidateMode.current === "weapon" ? "火炮" : "目标模块"}：</span>
      {candidates.map(c => <button key={c.shipId+c.moduleId} onClick={() => selectCandidate(c)}>{c.name} · 第 {c.level} 层 · {c.moduleId}</button>)}
      {candidateMode.current === "target" && <button onClick={() => { gunControl.onTarget(candidates[0].shipId, null); setCandidates([]); }}>瞄准整舰</button>}
      <button onClick={() => setCandidates([])}>取消选择</button>
    </div>}
    <div ref={host} className="tactical-canvas" tabIndex={active ? 0 : -1} aria-label={gunControl ? "战术画布，右键选择武器组，左键指定目标或射击，中键平移" : "战术画布，点击选舰，拖动平移，滚轮缩放"} onContextMenu={e => e.preventDefault()}
      onPointerDown={e => {
        if (![0, 1, 2].includes(e.button) || drag.current) return;
        e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { pointer: e.pointerId, start: point(e), camera, moved: false, button: e.button };
      }}
      onPointerMove={e => {
        const d = drag.current;
        if (!d && canvasWeaponsEnabled && gunControl?.mode === "manual" && gunControl.weaponId) gunControl.onAim(world(point(e), camera));
        if (!d || d.pointer !== e.pointerId) return;
        const p = point(e), dx = p.x - d.start.x, dy = p.y - d.start.y;
        d.moved ||= Math.hypot(dx, dy) > 4;
        if (d.moved && (!gunControl || d.button === 1)) setCamera({ ...d.camera, x: d.camera.x + dx, y: d.camera.y + dy });
      }}
      onPointerUp={e => {
        const d = drag.current; if (!d || d.pointer !== e.pointerId) return;
        drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId);
        if (d.moved) return;
        const released = point(e);
        if (released.x < 0 || released.y < 0 || released.x > size.width || released.y > size.height) return;
        const view = displayed.current;
        if (gunControl && (d.button === 0 || d.button === 2)) {
          if (!(d.button === 2 ? gunControl.enabled && ownLayer === observationLayer : canvasWeaponsEnabled)) { if (d.button === 0) onSelect(pickShip(view, released, camera)); return; }
          const p = point(e), level = pickLevel === "all" ? undefined : Number(pickLevel);
          if (d.button === 2) {
            const hits = pickModules(view, p, camera, gunControl.ownShipId, true, level);
            candidateMode.current = "weapon";
            if (hits.length === 1) { gunControl.onWeapon(hits[0].moduleId); setCandidates([]); }
            else setCandidates(hits);
          } else if (gunControl.weaponId && gunControl.mode === "manual") {
            gunControl.onFire(world(p, camera));
          } else if (gunControl.weaponId) {
            const ship = pickShip(view, p, camera);
            if (ship && view.geometry.ships.find(s=>s.id===ship)?.side_id !== view.geometry.ships.find(s=>s.id===gunControl.ownShipId)?.side_id) {
              const hits = pickModules(view, p, camera, ship, false, level);
              candidateMode.current = "target";
              if (hits.length === 1) { gunControl.onTarget(ship, hits[0].moduleId); setCandidates([]); }
              else if (hits.length) setCandidates(hits);
              else { gunControl.onTarget(ship, null); setCandidates([]); }
            } else onSelect(ship);
          } else onSelect(pickShip(view, p, camera));
        } else if (d.button === 0) onSelect(pickShip(view, point(e), camera));
      }}
      onPointerLeave={() => gunControl?.onLeave()}
      onPointerCancel={() => { drag.current = null; gunControl?.onLeave(); }} onLostPointerCapture={() => { drag.current = null; }} onBlur={() => { drag.current = null; gunControl?.onLeave(); }}
      onKeyDown={e => {
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
          e.preventDefault(); setCamera(c => ({ ...c, x: c.x + (e.key === "ArrowLeft" ? 50 : e.key === "ArrowRight" ? -50 : 0), y: c.y + (e.key === "ArrowUp" ? 50 : e.key === "ArrowDown" ? -50 : 0) }));
        } else if (["+", "=", "-"].includes(e.key)) {
          e.preventDefault(); setCamera(c => zoomScene(c, { x: size.width / 2, y: size.height / 2 }, e.key === "-" ? 1 / 1.3 : 1.3));
        } else if (e.key === "Home") { e.preventDefault(); fit(); }
        else if (e.key === "Escape") { drag.current = null; onSelect(null); }
      }}>
      {failure && <p role="alert" className="canvas-message editor-error">{failure}。舰艇列表仍可查看；可返回编辑再进入重试。</p>}
      {!ready && !failure && <p role="status" className="canvas-message">正在建立战术画布…</p>}
      {ready && <div className="tactical-scale" style={{ width: step * camera.scale }}>{step} m</div>}
      {ready && <>
        <div className="tactical-grid-key"><span>小格 50 m</span><span>参考线 500 m</span><strong>主参考线 5 km</strong></div>
        {references.map(line => <span key={`${line.axis}:${line.world_m}`} className={`tactical-grid-coordinate ${line.spacing === 5000 ? 'major' : ''}`}
          aria-hidden="true" style={line.axis === 'x' ? { left: line.pixel+5, top: 4 } : { left: 5, top: line.pixel+3 }}>
          {line.axis.toUpperCase()} {distanceLabel(line.world_m)}
        </span>)}
        {!visibleCount && <p className="tactical-layer-empty" role="status">{LAYER_NAMES[observationLayer]}暂无已知舰艇</p>}
      </>}
      {ready && view.snapshot.ships.map(pose => {
        return <span key={pose.id} className="tactical-ship-label" data-ship-id={pose.id}
          ref={element => { if (element) labels.current.set(pose.id, element); else labels.current.delete(pose.id); }}
          style={{ left: 0, top: 0 }}>
          {view.geometry.ships.find(s => s.id === pose.id)?.name}{pose.wreck ? ' · 残骸' : pose.descent ? ' · 下坠' : ''}
        </span>;
      })}
    </div>
    {artFailure && <p className="tactical-art-notice" role="status">部分云海素材暂未载入，仍可继续操作战场。</p>}
    {gunControl && <div className="tactical-layer-hint">{ownLayer !== observationLayer ? <>
      旗舰位于{layerName(ownLayer)}。<button onClick={() => { if (isHeightLayer(ownLayer)) setObservationLayer(ownLayer); }}>返回旗舰所在层</button>
    </> : '观察切换不改变舰艇高度与火炮作用层。'}</div>}
    <details className="viewport-help" open={!compact}><summary>战场操作说明</summary>
      <p>{gunControl ? "右键本舰火炮选择；自动模式左键敌舰或模块指定目标，手动模式跟随鼠标、左键单发。中键拖动平移，滚轮缩放。黄色粗线是实际炮向，十字是瞄准点。" : "点击舰体或右侧列表选舰；拖动平移，滚轮缩放。"}画布获得焦点后可用方向键平移、＋/− 缩放、Home 查看全场。</p>
      <p>观察层切换只改变画面。先在火炮面板选择炮弹作用层，再切到该层瞄准；炮弹发射后固定在该层。舰内甲板叠加显示，“舰内点选甲板”仅区分重叠模块。网格固定为 50 米，缩远时淡出过密细线。箭头指向船艏，绿色线表示 5 秒速度向量。</p>
    </details>
  </div>;
}
