import type { Camera } from '../editor/viewport';
import { screen, world } from '../editor/viewport';

export const GRID_SPACINGS = [50, 500, 5000] as const;
export type GridSpacing = typeof GRID_SPACINGS[number];
export type GridLine = { axis: 'x' | 'y'; world_m: number; pixel: number; spacing: GridSpacing; alpha: number };
const fade = (pixels: number, lo: number, hi: number) => Math.max(0, Math.min(1, (pixels-lo)/(hi-lo)));
export function gridOpacity(spacing: GridSpacing, scale: number) {
  return spacing === 50 ? .24*fade(spacing*scale, 3, 12) : spacing === 500 ? .58*fade(spacing*scale, 8, 24) : .88;
}
export function distanceGrid(camera: Camera, width: number, height: number): GridLine[] {
  const lo = world({ x: 0, y: height }, camera), hi = world({ x: width, y: 0 }, camera);
  const lines: GridLine[] = [];
  for (const spacing of GRID_SPACINGS) {
    const alpha = gridOpacity(spacing, camera.scale);
    if (!alpha) continue;
    for (const axis of ['x', 'y'] as const) {
      for (let n = Math.ceil(lo[axis]/spacing); n <= Math.floor(hi[axis]/spacing); n++) {
        // The stronger level owns coincident lines, including negative axes.
        if (spacing !== 5000 && n % 10 === 0) continue;
        const position = n*spacing;
        const point = screen(axis === 'x' ? { x: position, y: 0 } : { x: 0, y: position }, camera);
        lines.push({ axis, world_m: position, pixel: point[axis], spacing, alpha });
      }
    }
  }
  return lines;
}
export function gridLabels(lines: GridLine[], scale: number, width: number, height: number) {
  const spacing = 500*scale >= 85 ? 500 : 5000;
  return lines.filter(line => line.spacing >= spacing && line.pixel >= 28 &&
    line.pixel < (line.axis === 'x' ? width-70 : height-65));
}
export function distanceLabel(metres: number) {
  return Math.abs(metres) >= 1000 ? `${metres/1000} km` : `${metres} m`;
}
export function scaleReference(scale: number): number {
  // The ruler can show a fraction of one cell when closely zoomed in; grid
  // coordinates themselves always remain on the 50/500/5000 metre levels.
  return [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000].find(m => m*scale >= 65) ?? 5000;
}
