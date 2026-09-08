import { useState } from "react";
import type { TacticalControlInput, TacticalView } from "./model";
import { CHANNELS, makeStepInput, NOTCHES } from "./control";
import type { HelmDraft, Notch } from "./control";

const reasons: Record<string, string> = { crew_limit: "船员过载限制", structure_limit: "结构负荷限制" };
export function TacticalControls({ view, active, busy, uncertain, onStep }: {
  view: TacticalView; active: boolean; busy: boolean; uncertain: boolean; onStep: (input: TacticalControlInput, count: number) => void;
}) {
  const [draft, setDraft] = useState<HelmDraft>({ notch: "stop", direction: "forward", brake: false });
  const [turnPercent, setTurnPercent] = useState(25), [error, setError] = useState("");
  const [seconds, setSeconds] = useState(5);
  const state = view.snapshot.control_state;
  if (!state) return <p className="editor-note">当前后台未启用单步操纵，请使用最新桌面版本。</p>;
  const locked = !active || busy || uncertain || !state.available || !view.snapshot.paused;
  function step(turn: -1 | 0 | 1, count = 1) {
    if (locked) return;
    try { const input = makeStepInput(view.snapshot, draft, turn, turnPercent); setError(""); onStep(input, count); }
    catch (failure) { setError((failure as Error).message); }
  }
  return <section className="tactical-controls" aria-label="蓝方旗舰单步操纵">
    <h3>蓝方旗舰 · 试航操纵</h3>
    <p>先选择车钟，再推进数秒观察运动。期间持续使用当前指令，完成后自动暂停；发动机逐步响应。</p>
    <p className="muted">时长指模拟时间，计算可能更久。这是独立试航参数样例，尚非正式舰艇平衡数值。</p>
    <fieldset disabled={locked} onKeyDown={e => { if (e.repeat && e.target instanceof HTMLButtonElement) e.preventDefault(); }}>
      <div className="editor-row">
        <label>推进方向<select aria-label="推进方向" value={draft.direction} disabled={draft.brake} onChange={e => setDraft({ ...draft, direction: e.target.value as HelmDraft["direction"] })}><option value="forward">前进</option><option value="reverse">倒车</option></select></label>
        <label>车钟<select aria-label="车钟" value={draft.notch} disabled={draft.brake} onChange={e => setDraft({ ...draft, notch: e.target.value as Notch })}>{Object.entries(NOTCHES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>本步转向推力<select aria-label="本步转向推力" value={turnPercent} disabled={draft.brake} onChange={e => setTurnPercent(Number(e.target.value))}>{[2, 5, 10, 25, 50, 75, 100].map(n => <option key={n} value={n}>{n}%</option>)}</select></label>
      </div>
      <label className="tactical-brake"><input type="checkbox" checked={draft.brake} onChange={e => setDraft({ ...draft, brake: e.target.checked })} />自动线性制动</label>
      <div className="editor-row">
        <label>推进时长<select aria-label="推进时长" value={seconds} onChange={e => setSeconds(Number(e.target.value))}>{[1, 5, 10].map(n => <option key={n} value={n}>{n} 秒</option>)}</select></label>
        <button onClick={() => step(0, seconds * 60)}>推进 {seconds} 秒</button>
        <button disabled={draft.brake} onClick={() => step(-1, seconds * 60)}>左转 {seconds} 秒</button>
        <button disabled={draft.brake} onClick={() => step(1, seconds * 60)}>右转 {seconds} 秒</button>
      </div>
      <div className="editor-row">
        <button onClick={() => step(0)}>单步推进</button>
        <button disabled={draft.brake} onClick={() => step(-1)}>左转单步</button>
        <button disabled={draft.brake} onClick={() => step(1)}>右转单步</button>
      </div>
    </fieldset>
    <p className="muted">单步按钮每次仅推进 1/60 秒，供细查使用。普通推进不请求转向；制动时自动选择反向推力，不附带转向。选中红方仅改变检查对象。</p>
    {state.unavailable_reason && <p role="alert">{state.unavailable_reason}</p>}
    {uncertain && <p role="alert">上次推进结果尚未确认，请先点击“读取场景状态”。不会自动重发指令。</p>}
    {error && <p role="alert">{error}</p>}
    <div className="editor-summary"><span>已确认输入：{state.last_input_seq}</span><span>燃料：{state.fuel_units.toFixed(1)}{state.fuel_policy === "gaotian.tactical-fuel/no-propulsion-burn/v1" ? "（战术推进不消耗）" : ""}</span>
      <span>{state.requested_control?.automatic_brake ? "上一步：自动制动" : state.last_input_seq ? "上一步：车钟 / 转向指令" : "尚未执行操纵"}</span></div>
    {state.missing_channels.length > 0 && <p role="status">当前舰艇没有可提供以下请求的推进器：{state.missing_channels.map(c => CHANNELS[c] ?? c).join("、")}。</p>}
    <details><summary>推进响应与安全限制</summary>
      <p>实际输出受发动机响应、安全限制及换向互锁影响；停车不会瞬间消除已有速度或发动机输出。</p>
      <div className="propulsion-tables"><table><thead><tr><th>方向</th><th>有效请求</th><th>安全上限</th></tr></thead><tbody>{state.channels.map(c => <tr key={c.channel}><td>{CHANNELS[c.channel] ?? c.channel}</td><td>{c.requested_percent}%</td><td>{c.safety_ceiling_percent}% {c.safety_reasons.map(r => reasons[r] ?? r).join("、")}</td></tr>)}</tbody></table>
        <table><thead><tr><th>推进器</th><th>目标</th><th>当前实际</th></tr></thead><tbody>{state.engines.map(e => <tr key={e.id}><td>{view.geometry.ships.find(s => s.id === state.direct_ship_id)?.modules.find(m => m.id === e.id)?.name ?? e.id}</td><td>{e.target_percent}%</td><td>{e.actual_percent}%</td></tr>)}</tbody></table></div>
    </details>
  </section>;
}
