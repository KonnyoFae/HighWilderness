import { Container, Graphics, Texture, TilingSprite } from '../rendering/pixi';
import type { Camera } from '../editor/viewport';
import type { HeightLayer } from './layers';
import upperClouds from './art/upper-cloud-sea.svg';
import middleClouds from './art/cloud-sea.svg';
import foregroundClouds from './art/cloud-foreground.svg';
import lowerClouds from './art/rain-cloud-sea.svg';
import rain from './art/rain-foreground.svg';

export type AtmosphereAsset = { url: string; opacity: number; parallax: number; tilePixels: number;
  scrollPixelsPerSecond?: readonly [number, number] };
export type LayerArt = { baseColor: number; background: AtmosphereAsset; foreground: AtmosphereAsset | null };
// Independent art slots. Replace the URL or sampling parameters here without
// touching combat, visibility, or fixed-distance grid rules.
export const TACTICAL_LAYER_ART: Record<HeightLayer, LayerArt> = {
  upper: { baseColor: 0xc5d0cb, background: { url: upperClouds, opacity: 1, parallax: .14, tilePixels: 1024 }, foreground: null },
  cloud: { baseColor: 0x47565d, background: { url: middleClouds, opacity: 1, parallax: .14, tilePixels: 1024 },
    foreground: { url: foregroundClouds, opacity: .34, parallax: .28, tilePixels: 1152 } },
  rain: { baseColor: 0x15252f, background: { url: lowerClouds, opacity: 1, parallax: .14, tilePixels: 1024 },
    foreground: { url: rain, opacity: .85, parallax: .1, tilePixels: 512, scrollPixelsPerSecond: [-110, 340] } },
};

export function atmosphereOffset(asset: AtmosphereAsset, camera: Camera, timeSeconds: number) {
  const repeat = asset.tilePixels;
  return { x: (camera.x*asset.parallax+(asset.scrollPixelsPerSecond?.[0] ?? 0)*timeSeconds)%repeat,
    y: (camera.y*asset.parallax+(asset.scrollPixelsPerSecond?.[1] ?? 0)*timeSeconds)%repeat };
}

export class TacticalAtmosphere {
  readonly background = new Container();
  readonly foreground = new Container();
  private base = new Graphics();
  private behind = new TilingSprite({ texture: Texture.WHITE });
  private ahead = new TilingSprite({ texture: Texture.WHITE });
  private textures = new Map<string, Texture>();
  private disposed = false;
  private baseKey = '';

  constructor(private art: Record<HeightLayer, LayerArt> = TACTICAL_LAYER_ART) {
    this.behind.visible = this.ahead.visible = false;
    this.background.addChild(this.base, this.behind); this.foreground.addChild(this.ahead);
    this.background.eventMode = this.foreground.eventMode = 'none';
  }

  async load() {
    const urls = [...new Set(Object.values(this.art).flatMap(layer => [layer.background.url, ...(layer.foreground ? [layer.foreground.url] : [])]))];
    const results = await Promise.allSettled(urls.map(async url => {
      const bitmap = new Image(); bitmap.src = url; await bitmap.decode();
      // Each viewport owns these textures; never unload another canvas's cache.
      if (!this.disposed) this.textures.set(url, Texture.from(bitmap));
    }));
    return results.every(result => result.status === 'fulfilled');
  }

  draw(layer: HeightLayer, camera: Camera, width: number, height: number, timeSeconds: number) {
    const art = this.art[layer], key = `${layer}:${width}:${height}`;
    if (key !== this.baseKey) {
      this.base.clear().rect(0, 0, width, height).fill(art.baseColor); this.baseKey = key;
    }
    const configure = (sprite: TilingSprite, asset: AtmosphereAsset | null) => {
      const texture = asset && this.textures.get(asset.url);
      sprite.visible = !!texture;
      if (!texture || !asset) return;
      sprite.texture = texture; sprite.width = width; sprite.height = height; sprite.alpha = asset.opacity;
      sprite.tileScale.set(asset.tilePixels/texture.width, asset.tilePixels/texture.height);
      const offset = atmosphereOffset(asset, camera, timeSeconds);
      sprite.tilePosition.set(offset.x, offset.y);
    };
    configure(this.behind, art.background); configure(this.ahead, art.foreground);
  }

  destroy() {
    this.disposed = true;
    this.background.destroy({ children: true, context: true }); this.foreground.destroy({ children: true, context: true });
    for (const texture of this.textures.values()) texture.destroy(true);
    this.textures.clear();
  }
}
