import { Application, Container, Graphics } from "../rendering/pixi";
import { useEffect, useRef, useState } from "react";
import { screen, world } from "../editor/viewport";
import type { Camera, Point } from "../editor/viewport";
import type { TacticalView } from "./model";
import { fitScene, gridSpacing, moduleFootprints, pickShip, shipPoints, zoomScene } from "./viewport";
import type { ShipGeometry } from "./viewport";

type ShipObjects = { root: Container; selection: Graphics; modules: { id: string; max: number; graphic: Graphics }[] };
function buildShip(ship: ShipGeometry): ShipObjects {
  const root = new Container(), selection = new Graphics();
  const color = ship.side_id === "side.blue" ? 0x70c9f1 : 0xf18e80;
  const modules: ShipObjects["modules"] = [];
  const footprints = ship.modules.map(m => ({ module: m, cells: moduleFootprints(m) }));
  const levels = [...new Set([...ship.decks.map(d => d.level), ...footprints.flatMap(m => m.cells.map(c => c.level))])].sort((a, b) => a - b);
  for (const level of levels) {
    const hull = new Graphics(); root.addChild(hull);
    for (const region of ship.decks.filter(d => d.level === level).flatMap(d => d.regions)) {
      hull.poly(region.vertices_m.flat(), true).fill({ color, alpha: .22 }).stroke({ color, width: .65, alpha: .85 });
      selection.poly(region.vertices_m.flat(), true).stroke({ color: 0xffe6a2, width: 1.6 });
    }
    for (const { module, cells } of footprints) {
      const graphic = new Graphics();
      for (const p of cells.filter(p => p.level === level)) {
        const tint = p.kind === "top" ? 0xead693 : p.kind === "body" ? 0xb3a0db : color;
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

export function TacticalViewport({ view, active, selected, onSelect }: {
  view: TacticalView; active: boolean; selected: string | null; onSelect: (id: string | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const renderer = useRef<Application | null>(null);
  const scene = useRef<Container | null>(null);
  const grid = useRef<Graphics | null>(null), vectors = useRef<Graphics | null>(null);
  const objects = useRef(new Map<string, ShipObjects>());
  const [ready, setReady] = useState(false), [failure, setFailure] = useState("");
  const [size, setSize] = useState({ width: 800, height: 540 });
  const [camera, setCamera] = useState<Camera>(() => fitScene(view, 800, 540));
  const fitted = useRef(false);
  const latest = useRef({ view, camera }); latest.current = { view, camera };
  const drag = useRef<{ pointer: number; start: Point; camera: Camera; moved: boolean; button: number } | null>(null);

  useEffect(() => {
    if (!active) return;
    let disposed = false;
    const app = new Application(), element = host.current!;
    let observer: ResizeObserver | undefined;
    setFailure("");
    void app.init({ preference: "webgl", width: size.width, height: size.height, background: 0x081b20,
      antialias: true, resolution: window.devicePixelRatio || 1, autoDensity: true, autoStart: false }).then(() => {
      if (disposed) { app.destroy(true, { children: true, context: true }); return; }
      renderer.current = app;
      grid.current = new Graphics(); scene.current = new Container(); vectors.current = new Graphics();
      app.stage.addChild(grid.current, scene.current, vectors.current);
      element.appendChild(app.canvas);
      const resize = () => {
        const width = element.clientWidth, height = element.clientHeight;
        if (!width || !height) return;
        const oldWidth = app.screen.width, oldHeight = app.screen.height;
        app.renderer.resolution = window.devicePixelRatio || 1;
        app.renderer.resize(width, height); setSize({ width, height });
        setCamera(c => fitted.current ? { ...c, x: c.x + (width - oldWidth) / 2, y: c.y + (height - oldHeight) / 2 } : fitScene(latest.current.view, width, height));
        fitted.current = true;
      };
      observer = new ResizeObserver(resize); observer.observe(element);
      resize(); setReady(true);
    }).catch(error => { if (!disposed) setFailure(`画布初始化失败：${String(error)}`); });
    return () => {
      disposed = true; observer?.disconnect(); drag.current = null; setReady(false);
      if (renderer.current === app) {
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
      const object = buildShip(ship); scene.current.addChild(object.root); objects.current.set(ship.id, object);
    }
  }, [ready, view.geometry]);

  useEffect(() => {
    const app = renderer.current, root = scene.current, g = grid.current, v = vectors.current;
    if (!active || !ready || !app || !root || !g || !v) return;
    root.position.set(camera.x, camera.y); root.scale.set(camera.scale, -camera.scale);
    const lo = world({ x: 0, y: size.height }, camera), hi = world({ x: size.width, y: 0 }, camera);
    const step = gridSpacing(camera.scale); g.clear(); v.clear();
    for (let x = Math.ceil(lo.x / step) * step; x <= hi.x; x += step) {
      const p = screen({ x, y: 0 }, camera); g.moveTo(p.x, 0).lineTo(p.x, size.height);
    }
    for (let y = Math.ceil(lo.y / step) * step; y <= hi.y; y += step) {
      const p = screen({ x: 0, y }, camera); g.moveTo(0, p.y).lineTo(size.width, p.y);
    }
    g.stroke({ color: 0x244047, width: 1, alpha: .7 });
    for (const [id, object] of objects.current) {
      const pose = view.snapshot.ships.find(s => s.id === id);
      object.root.visible = !!pose;
      if (!pose) continue;
      object.root.position.set(pose.position_m[0], pose.position_m[1]); object.root.rotation = pose.heading_rad;
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
          .moveTo(end.x, end.y).lineTo(end.x - 7 * Math.cos(a + .4), end.y - 7 * Math.sin(a + .4)).stroke({ color: 0xa3efb9, width: 1.5 });
      }
    }
    // No ticker: paused scenes and hidden workspaces consume no animation loop.
    app.render();
  }, [active, ready, view, selected, camera, size]);

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
  const fit = (id?: string) => setCamera(fitScene(view, size.width, size.height, id));
  const step = gridSpacing(camera.scale);
  return <div className="tactical-viewport">
    <div className="viewport-toolbar">
      <button onClick={() => fit()}>适应全场</button>
      <button disabled={!selected} onClick={() => fit(selected!)}>聚焦所选舰</button>
      <button aria-label="战术放大" onClick={() => setCamera(c => zoomScene(c, { x: size.width / 2, y: size.height / 2 }, 1.5))}>＋</button>
      <button aria-label="战术缩小" onClick={() => setCamera(c => zoomScene(c, { x: size.width / 2, y: size.height / 2 }, 1 / 1.5))}>−</button>
      <span className="muted">多层叠加 · 船艏箭头 · 绿色线表示 5 秒速度向量</span>
    </div>
    <div ref={host} className="tactical-canvas" tabIndex={active ? 0 : -1} aria-label="战术画布，点击选舰，拖动平移，滚轮缩放" onContextMenu={e => e.preventDefault()}
      onPointerDown={e => {
        if (![0, 1, 2].includes(e.button) || drag.current) return;
        e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { pointer: e.pointerId, start: point(e), camera, moved: false, button: e.button };
      }}
      onPointerMove={e => {
        const d = drag.current; if (!d || d.pointer !== e.pointerId) return;
        const p = point(e), dx = p.x - d.start.x, dy = p.y - d.start.y;
        d.moved ||= Math.hypot(dx, dy) > 4;
        if (d.moved) setCamera({ ...d.camera, x: d.camera.x + dx, y: d.camera.y + dy });
      }}
      onPointerUp={e => {
        const d = drag.current; if (!d || d.pointer !== e.pointerId) return;
        drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId);
        if (!d.moved && d.button === 0) onSelect(pickShip(view, point(e), camera));
      }}
      onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }} onBlur={() => { drag.current = null; }}
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
      {ready && view.snapshot.ships.map(pose => {
        const p = screen({ x: pose.position_m[0], y: pose.position_m[1] }, camera);
        return <span key={pose.id} className="tactical-ship-label" style={{ left: p.x + 18, top: p.y }}>
          {view.geometry.ships.find(s => s.id === pose.id)?.name}
        </span>;
      })}
    </div>
    <p className="viewport-help">点击舰体或右侧列表选舰；拖动平移，滚轮缩放。画布获得焦点后可用方向键平移、＋/− 缩放、Home 查看全场。</p>
  </div>;
}
