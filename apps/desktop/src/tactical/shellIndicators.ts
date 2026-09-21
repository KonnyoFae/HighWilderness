import type { Graphics } from '../rendering/pixi';
import { screen } from '../editor/viewport';
import type { Camera } from '../editor/viewport';
import type { DisplayProjectile } from './model';

export function shellIndicatorSize(p: DisplayProjectile): 1 | 3 | null {
  if (p.kind !== 'shell') return null;
  return p.has_durability || (p.maximum_durability ?? p.durability ?? 0) > 0 ? 3 : 1;
}

// Screen-space, opaque indicators stay legible above weather and do not scale
// with the camera. Four filled strips give the 3x3 frame an exact 1px empty centre.
export function drawShellIndicators(graphics: Graphics, projectiles: DisplayProjectile[], camera: Camera, friendlyShips: Set<string>) {
  for (const p of projectiles) {
    const size = shellIndicatorSize(p);
    if (!size) continue;
    const at = screen({x:p.position_m[0], y:p.position_m[1]}, camera);
    const x = Math.round(at.x) - (size === 3 ? 1 : 0), y = Math.round(at.y) - (size === 3 ? 1 : 0);
    const color = friendlyShips.has(p.ship_id) ? 0x287dff : 0xf04452;
    if (size === 1) graphics.rect(x, y, 1, 1).fill(color);
    else graphics.rect(x, y, 3, 1).rect(x, y+2, 3, 1)
      .rect(x, y+1, 1, 1).rect(x+2, y+1, 1, 1).fill(color);
  }
}
