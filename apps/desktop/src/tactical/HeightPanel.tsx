import type { HeightNavigation, TacticalSnapshot } from './model';
import { HEIGHT_LAYERS, layerName } from './layers';
import type { HeightLayer } from './layers';

const reasons: Record<string, string> = {
  missing_baseline: '缺少入战换层参数', ship_unavailable: '舰艇已失去作战能力',
  command_unavailable: '舰艇无法接收换层命令', no_entry_surplus: '入战时没有可用于主动换层的净升力',
  no_lift_surplus: '当前没有正升力冗余，无法主动换层',
};
export function heightTime(seconds: number | null) {
  if (seconds === null) return '不可用';
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${(seconds % 60).toFixed(1)} 秒` : `${seconds.toFixed(1)} 秒`;
}

export function HeightPanel({ value, actualLayer, friendly, disabled, uncertain, onTarget, descent, wreck }: {
  value?: HeightNavigation | null; actualLayer: string; friendly: boolean; disabled: boolean; uncertain: boolean;
  onTarget: (layer: HeightLayer | null) => void;
  descent?: TacticalSnapshot['ships'][number]['descent']; wreck?: TacticalSnapshot['ships'][number]['wreck'];
}) {
  if (!value) return null;
  return <section className="height-panel" aria-label="舰艇换层">
    <h4>{friendly ? '换层指令' : '换层状态'}</h4>
    <p>实际高度：<strong>{layerName(actualLayer)}</strong></p>
    {wreck ? <p role="status">已坠毁 · 留下可打捞残骸</p> : descent && <div className="height-progress descent-warning" role="status" aria-label="强制下坠">
      <strong>{descent.paused ? '升力恰好持平 · 下坠进度冻结' : descent.next_layer ? `正在下坠至${layerName(descent.next_layer)}` : '雨层下坠 · 即将坠毁'}</strong>
      <p>{descent.paused ? '再次出现升力缺口时继续；恢复正冗余后解除。' : `本段剩余 ${heightTime(descent.remaining_s)}`}</p>
      <progress aria-label="强制下坠进度" value={descent.progress} max={1} />
      <p>抢修升力储罐可停止下坠。下坠期间仍可移动、攻击和损管。</p>
    </div>}
    {value.target_layer && <div className="height-progress" aria-label="当前换层进度">
      <p>{layerName(actualLayer)} → {layerName(value.next_layer)} · 本段剩余 {heightTime(value.remaining_s)}</p>
      <progress aria-label="相邻层换层进度" value={value.progress} max={1} />
      <p>最终目标：{layerName(value.target_layer)}{value.next_layer !== value.target_layer ? ` · 全程预计剩余 ${heightTime(value.total_remaining_s)}` : ''}</p>
    </div>}
    {!descent && !wreck && <p className="muted">主动换层相邻层 5 公里 · 入战基准 {heightTime(value.base_duration_s)}<br />
      当前每段 {heightTime(value.duration_s)}{value.lift_loss_fraction > 0 ? `（升力损失 ${(value.lift_loss_fraction * 100).toFixed(1)}%，耗时增加同比例）` : ''}</p>}
    {friendly && <>
      <div className="height-targets">{HEIGHT_LAYERS.map(layer => <button key={layer}
        disabled={disabled || uncertain || !!descent || !!wreck || !!value.unavailable_reason || layer === actualLayer || layer === value.target_layer}
        aria-pressed={layer === value.target_layer} onClick={() => onTarget(layer)}>前往{layerName(layer)}</button>)}</div>
      <button disabled={disabled || uncertain || !value.target_layer} onClick={() => onTarget(null)}>取消换层</button>
      <p className="muted">换层与移动、攻击同时执行。更改目标会重新开始本段；取消保留当前实际层。</p>
      {value.unavailable_reason && <p>{reasons[value.unavailable_reason] ?? '当前无法换层'}</p>}
      {uncertain && <p role="status">换层状态待确认，正在读取；不会自动重复发令。</p>}
    </>}
  </section>;
}
