import { gridLines, screen, world } from "./viewport";
import type { Camera } from "./viewport";

export function GridOverlay({ camera, width, height, axesOnly = false, gridOnly = false }: { camera: Camera; width: number; height: number; axesOnly?: boolean; gridOnly?: boolean }) {
  const lo = world({ x: 0, y: height }, camera), hi = world({ x: width, y: 0 }, camera);
  const xs = gridLines(lo.x, hi.x, camera.scale), ys = gridLines(lo.y, hi.y, camera.scale);
  const clamp = (n: number, max: number) => Math.max(20, Math.min(max - 20, n));
  return <svg className="editor-grid-overlay" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
    {!axesOnly && <g>{xs.map(l => <line key={`x${l.value}`} x1={screen({x:l.value,y:0},camera).x} x2={screen({x:l.value,y:0},camera).x} y1={0} y2={height} stroke={l.color} strokeWidth={l.width} strokeOpacity={l.alpha}/>)}
      {ys.map(l => <line key={`y${l.value}`} x1={0} x2={width} y1={screen({x:0,y:l.value},camera).y} y2={screen({x:0,y:l.value},camera).y} stroke={l.color} strokeWidth={l.width} strokeOpacity={l.alpha}/>)}</g>}
    {!gridOnly && <><g className="grid-labels" fontSize={12}>
      {xs.filter(l => l.tier === "ten" || l.tier === "five" && camera.scale >= 3).map(l => <text key={l.value} x={screen({x:l.value,y:0},camera).x+4} y={height-9}>{l.value} m</text>)}
      {ys.filter(l => l.tier === "ten" || l.tier === "five" && camera.scale >= 3).map(l => <text key={l.value} x={8} y={screen({x:0,y:l.value},camera).y-5}>{l.value} m</text>)}
    </g>
    <line x1={0} x2={width} y1={camera.y} y2={camera.y} stroke="#faac7c" strokeWidth={2}/>
    <line x1={camera.x} x2={camera.x} y1={0} y2={height} stroke="#70c8ff" strokeWidth={2}/>
    <g className="grid-labels" fontSize={13}>
      <text x={width-78} y={clamp(camera.y-9,height)} fill="#faac7c">X →</text>
      <text x={clamp(camera.x+9,width)} y={24} fill="#70c8ff">Y ↑ 舰艏</text>
      {camera.x>=0 && camera.x<=width && camera.y>=0 && camera.y<=height && <g>
        <circle cx={camera.x} cy={camera.y} r={6} fill="#081b20" stroke="#fff1bd" strokeWidth={2}/>
        <text x={clamp(camera.x+12,width-55)} y={clamp(camera.y+22,height)}>原点 (0, 0)</text>
      </g>}
    </g></>}
  </svg>;
}
