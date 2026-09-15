import type { GunView } from './model';
import type { GunIntent } from './gunnery';
import { HEIGHT_LAYERS, isHeightLayer, layerName } from './layers';

export function GunBallisticsPanel({gun, ownLayer, onCommand, hideLayerControl = false}: {
  gun: GunView; ownLayer?: string; onCommand: (intent: GunIntent) => void; hideLayerControl?: boolean;
}) {
  const flight = gun.ballistics;
  if (!flight) return null;
  return <div className="gun-ballistics" aria-label="火炮弹道与作用层">
    {!hideLayerControl && <div className="editor-row"><label>炮弹作用层<select aria-label="炮弹作用层" value={gun.attack_layer ?? ''}
      onChange={e => onCommand({kind:'layer',arguments:{layer:e.target.value || null}})}>
      <option value="">跟随本舰所在层</option>
      {HEIGHT_LAYERS.map(layer => <option key={layer} value={layer}
        disabled={!isHeightLayer(ownLayer) || Math.abs(HEIGHT_LAYERS.indexOf(layer)-HEIGHT_LAYERS.indexOf(ownLayer))>1}>{layerName(layer)}</option>)}
    </select></label></div>}
    <p>{flight.caliber_mm} 毫米 · 初速 {Math.round(flight.effective_speed_mps)} 米/秒 · 寿命 {flight.lifetime_s.toFixed(1)} 秒</p>
    <p>静止炮口参考射程 {(flight.reference_range_m/1000).toFixed(2)} 公里 · 连续射速 {Math.round(flight.cyclic_rpm)} 发/分（另计换批装填）</p>
    <small>{flight.drag ? '飞行中受阻力减速。' : '旧存档弹道：沿用原有技术参数。'}
      {flight.speed_retention < 1 ? `跨层初速保留 ${Math.round(flight.speed_retention*100)}%，寿命不变。` : '跨层只能选择相邻层。'}炮弹穿过友舰。</small>
  </div>;
}
