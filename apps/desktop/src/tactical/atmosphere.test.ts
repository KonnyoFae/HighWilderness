import { describe, expect, it } from 'vitest';
import { atmosphereOffset, TACTICAL_LAYER_ART } from './atmosphere';

describe('layer art slots and display clock', () => {
  it('has independent upper/cloud/rain backgrounds and the required foregrounds', () => {
    expect(new Set(Object.values(TACTICAL_LAYER_ART).map(layer => layer.background.url)).size).toBe(3);
    expect(TACTICAL_LAYER_ART.upper.foreground).toBeNull();
    expect(TACTICAL_LAYER_ART.cloud.foreground!.url).not.toBe(TACTICAL_LAYER_ART.cloud.background.url);
    expect(TACTICAL_LAYER_ART.cloud.foreground!.opacity).toBeGreaterThan(0);
    expect(TACTICAL_LAYER_ART.cloud.foreground!.opacity).toBeLessThan(1);
    expect(TACTICAL_LAYER_ART.rain.foreground!.scrollPixelsPerSecond).toEqual([-110, 340]);
  });
  it('moves rain only with sampled simulation time, with no elapsed wall-time source', () => {
    const art = TACTICAL_LAYER_ART.rain.foreground!, camera = { x: 100, y: 200, scale: 1 };
    expect(atmosphereOffset(art, camera, 1)).not.toEqual(atmosphereOffset(art, camera, 1.1));
    expect(atmosphereOffset(art, camera, 1)).toEqual(atmosphereOffset(art, camera, 1));
    expect(atmosphereOffset(TACTICAL_LAYER_ART.cloud.foreground!, camera, 1)).toEqual(
      atmosphereOffset(TACTICAL_LAYER_ART.cloud.foreground!, camera, 20));
  });
});
