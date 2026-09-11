import { useEffect, useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import { acceptSnapshot, SCENARIO_ID } from "./model";
import type { TacticalControlInput, TacticalSnapshot, TacticalView } from "./model";
import { TacticalViewport } from "./TacticalViewport";
import { TacticalControls } from "./TacticalControls";
import { RealtimePanel } from "./RealtimePanel";
import type { PreparedLaunch } from './preparation';
import { reconcileAdvance, reconcileStep, stepTicket } from "./control";
import type { StepTicket } from "./control";

export function TacticalPanel({ transport, instance, active = true, onBusy, preparedLaunch, onPreparedClose }: {
  transport: BridgeTransport; instance: string; active?: boolean; onBusy?: (busy: boolean) => void;
  preparedLaunch?:PreparedLaunch|null; onPreparedClose?:()=>void;
}) {
  const [view, setView] = useState<TacticalView | null>(null);
  const [experimental, setExperimental] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState<StepTicket | null>(null);
  const [receipt, setReceipt] = useState("");
  const pending = useRef(false), mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  async function run(method: "tactical.create" | "tactical.inspect" | "tactical.close" | "tactical.step" | "tactical.advance" | "tactical.pause", input?: TacticalControlInput, stepCount = 1) {
    const advancing = method === "tactical.advance", controlling = method === "tactical.step" || advancing;
    if (pending.current || !active || controlling && (uncertain || !input)) return;
    pending.current = true; setBusy(true); setError("");
    let ticket: StepTicket | null = null, sent = false;
    const params = method === "tactical.create" ? { scenario_id: SCENARIO_ID }
      : method === "tactical.close" || method === "tactical.pause" ? { scene_id: view!.snapshot.scene_id }
      : controlling ? { scene_id: input!.scene_id, input: input!, ...(advancing ? { step_count: stepCount } : {}) }
      : { scene_id: view?.snapshot.scene_id ?? null, known_static_sha256: view?.snapshot.static_sha256 ?? null };
    try {
      if (controlling) { ticket = { ...await stepTicket(input!), stepCount }; setReceipt(""); }
      sent = true;
      const result = await transport.tactical<TacticalSnapshot | { closed: true; scene_id: string }>({
        backend_instance_id: instance, method, params, session_id: null, expected_revision: null,
      });
      if (!mounted.current) return;
      if ("closed" in result) {
        if (result.scene_id !== view?.snapshot.scene_id) throw new Error("释放结果的场景身份不匹配。");
        setView(null); setSelected(null); setUncertain(null); setReceipt("");
      } else {
        const next = acceptSnapshot(view, result, instance);
        if (ticket) {
          const confirmed = advancing ? reconcileAdvance(ticket, result) === "accepted" : reconcileStep(ticket, result) === "executed";
          if (!confirmed) throw new Error("推进确认与请求不匹配，请读取场景状态核对。");
          setReceipt(advancing ? "已开始按秒推进，可以随时停止。" : `输入 ${ticket.input.input_seq} 已执行，推进至第 ${result.fixed_step} 步。`);
        } else if (uncertain) {
          const outcome = (uncertain.stepCount ?? 1) > 1 ? reconcileAdvance(uncertain, result) : reconcileStep(uncertain, result);
          if (outcome === "unknown") setError("当前状态无法确认上次指令，请释放并重建测试场景；不会自动重复推进。");
          else {
            setUncertain(null);
            setReceipt(outcome === "not_executed" ? "已回读确认：上次输入未执行，可以调整后重新推进。" : outcome === "executed" ? `已回读确认：输入 ${uncertain.input.input_seq} 执行过一次，无需重发。` : `已回读确认：输入 ${uncertain.input.input_seq} 已接收，无需重发。`);
          }
        }
        setView(next);
        if (!view) { setSelected(result.static?.ships.find(s => s.side_id === "side.blue")?.id ?? null); setUncertain(null); setReceipt(""); }
      }
    } catch (failure) {
      if (mounted.current) {
        const normalized = normalizeHostFailure(failure);
        if (sent && ticket) setUncertain(ticket);
        if (normalized.code === "tactical.scene_missing") { setView(null); setUncertain(null); }
        setError(failure instanceof Error ? failure.message : normalized.message);
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  // Poll only presentation. Background authority advances even without these reads.
  useEffect(() => {
    if (!active || busy || uncertain || error || view?.snapshot.advance_state?.status !== "running") return;
    const timer = window.setTimeout(() => void run("tactical.inspect"), 150);
    return () => window.clearTimeout(timer);
  }, [active, busy, uncertain, error, view]);
  useEffect(() => {
    if (active && view) void run("tactical.inspect");
  }, [active]);
  const ship = view?.geometry.ships.find(s => s.id === selected);
  const pose = view?.snapshot.ships.find(s => s.id === selected);
  if (experimental || preparedLaunch) return <RealtimePanel key={preparedLaunch?.launch_id??'technical'} transport={transport} instance={instance} active={active} onBusy={onBusy}
    preparedLaunch={preparedLaunch} onClose={()=>{setExperimental(false);if(preparedLaunch)onPreparedClose?.();}} />;
  return <section className="panel tactical-panel" aria-label="两舰战术视角">
    <h2>两舰试航场景</h2>
    <p className="editor-note">选择车钟后按秒试航，观察蓝方旗舰加速与转向；到达指定时长后自动暂停。</p>
    <div className="editor-row">
      <button disabled={busy || view !== null || !active} onClick={() => setExperimental(true)}>实时试航（实验）</button>
      <button disabled={busy || view !== null} onClick={() => void run("tactical.create")}>建立两舰场景</button>
      <button disabled={busy} onClick={() => void run("tactical.inspect")}>读取场景状态</button>
      <button disabled={busy || !view} onClick={() => void run("tactical.close")}>释放测试场景</button>
      {view?.snapshot.advance_state?.status === "running" && <button disabled={busy} onClick={() => void run("tactical.pause")}>停止推进</button>}
    </div>
    {busy && <p role="status">正在处理场景…</p>}
    {error && <p role="alert" className="editor-error">{error}</p>}
    {receipt && <p role="status">{receipt}</p>}
    {view?.snapshot.advance_state && <p role="status">{({ running: "正在推进", completed: "推进完成", stopped: "已停止推进", failed: "推进中断" })[view.snapshot.advance_state.status]}：
      {(view.snapshot.advance_state.executed_steps / 60).toFixed(2)} / {view.snapshot.advance_state.step_count / 60} 秒
      {view.snapshot.advance_state.error && ` · ${view.snapshot.advance_state.error}`}</p>}
    {view && <>
      <p className="editor-summary">{view.snapshot.paused ? "已暂停" : "运行中"} · 第 {view.snapshot.fixed_step} 步 · {view.snapshot.time_s.toFixed(3)} 秒 · 完整推进安全规则</p>
      <div className="tactical-columns">
        <TacticalViewport key={`${view.snapshot.scene_id}.${view.snapshot.static_sha256}`} view={view} active={active} selected={selected} onSelect={setSelected} />
        <aside className="tactical-inspector" aria-label="舰艇检查">
          <h3>场内舰艇</h3>
          <div className="tactical-ship-list">{view.geometry.ships.map(s => <button key={s.id} aria-pressed={selected === s.id}
            className={s.side_id === "side.blue" ? "ship-blue" : "ship-red"} onClick={() => setSelected(s.id)}>{s.name}</button>)}</div>
          {ship && pose ? <>
            <h3>{ship.name}</h3>
            <p>{ship.side_id === "side.blue" ? "蓝方" : "红方"} · 仅检查布局与状态</p>
            <dl className="tactical-stats">
              <dt>速度</dt><dd>{pose.speed_mps.toFixed(2)} m/s</dd>
              <dt>船壳完整度</dt><dd>{(pose.hull_integrity * 100).toFixed(1)}%</dd>
              <dt>位置 X / Y</dt><dd>{pose.position_m.map(n => n.toFixed(1)).join(" / ")} m</dd>
              <dt>朝向</dt><dd>{((pose.heading_rad * 180 / Math.PI % 360 + 360) % 360).toFixed(1)}°</dd>
              <dt>转向速度</dt><dd>{(pose.yaw_rate_radps * 180 / Math.PI).toFixed(2)} °/s</dd>
              <dt>高度</dt><dd>{{ rain: "雨层", cloud: "云层", upper: "上层" }[pose.height_layer] ?? pose.height_layer}</dd>
              <dt>布局</dt><dd>{ship.decks.length} 层 · {ship.modules.length} 个部件</dd>
            </dl>
            <p className="muted">0° 船艏向上，正角度逆时针。金色为顶挂占用，紫色为部件本体投影。</p>
            <details><summary>部件耐久</summary><ul className="tactical-modules">{ship.modules.map(m => {
              const health = pose.modules.find(p => p.id === m.id)?.durability;
              return <li key={m.id}><span>{m.name}</span><span>{health?.toFixed(0) ?? "—"} / {m.max_durability}</span></li>;
            })}</ul></details>
          </> : <p className="muted">点击舰体或列表查看状态。</p>}
        </aside>
      </div>
      <TacticalControls key={view.snapshot.scene_id} view={view} active={active} busy={busy} uncertain={uncertain !== null}
        onStep={(input, count) => void run(count === 1 ? "tactical.step" : "tactical.advance", input, count)} />
    </>}
  </section>;
}
