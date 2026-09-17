import type { TacticalView } from './model';

export const HEIGHT_LAYERS = ['upper', 'cloud', 'rain'] as const;
export type HeightLayer = typeof HEIGHT_LAYERS[number];
export const LAYER_NAMES: Record<HeightLayer, string> = { upper: '上层', cloud: '云层', rain: '雨层' };
export const isHeightLayer = (value: unknown): value is HeightLayer => HEIGHT_LAYERS.includes(value as HeightLayer);
export const layerName = (value: unknown) => isHeightLayer(value) ? LAYER_NAMES[value] : '未知高度';
export function initialObservationLayer(view: TacticalView, shipId?: string | null): HeightLayer {
  const layer = (view.snapshot.ships.find(s => s.id === shipId) ?? view.snapshot.ships[0])?.height_layer;
  return isHeightLayer(layer) ? layer : 'upper';
}

// Filtering is strictly a display/picking operation. Never use the observation
// layer as an attack command, projectile layer, or ship-motion update.
export function viewOnLayer(view: TacticalView, layer: HeightLayer): TacticalView {
  const { snapshot } = view;
  const ships = snapshot.ships.filter(s => s.height_layer === layer);
  const visible = new Set(ships.map(s => s.id));
  const belongs = (explicit: string | null | undefined, shipId: string) =>
    (explicit ?? snapshot.ships.find(s => s.id === shipId)?.height_layer) === layer;
  const gunnery = snapshot.gunnery;
  return { geometry: view.geometry, snapshot: { ...snapshot, ships,
    gunnery: gunnery && { ...gunnery,
      weapons: gunnery.weapons.filter(g => visible.has(g.ship_id) || g.effective_layer === layer).map(g =>
        (g.effective_layer !== undefined && g.effective_layer !== layer) || (g.target_ship_id && !visible.has(g.target_ship_id))
          ? { ...g, aim_point_m: null, target_module_id: null } : g),
      projectiles: gunnery.projectiles.filter(p => belongs(p.height_layer, p.ship_id)),
      point_defense: gunnery.point_defense && {...gunnery.point_defense,
        recent:gunnery.point_defense.recent.filter(e=>belongs(e.height_layer,e.source_ship_id)),
        threats:gunnery.point_defense.threats.filter(e=>belongs(e.height_layer,e.ship_id))},
      damage: gunnery.damage && { ...gunnery.damage,
        magazine_explosions: gunnery.damage.magazine_explosions?.filter(event => belongs(event.height_layer, event.ship_id)),
        recent: gunnery.damage.recent.filter(hit => belongs(hit.height_layer, hit.ship_id)) } } } };
}
