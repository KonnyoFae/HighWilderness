import { makeHelmControl, NOTCHES } from './control';
import type { HelmDraft, Notch } from './control';
import type { TacticalControlInput, TacticalSnapshot } from './model';
import { HeightPanel } from './HeightPanel';
import type { HeightLayer } from './layers';

export type HelmOrder = { draft: HelmDraft; turn: -1 | 0 | 1; turnPercent: number; stabilize: boolean };
export type HelmIntent = { notch: Notch } | { direction: HelmDraft['direction'] } | { turn: -1 | 0 | 1; power?: 50 | 100 } | { brake: boolean };
export const NEUTRAL_HELM: HelmOrder = { draft: { notch: 'stop', direction: 'forward', brake: false }, turn: 0, turnPercent: 50, stabilize: false };
export function changeHelm(order: HelmOrder, intent: HelmIntent): HelmOrder {
  if ('turn' in intent) {
    const power = intent.power ?? 50;
    const cancel = intent.turn === 0 || order.turn === intent.turn && order.turnPercent === power && !order.stabilize;
    return { draft: { ...order.draft, brake: false }, turn: cancel ? 0 : intent.turn, turnPercent: power, stabilize: cancel };
  }
  if ('brake' in intent) return { ...order, draft: { ...order.draft, notch: 'stop', brake: intent.brake }, turn: 0, stabilize: true };
  return { ...order, draft: { ...order.draft, ...intent, brake: false } };
}
export function readHelm(control?: TacticalControlInput['arguments']['control'], idleDirection: HelmDraft['direction'] = 'forward'): HelmOrder {
  if (!control) return NEUTRAL_HELM;
  const channels = control.channel_commands;
  const reverse = channels.find(c => c.command_channel === 'translation.reverse');
  const forward = channels.find(c => c.command_channel === 'translation.forward');
  const direction = reverse?.commanded_notch && reverse.commanded_notch !== 'stop' ? 'reverse' :
    forward?.commanded_notch && forward.commanded_notch !== 'stop' ? 'forward' : idleDirection;
  const notch = channels.find(c => c.command_channel === `translation.${direction}`)?.commanded_notch ?? 'stop';
  const yaw = channels.find(c => c.command_channel.startsWith('yaw.') && (c.target_output_percent ?? 0) > 0);
  return { draft: { direction, notch: notch as Notch, brake: control.automatic_brake }, turnPercent: yaw?.target_output_percent || 50,
    stabilize: !!control.automatic_yaw_brake,
    turn: channels.some(c => c.command_channel === 'yaw.counterclockwise' && (c.target_output_percent ?? 0) > 0) ? -1 :
      channels.some(c => c.command_channel === 'yaw.clockwise' && (c.target_output_percent ?? 0) > 0) ? 1 : 0 };
}
export const helmControl = (order: HelmOrder): TacticalControlInput['arguments']['control'] => {
  const control = makeHelmControl(order.draft, order.turn, order.turnPercent);
  return order.stabilize ? { ...control, interface: 'gaotian.tactical-propulsion-control/v3alpha1', automatic_yaw_brake: true } : control;
};
const shortNotches: Record<Notch, string> = { stop: '停车', dead_slow: '微速', quarter: '¼', half: '½', three_quarter: '¾', full: '全速' };

export function FlagshipHelm({ name, pose, order, disabled, uncertain, heightUncertain, onOrder, onHeight, yawStatus }: {
  yawStatus?: 'braking' | 'settled' | 'unavailable' | null;
  name: string; pose: TacticalSnapshot['ships'][number]; order: HelmOrder; disabled: boolean; uncertain: boolean;
  heightUncertain: boolean; onOrder: (order: HelmOrder) => void; onHeight: (target: HeightLayer | null) => void;
}) {
  return <section className="flagship-helm" aria-label="旗舰即时操纵">
    <div className="dock-heading"><h3>旗舰 · {name}</h3><small>{pose.speed_mps.toFixed(1)} m/s · {(pose.yaw_rate_radps * 180 / Math.PI).toFixed(1)} °/s</small></div>
    <fieldset disabled={disabled || uncertain}>
      <legend>推进与转向</legend>
      <div className="helm-direction" aria-label="推进方向">{(['forward', 'reverse'] as const).map(direction =>
        <button key={direction} aria-pressed={order.draft.direction === direction} onClick={() => onOrder(changeHelm(order, { direction }))}>{direction === 'forward' ? '前进' : '倒车'}</button>)}
        <button className="helm-brake" aria-pressed={order.draft.brake} onClick={() => onOrder(changeHelm(order, { brake: !order.draft.brake }))}>{order.draft.brake ? '解除制动' : '制动'}</button>
      </div>
      <div className="helm-maneuver">
      <div className="helm-turn-column" aria-label="左转档位"><span>↶ 左转</span>{([50, 100] as const).map(power => <button key={power}
        aria-label={`左转${power === 50 ? '半速' : '全速'}`} aria-pressed={!order.stabilize && order.turn === -1 && order.turnPercent === power}
        onClick={() => onOrder(changeHelm(order, { turn: -1, power }))}>{power === 50 ? '半速' : '全速'}</button>)}</div>
      <div className="helm-notches" aria-label="即时车钟">{(Object.keys(NOTCHES) as Notch[]).reverse().map(notch => <button key={notch}
        title={NOTCHES[notch]} aria-label={`车钟${NOTCHES[notch]}`} aria-pressed={!order.draft.brake && order.draft.notch === notch}
        onClick={() => onOrder(changeHelm(order, { notch }))}>{shortNotches[notch]}</button>)}</div>
      <div className="helm-turn-column" aria-label="右转档位"><span>右转 ↷</span>{([50, 100] as const).map(power => <button key={power}
        aria-label={`右转${power === 50 ? '半速' : '全速'}`} aria-pressed={!order.stabilize && order.turn === 1 && order.turnPercent === power}
        onClick={() => onOrder(changeHelm(order, { turn: 1, power }))}>{power === 50 ? '半速' : '全速'}</button>)}</div>
      </div>
    </fieldset>
    {uncertain && <p role="status">操纵正在确认，确认后可继续下令。</p>}
    <p className="helm-yaw-status" role="status">{order.stabilize ? yawStatus === 'unavailable' ? '回正受阻 · 缺少可用反向转向推力' : yawStatus === 'settled' ? '航向稳定' : '自动回正中 · 正在消除角速度' : order.turn ? `${order.turn < 0 ? '左转' : '右转'} · ${order.turnPercent}% 推力` : '未施加转向指令'}</p>
    <small className="helm-hint">再次点击当前转向档位自动回正；只消除角速度，不返回原航向。停车保留惯性，制动同时回正。</small>
    <HeightPanel compact value={pose.height_navigation} actualLayer={pose.height_layer} descent={pose.descent} wreck={pose.wreck}
      friendly disabled={disabled} uncertain={heightUncertain} onTarget={onHeight}/>
  </section>;
}
