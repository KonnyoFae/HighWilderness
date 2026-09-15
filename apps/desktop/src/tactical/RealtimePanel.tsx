import { useEffect, useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import type { TacticalRequest, TacticalView } from "./model";
import { SCENARIO_ID } from "./model";
import { makeHelmControl, NOTCHES } from "./control";
import type { HelmDraft, Notch } from "./control";
import { acceptRealtime, pauseLabel, receiptLabel } from "./realtime";
import type { RealtimeEnvelope } from "./realtime";
import { SettlementPanel } from "./SettlementPanel";
import type { SettlementEnvelope, SettlementLibrary } from "./settlement";
import { fuelTankName } from './settlement';
import { TacticalViewport } from "./TacticalViewport";
import { GunControlPanel, commonGunValue } from './GunControlPanel';
import type { GunIntent } from "./gunnery";
import type { Point } from "../editor/viewport";
import type { PreparedLaunch } from './preparation';
import { ammunitionName } from './ammunition';
import { DamageControlPanel } from './DamageControlPanel';
import type { DamageControlIntent } from './DamageControlPanel';
import { LiftReserve } from '../LiftReserve';
import { HeightPanel } from './HeightPanel';
import type { HeightLayer } from './layers';

export function RealtimePanel({ transport, instance, active, onBusy, onClose, preparedLaunch, battleLayout = false }: {
  transport: BridgeTransport; instance: string; active: boolean; onBusy?: (busy: boolean) => void; onClose: () => void;
  preparedLaunch?:PreparedLaunch|null;
  battleLayout?: boolean;
}) {
  const [view, setView] = useState<TacticalView | null>(null), [state, setState] = useState<RealtimeEnvelope | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [receipt, setReceipt] = useState("");
  const [uncertain, setUncertain] = useState<number | null>(null), [selected, setSelected] = useState<string | null>("ship.web.blue");
  const [draft, setDraft] = useState<HelmDraft>({ notch: "stop", direction: "forward", brake: false });
  const latest = useRef<{ state: RealtimeEnvelope | null; view: TacticalView | null }>({ state: null, view: null });
  const pending = useRef<Promise<unknown> | null>(null), acting = useRef(false), mounted = useRef(true);
  const ack = useRef({ inputs: [] as number[], event: 0 });
  const unknown = useRef<number | null>(null);
  const [groupId, setGroupId] = useState<string | null>(null);
  const groupRef = useRef(groupId); groupRef.current = groupId;
  const [weaponId, setWeaponId] = useState<string | null>(null);
  const [gunUncertain, setGunUncertain] = useState<number | null>(null);
  const unknownGun = useRef<number | null>(null), pointerAim = useRef<Point | null>(null);
  const queuedFire = useRef<{ weapon: string; point: Point } | null>(null);
  const [storeError, setStoreError] = useState("");
  const [library, setLibrary] = useState<SettlementLibrary | null>(null);
  const [historyResult, setHistoryResult] = useState<SettlementEnvelope | null>(null);
  const launchKey = useRef<{ instanceId: string; revision: number; id: string } | null>(null);
  const weaponRef = useRef(weaponId); weaponRef.current = weaponId;
  const attemptedEntry=useRef(false);
  const [entryUncertain,setEntryUncertain]=useState(false);
  const [damageUncertain,setDamageUncertain]=useState(false);
  const [inspectorTab, setInspectorTab] = useState<"ship" | "weapons" | "damage">("ship");
  const unknownDamage=useRef(false);
  const [heightUncertain, setHeightUncertain] = useState(false);
  const unknownHeight = useRef(false);
  useEffect(()=>{if(active&&preparedLaunch&&!attemptedEntry.current){attemptedEntry.current=true;void action('create');}},[active,preparedLaunch]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { onBusy?.(busy); return () => onBusy?.(false); }, [busy, onBusy]);
  function call<T>(method: TacticalRequest["method"], params: Record<string, unknown>) {
    return transport.tactical<T>({ backend_instance_id: instance, method, params, session_id: null, expected_revision: null });
  }
  async function refreshLibrary() {
    const value = await call<SettlementLibrary>("tactical.realtime.settlements", {});
    if (mounted.current) setLibrary(value);
  }
  useEffect(() => {
    let stopped = false;
    if (active) void refreshLibrary().catch(e => { if (!stopped && mounted.current) setStoreError(normalizeHostFailure(e).message); });
    return () => { stopped = true; };
  }, [active, state?.settlement?.result.settlement_id, state?.settlement?.saved]);
  useEffect(() => { setHistoryResult(null); }, [state?.settlement?.result.settlement_id]);
  async function storedAction(kind: "refresh" | "inspect" | "save" | "deploy", id = "", revision = 0) {
    if (acting.current || !active) return;
    acting.current = true; setBusy(true); setStoreError("");
    try {
      await pending.current;
      if (kind === "refresh") { await refreshLibrary(); return; }
      if (kind === "deploy") {
        if (preparedLaunch) throw new Error("自建舰需要返回战前准备后进入下一场交战。");
        if (launchKey.current?.instanceId !== id || launchKey.current.revision !== revision)
          launchKey.current = { instanceId: id, revision, id: `launch.${crypto.randomUUID()}` };
        const next = await call<RealtimeEnvelope>("tactical.realtime.deploy", { instance_id: id, revision, launch_id: launchKey.current.id });
        latest.current = { state: null, view: null };
        ack.current = { inputs: [], event: 0 };
        unknown.current = unknownGun.current = null; setUncertain(null); setGunUncertain(null);
        pointerAim.current = queuedFire.current = null; setWeaponId(null); weaponRef.current = null; setGroupId(null); groupRef.current = null;
        setHistoryResult(null); setReceipt(""); setDraft({ notch: "stop", direction: "forward", brake: false });
        accept(next);
      } else {
        const result = await call<SettlementEnvelope>(kind === "save" ? "tactical.realtime.save" : "tactical.realtime.settlement", { settlement_id: id });
        setHistoryResult(result);
        const current = latest.current.state;
        if (kind === "save" && current?.settlement?.result.settlement_id === id && result.saved) {
          // The save receipt is authoritative even if the following scene read
          // fails. Saving unrelated history must never unlock the current scene.
          const saved = { ...current, settlement: result };
          latest.current = { ...latest.current, state: saved }; setState(saved);
        }
        if (kind === "save" && latest.current.state) await read();
      }
      await refreshLibrary();
    } catch (e) { if (mounted.current) setStoreError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current) setBusy(false); }
  }
  function accept(next: RealtimeEnvelope) {
    if (!mounted.current) return;
    const accepted = acceptRealtime(latest.current.state, latest.current.view, next, instance);
    if(!latest.current.state)setSelected(next.direct_ship_id);
    latest.current = { state: next, view: accepted }; setState(next); setView(accepted);
    ack.current = { inputs: next.receipts.filter(r => r.status !== "accepted").map(r => r.sequence),
      event: next.events.at(-1)?.sequence ?? next.status.acknowledged_event_sequence };
    const last = next.receipts.at(-1);
    if (last) setReceipt(`操纵 ${last.sequence}：${receiptLabel[last.status] ?? last.status}`);
    if (unknown.current !== null) {
      const found = next.receipts.find(r => r.sequence === unknown.current);
      if (found || next.status.highest_input_sequence < unknown.current) {
        if (!found) setReceipt(`操纵 ${unknown.current} 未被接受，可重新发令。`);
        unknown.current = null; setUncertain(null);
      }
    }
    setError(next.error ?? "");
    if (unknownHeight.current && next.view.height_commands) {
      unknownHeight.current = false; setHeightUncertain(false);
    }
    if (unknownDamage.current && next.view.gunnery?.damage_control) {
      unknownDamage.current=false;setDamageUncertain(false);
    }
    if (unknownGun.current !== null && next.view.gunnery) {
      // A fresh read resolves a lost reply; never resubmit a click automatically.
      unknownGun.current = null; setGunUncertain(null);
    }
  }
  async function read() {
    const current = latest.current;
    if (!current.state) return;
    accept(await call<RealtimeEnvelope>("tactical.realtime.read", { scene_id: current.state.status.epoch,
      known_static_sha256: current.view?.snapshot.static_sha256 ?? null, ack_inputs: ack.current.inputs, ack_events: ack.current.event }));
  }
  useEffect(() => {
    if (!active) return;
    let stopped = false;
    const poll = () => {
      if (stopped || acting.current || pending.current || !latest.current.state) return;
      const task = read().catch(e => { if (mounted.current) setError(normalizeHostFailure(e).message); });
      pending.current = task; void task.finally(() => { if (pending.current === task) pending.current = null; });
    };
    poll(); const timer = window.setInterval(poll, 67);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [active]);

  async function action(kind: "create" | "read" | "resume" | "pause" | "control" | "withdraw" | "close", turn: -1 | 0 | 1 = 0) {
    if (acting.current || !active) return;
    acting.current = true; setBusy(true); setError("");
    try {
      await pending.current;
      if (kind === "create") {
        setHistoryResult(null);setEntryUncertain(!!preparedLaunch);
        try {
          accept(await call<RealtimeEnvelope>(preparedLaunch?'tactical.realtime.deploy_prepared':'tactical.realtime.create',preparedLaunch??{scenario_id:SCENARIO_ID}));
          setEntryUncertain(false);
        } catch(e) {
          if(preparedLaunch){
            const confirmation=await call<{scene:RealtimeEnvelope|null}>('tactical.realtime.prepared_entry',{launch_id:preparedLaunch.launch_id});
            if(confirmation.scene)accept(confirmation.scene);
            setEntryUncertain(false);
          }
          throw e;
        }
        return;
      }
      if (!latest.current.state) { if (kind === "close") onClose(); return; }
      if (kind === "read") { await read(); return; }
      const scene_id = latest.current.state.status.epoch;
      if (kind === "control") {
        if (unknown.current !== null) return;
        await read(); // fresh target; never retarget an input after sending it
        const current = latest.current.state!;
        if (!current.status.running || !current.available) throw new Error("请先开始试航，并确认旗舰仍可操纵。");
        const sequence = current.status.highest_input_sequence + 1;
        unknown.current = sequence; setUncertain(sequence);
        const input = { interface: "gaotian.tactical-scheduled-control/e3a-v1alpha1", epoch: scene_id,
          generation: current.status.generation, sequence, ship_id: current.direct_ship_id,
          target_step: current.status.fixed_step + 2, control: makeHelmControl(draft, turn) };
        const result = await call<{ status: string }>("tactical.realtime.control", { scene_id, input });
        unknown.current = null; setUncertain(null);
        setReceipt(`操纵 ${sequence}：${receiptLabel[result.status] ?? result.status}`);
        await read();
      } else if (kind === "close") {
        await call("tactical.realtime.close", { scene_id }); onClose();
      } else accept(await call<RealtimeEnvelope>(`tactical.realtime.${kind}`, { scene_id }));
    } catch (e) { if (mounted.current) setError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current) setBusy(false); }
  }
  async function sendHeight(target: HeightLayer | null) {
    if (acting.current || !active || unknownHeight.current || !selected) return;
    const shipId = selected;
    acting.current = true; setBusy(true); setError('');
    try {
      await pending.current;
      const current = latest.current.state;
      if (!current?.status.running || !current.available || !current.view.height_commands || current.view.gunnery?.ending) return;
      unknownHeight.current = true; setHeightUncertain(true);
      accept(await call<RealtimeEnvelope>('tactical.realtime.height', { scene_id: current.status.epoch,
        input: { epoch: current.status.epoch, generation: current.status.generation,
          sequence: current.view.height_commands.command_sequence + 1, ship_id: shipId, target_layer: target } }));
    } catch (e) { if (mounted.current) setError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current) setBusy(false); }
  }
  async function sendDamageControl(intent:DamageControlIntent, shipId=latest.current.state?.direct_ship_id) {
    if(acting.current||!active||unknownDamage.current)return;
    acting.current=true;setBusy(true);setError('');
    try {
      await pending.current;
      const current=latest.current.state;
      if(!current?.status.running||!current.available||!current.view.gunnery?.damage_control||!shipId)return;
      unknownDamage.current=true;setDamageUncertain(true);
      accept(await call<RealtimeEnvelope>('tactical.realtime.damage_control',{scene_id:current.status.epoch,
        input:{epoch:current.status.epoch,generation:current.status.generation,
          sequence:current.view.gunnery.damage_control.command_sequence+1,ship_id:shipId,...intent}}));
    } catch(e){if(mounted.current)setError(normalizeHostFailure(e).message);}
    finally{acting.current=false;if(mounted.current)setBusy(false);}
  }
  async function sendGun(intent: GunIntent) {
    if (acting.current || !active || unknownGun.current !== null || !weaponRef.current) return;
    const selector = groupRef.current ? {group_id: groupRef.current} : {weapon_id: weaponRef.current};
    pointerAim.current = null;
    if (intent.kind !== 'aim') queuedFire.current = null;
    acting.current = true;
    if (intent.kind !== "aim") setBusy(true);
    try {
      await pending.current;
      const current = latest.current.state;
      if (!current?.status.running || !current.available || !current.view.gunnery) return;
      const sequence = current.view.gunnery.command_sequence+1;
      unknownGun.current = sequence;
      if (intent.kind !== "aim") setGunUncertain(sequence);
      accept(await call<RealtimeEnvelope>("tactical.realtime.gun", { scene_id: current.status.epoch,
        input: { epoch: current.status.epoch, generation: current.status.generation, sequence,
          ...selector, ...intent } }));
      unknownGun.current = null; setGunUncertain(null);
    } catch (e) { if (mounted.current) setError(normalizeHostFailure(e).message); }
    finally { acting.current = false; if (mounted.current && intent.kind !== "aim") setBusy(false); }
  }
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      const point = pointerAim.current;
      const gun = latest.current.state?.view.gunnery?.weapons.find(g => g.module_id === weaponRef.current && g.ship_id === latest.current.state?.direct_ship_id);
      if (!latest.current.state?.status.running || gun?.mode !== "manual") queuedFire.current = null;
      const fire = queuedFire.current;
      if (fire && !acting.current && unknownGun.current === null) {
        queuedFire.current = null;
        if (fire.weapon === (groupRef.current ?? weaponRef.current)) void sendGun({ kind: "fire", arguments: { point: [fire.point.x, fire.point.y] } });
        return;
      }
      if (point && gun?.mode === "manual" && !acting.current) {
        pointerAim.current = null;
        void sendGun({ kind: "aim", arguments: { point: [point.x, point.y] } });
      }
    }, 100);
    return () => { window.clearInterval(timer); pointerAim.current = null; queuedFire.current = null; };
  }, [active]);
  useEffect(() => {
    const hide = () => { if (document.hidden && latest.current.state?.status.running) void action("pause"); };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  });
  const pose = view?.snapshot.ships.find(s => s.id === selected);
  const friendlySelected = !!selected && !!view && view.geometry.ships.find(s => s.id === selected)?.side_id ===
    view.geometry.ships.find(s => s.id === state?.direct_ship_id)?.side_id;
  const durability = view?.geometry.ships.find(s=>s.id===selected)?.structural_durability;
  const ownGuns = view?.snapshot.gunnery?.weapons.filter(g => g.ship_id === state?.direct_ship_id) ?? [];
  const groups = view?.snapshot.gunnery?.groups?.filter(g => g.ship_id === state?.direct_ship_id) ?? [];
  const group = groups.find(g => g.group_id === groupId);
  const controlledGuns = ownGuns.filter(g => group ? group.weapon_ids.includes(g.module_id) : g.module_id === weaponId);
  const gunMode = commonGunValue(controlledGuns,g=>g.mode);
  const gunLayer = commonGunValue(controlledGuns,g=>g.effective_layer);
  useEffect(() => {
    if (!weaponId || ownGuns.some(g=>g.module_id===weaponId) && (!groupId || group)) return;
    groupRef.current = null; setGroupId(null);
    weaponRef.current = null; setWeaponId(null);
    pointerAim.current = queuedFire.current = null;
  }, [state?.status.epoch, weaponId, groupId, groups, ownGuns]);
  const chooseWeapon = (id: string) => {
    pointerAim.current = null; queuedFire.current = null; groupRef.current = null; setGroupId(null); weaponRef.current = id || null; setWeaponId(id || null);
    if (battleLayout && state) { setSelected(state.direct_ship_id); setInspectorTab("weapons"); }
  };
  const chooseGroup = (id: string) => {
    const value = groups.find(g=>g.group_id===id);
    if (!value) return;
    pointerAim.current = queuedFire.current = null;
    groupRef.current = id; setGroupId(id); weaponRef.current = value.weapon_ids[0]; setWeaponId(weaponRef.current);
    if (battleLayout && state) { setSelected(state.direct_ship_id); setInspectorTab('weapons'); }
  };
  const chooseCanvasWeapon = (id: string) => {
    const value = groups.find(g=>g.weapon_ids.includes(id));
    if (value) chooseGroup(value.group_id); else chooseWeapon(id);
  };
  if (battleLayout) return <section className="panel tactical-panel battle-workspace" aria-label="战术战场">
    <header className="battle-toolbar">
      <div><p className="eyebrow">TACTICAL OPERATIONS</p><h2>{view ? "舰队交战" : "正在部署舰队"}</h2></div>
      <div className="editor-row">
        {!state && <button disabled={busy || !active} onClick={() => void action("create")}>重试进入战场</button>}
        <button disabled={busy || !state || state.status.running || !!state.settlement || !state.available} onClick={() => void action("resume")}>开始交战</button>
        <button disabled={busy || !state?.status.running} onClick={() => void action("pause")}>暂停交战</button>
        <button className="secondary" disabled={busy || !state || !!state.settlement} onClick={() => void action("withdraw")}>结束本场交战</button>
        {!state && <button disabled={busy || entryUncertain} onClick={() => void action("close")}>返回战前准备</button>}
      </div>
    </header>
    <div className="battle-notices">
    {entryUncertain && !busy && <p role="alert">入战结果尚未确认，请重试进入战场；会继续查询同一次入战。</p>}
    {error && <p role="alert">{error}</p>}
    {storeError && <p role="alert">{storeError}</p>}
    {receipt && <p role="status">{receipt}</p>}
    {uncertain !== null && <p role="alert">操纵 {uncertain} 的结果尚未确认，正在查询；不会自动重发。</p>}
    {gunUncertain !== null && <p role="alert">火炮命令 {gunUncertain} 尚待确认，正在查询；不会自动重发射击。</p>}
    {state && <p className="editor-summary">{state.status.running ? "运行中" : pauseLabel[state.status.pause_reason ?? ""] ?? "已暂停"} · 第 {state.status.fixed_step} 步 · {(state.status.fixed_step / 60).toFixed(2)} 秒</p>}
    {state && !state.available && <p role="alert">旗舰已失去操纵权。{state.loss_reason === "direct_ship_falling" ? "舰艇正在坠落。" : "后续操纵已取消。"}</p>}

    </div>
    {view && state && !state.settlement && <div className="battle-grid">
      <div className="battle-field">
        <nav className="battle-fleet" aria-label="战场舰艇">{view.geometry.ships.map(s => <button key={s.id}
          aria-pressed={selected === s.id} className={s.side_id === view.geometry.ships.find(v => v.id === state.direct_ship_id)?.side_id ? "ship-blue" : "ship-red"}
          onClick={() => { setSelected(s.id); setInspectorTab("ship"); }}>{s.name}{s.id === state.direct_ship_id ? " · 旗舰" : ""}{view.snapshot.ships.find(p=>p.id===s.id)?.wreck ? ' · 残骸' : view.snapshot.ships.find(p=>p.id===s.id)?.descent ? ' · 下坠' : ''}</button>)}</nav>
        <TacticalViewport view={view} active={active} selected={selected} onSelect={setSelected} compact
      gunControl={view.snapshot.gunnery && state ? { ownShipId: state.direct_ship_id, weaponId, weaponIds: controlledGuns.map(g=>g.module_id), selectionKey: groupId ?? weaponId ?? "", mode: gunMode ?? "auto", attackLayer: gunLayer,
        enabled: active && state.status.running && state.available && !busy && gunUncertain === null, canAim: !!gunMode && !!gunLayer,
        onWeapon: chooseCanvasWeapon, onTarget: (shipId, moduleId) => { void sendGun({ kind: "target", arguments: { ship_id: shipId, module_id: moduleId } }); },
        onAim: point => { pointerAim.current = point; }, onFire: point => { pointerAim.current = null;
          if (acting.current && weaponRef.current) queuedFire.current = { weapon: groupRef.current ?? weaponRef.current, point };
          else void sendGun({ kind: "fire", arguments: { point: [point.x, point.y] } }); },
        onLeave: () => { pointerAim.current = null; queuedFire.current = null; },
      } : undefined} />
        <section className="battle-helm" aria-label="旗舰操纵"><h3>旗舰操纵 · {view.geometry.ships.find(s => s.id === state.direct_ship_id)?.name}</h3>
    <fieldset className="realtime-helm" disabled={busy || !active || !state?.status.running || !state.available || uncertain !== null}>
      <div className="editor-row">
      <label>实时车钟<select aria-label="实时车钟" value={draft.notch} onChange={e => setDraft({ ...draft, notch: e.target.value as Notch })}>{Object.entries(NOTCHES).map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>实时方向<select aria-label="实时方向" value={draft.direction} onChange={e => setDraft({ ...draft, direction: e.target.value as HelmDraft["direction"] })}><option value="forward">前进</option><option value="reverse">倒车</option></select></label>
      <label><input type="checkbox" checked={draft.brake} onChange={e => setDraft({ ...draft, brake: e.target.checked })} />实时自动制动</label>
      </div>
      <div className="editor-row"><button onClick={() => void action("control")}>执行车钟 / 停止转向</button><button disabled={draft.brake} onClick={() => void action("control", -1)}>持续左转</button><button disabled={draft.brake} onClick={() => void action("control", 1)}>持续右转</button></div>
    </fieldset>

        </section>
      </div>
      <aside className="battle-inspector" aria-label="所选舰艇">
        <h3>{view.geometry.ships.find(s => s.id === selected)?.name ?? "选择舰艇"}</h3>
        <nav className="battle-tabs" aria-label="舰艇面板">
          <button aria-pressed={inspectorTab === "ship"} onClick={() => setInspectorTab("ship")}>舰况</button>
          <button aria-pressed={inspectorTab === "weapons"} onClick={() => setInspectorTab("weapons")}>火炮</button>
          <button aria-pressed={inspectorTab === "damage"} onClick={() => setInspectorTab("damage")}>损管</button>
        </nav>
        {inspectorTab === "ship" && pose && <div className="battle-ship-state">
          <LiftReserve value={pose.lift_reserve} />
          <HeightPanel value={pose.height_navigation} actualLayer={pose.height_layer}
            descent={pose.descent} wreck={pose.wreck}
            friendly={view.geometry.ships.find(s => s.id === selected)?.side_id === view.geometry.ships.find(s => s.id === state.direct_ship_id)?.side_id}
            disabled={busy || !active || !state.status.running || !state.available || !!view.snapshot.gunnery?.ending}
            uncertain={heightUncertain} onTarget={target => void sendHeight(target)} />
          <p className="muted">{selected === state.direct_ship_id ? "直控旗舰" : "所选舰艇状态"}</p>
          <dl className="tactical-stats">
            <dt>航速</dt><dd>{pose.speed_mps.toFixed(1)} m/s</dd>
            <dt>船壳</dt><dd>{(pose.hull_integrity * 100).toFixed(1)}%</dd>
            <dt>高度层</dt><dd>{{upper: "上层", cloud: "云层", rain: "雨层"}[pose.height_layer] ?? pose.height_layer}</dd>
            <dt>转向速度</dt><dd>{(pose.yaw_rate_radps * 180 / Math.PI).toFixed(1)} °/s</dd>
          </dl>
          {durability && <p>结构耐久 {(pose.hull_integrity * durability.maximum_points).toFixed(0)} / {durability.maximum_points.toFixed(0)}</p>}
          <details><summary>模块状态</summary>{pose.modules.map(m => <p key={m.id}>{view.geometry.ships.find(s => s.id === pose.id)?.modules.find(v => v.id === m.id)?.name ?? m.id}：{m.durability.toFixed(1)}{m.durability <= 0 ? " · 已损毁" : ""}</p>)}</details>
        </div>}
        {selected === state.direct_ship_id ? <>
          {inspectorTab === "weapons" && <div className="battle-weapons">
    {view?.snapshot.gunnery && state && <GunControlPanel view={view} shipId={state.direct_ship_id}
      weaponId={weaponId} groupId={groupId} onWeapon={chooseWeapon} onGroup={chooseGroup}
      disabled={busy || !active || !state.status.running || !state.available || gunUncertain !== null}
      onCommand={intent=>void sendGun(intent)} />}

          </div>}
        </> : inspectorTab === "weapons" && <p>当前火炮操作属于直控旗舰。<button onClick={() => setSelected(state.direct_ship_id)}>选择旗舰</button></p>}
        {inspectorTab === "damage" && (friendlySelected && selected ? <div className="battle-damage">
          <DamageControlPanel view={view} shipId={selected}
            disabled={busy || !active || !state.status.running || !state.available || !!view.snapshot.gunnery?.ending ||
              pose?.physical_status !== 'operational' || pose.command_status !== 'scene_command'}
            uncertain={damageUncertain} onCommand={intent => void sendDamageControl(intent, selected)} />
        </div> : <p>请选择本方舰艇下达损管命令。</p>)}
      </aside>
    </div>}
    {view?.snapshot.gunnery?.ending && <p role="status">交战已结束：{{ victory: "敌方失去作战能力", defeat: "本方失去作战能力", draw: "双方失去作战能力", withdrawal: "主动撤离" }[view.snapshot.gunnery.ending.reason] ?? view.snapshot.gunnery.ending.reason}。已完成有效在装批次，清除在途弹丸；{state?.settlement?.saved ? "战后结果已保存。" : "请在结算页面保存本场结果。"}</p>}

    {(state?.settlement || historyResult || !state) && <SettlementPanel
      current={state?.settlement && (!historyResult || historyResult.result.settlement_id === state.settlement.result.settlement_id) ? state.settlement : historyResult}
      library={library} busy={busy || !active} canDeploy={!state || !!state.settlement?.saved}
      onPrepare={preparedLaunch ? ()=>void action('close') : undefined}
      canPrepare={!entryUncertain && !!state?.settlement?.saved}
      onSave={id => void storedAction("save", id)} onInspect={id => void storedAction("inspect", id)}
      onRefresh={() => void storedAction("refresh")} onDeploy={(id, revision) => void storedAction("deploy", id, revision)} />}

    {view && <details className="battle-records"><summary>战场记录与全舰资源</summary>
    {view?.snapshot.gunnery?.fireproof&&<section aria-label="交战防火与火情"><h3>防火与火情</h3><p>防火仅降低新起火概率；已有火情需要损管灭火。</p>
      {view.snapshot.gunnery.fireproof.map(s=>{const ship=view.geometry.ships.find(v=>v.id===s.ship_id);const fires=view.snapshot.gunnery?.damage_control?.fires.filter(f=>f.ship_id===s.ship_id)??[];
        return <article key={s.ship_id}><h4>{ship?.name??s.ship_id}</h4><p>{s.decks.map(d=>`第 ${d.deck_level} 层：${d.multiplier<1?`起火概率降低 ${((1-d.multiplier)*100).toFixed(0)}%`:'无额外防火'}`).join(' · ')}</p>
          <p>{fires.length?fires.map(f=>`${ship?.modules.find(m=>m.id===f.module_id)?.name??f.module_id} 正在燃烧（强度 ${(f.intensity_units/1000).toFixed(2)}）`).join('、'):'当前无火情'}</p></article>;})}</section>}

    {view?.snapshot.gunnery?.fuel&&<section aria-label="燃料储备"><h3>燃料储备</h3><p>发动机运行不消耗燃料；仅燃料槽耐久归零时损失其中燃料。</p>
      {view.snapshot.gunnery.fuel.map(s=><details key={s.ship_id} open={s.ship_id===state?.direct_ship_id}><summary>{view.geometry.ships.find(v=>v.id===s.ship_id)?.name} · 燃料 {s.total_units.toFixed(2)}</summary>
        {s.tanks.map(t=><p key={t.tank_id}>{fuelTankName(t,Object.fromEntries(view.geometry.ships.find(v=>v.id===s.ship_id)?.modules.map(m=>[m.id,m.name])??[]))}：燃料 {t.quantity_units.toFixed(2)} / {t.capacity_units} · 耐久 {t.durability_points.toFixed(1)} / {t.maximum_points}{t.durability_points<=0?' · 已损毁':''}</p>)}</details>)}</section>}

    {view?.snapshot.gunnery?.damage && <details><summary>命中记录 · {view.snapshot.gunnery.damage.hits} 次</summary>
      {view.snapshot.gunnery.damage.recent.slice(-5).map(hit => <p key={hit.projectile_id}>{view.geometry.ships.find(s => s.id === hit.ship_id)?.name} · 第 {hit.deck_level} 层 · {hit.projectile_type ? ammunitionName(hit.projectile_type)+' · ' : ''}{{ penetrated: "击穿", stopped: "装甲阻挡", ricochet: "跳弹", module: "外部模块命中" }[hit.outcome] ?? hit.outcome}{hit.module_ids.length ? ` · ${hit.module_ids.map(id => view.geometry.ships.find(s => s.id === hit.ship_id)?.modules.find(m => m.id === id)?.name ?? id).join("、")} −${hit.module_damage.toFixed(1)}` : ""}</p>)}
    </details>}

    </details>}
    <p className="battle-stage-note">当前阶段可通过“结束本场交战”进入结算；按距离撤离将在后续阶段接入。</p>
  </section>;
  return <section className="panel tactical-panel" aria-label="实时试航实验">
    <h2>{preparedLaunch?'准备舰船交战':'实时试航（实验）'}</h2>
    <p>{preparedLaunch?'已保存准备的舰船参与本次交战，玩家操纵旗舰，其余友舰自动交战。':'后台持续运行两舰试航。'}操纵保持到下次发令，暂停后需要重新发令；战术推进不消耗燃料。</p>
    <div className="editor-row">
      {!state && <button disabled={busy || !active} onClick={() => void action("create")}>{preparedLaunch?'重试进入准备交战':'建立实时场景'}</button>}
      <button disabled={busy || !state || !active || !!state.view.gunnery?.ending} onClick={() => void action(state?.status.running ? "pause" : "resume")}>{state?.status.running ? "暂停试航" : "开始试航"}</button>
      <button disabled={busy || !state || !active} onClick={() => void action("read")}>读取实时状态</button>
      <button disabled={busy || !state || !active || !!state.view.gunnery?.ending} onClick={() => void action("withdraw")}>结束交战 / 撤离</button>
      <button disabled={busy || !active || entryUncertain || !!state?.settlement && !state.settlement.saved || !!preparedLaunch&&!!state&&!state.settlement?.saved} onClick={() => void action("close")}>{preparedLaunch?'返回战前准备':'退出实时实验'}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {storeError && <p role="alert">{storeError}</p>}
    {receipt && <p role="status">{receipt}</p>}
    {uncertain !== null && <p role="alert">操纵 {uncertain} 的结果尚未确认，正在查询；不会自动重发。</p>}
    {gunUncertain !== null && <p role="alert">火炮命令 {gunUncertain} 尚待确认，正在查询；不会自动重发射击。</p>}
    {state && <p className="editor-summary">{state.status.running ? "运行中" : pauseLabel[state.status.pause_reason ?? ""] ?? "已暂停"} · 第 {state.status.fixed_step} 步 · {(state.status.fixed_step / 60).toFixed(2)} 秒</p>}
    {state && !state.available && <p role="alert">旗舰已失去操纵权。{state.loss_reason === "direct_ship_falling" ? "舰艇正在坠落。" : "后续操纵已取消。"}</p>}
    <fieldset className="realtime-helm" disabled={busy || !active || !state?.status.running || !state.available || uncertain !== null}>
      <div className="editor-row">
      <label>实时车钟<select aria-label="实时车钟" value={draft.notch} onChange={e => setDraft({ ...draft, notch: e.target.value as Notch })}>{Object.entries(NOTCHES).map(([v,l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>实时方向<select aria-label="实时方向" value={draft.direction} onChange={e => setDraft({ ...draft, direction: e.target.value as HelmDraft["direction"] })}><option value="forward">前进</option><option value="reverse">倒车</option></select></label>
      <label><input type="checkbox" checked={draft.brake} onChange={e => setDraft({ ...draft, brake: e.target.checked })} />实时自动制动</label>
      </div>
      <div className="editor-row"><button onClick={() => void action("control")}>执行车钟 / 停止转向</button><button disabled={draft.brake} onClick={() => void action("control", -1)}>持续左转</button><button disabled={draft.brake} onClick={() => void action("control", 1)}>持续右转</button></div>
    </fieldset>
    {view?.snapshot.gunnery && state && <GunControlPanel view={view} shipId={state.direct_ship_id}
      weaponId={weaponId} groupId={groupId} onWeapon={chooseWeapon} onGroup={chooseGroup}
      disabled={busy || !active || !state.status.running || !state.available || gunUncertain !== null}
      onCommand={intent=>void sendGun(intent)} />}
    {view?.snapshot.gunnery?.ending && <p role="status">交战已结束：{{ victory: "敌方失去作战能力", defeat: "本方失去作战能力", draw: "双方失去作战能力", withdrawal: "主动撤离" }[view.snapshot.gunnery.ending.reason] ?? view.snapshot.gunnery.ending.reason}。已完成有效在装批次，清除在途弹丸；{state?.settlement?.saved ? "战后结果已保存。" : "请在结算页面保存本场结果。"}</p>}
    {view&&state&&<DamageControlPanel view={view} shipId={state.direct_ship_id}
      disabled={busy||!active||!state.status.running||!state.available||!!view.snapshot.gunnery?.ending}
      uncertain={damageUncertain} onCommand={intent=>void sendDamageControl(intent)}/>}
    {view?.snapshot.gunnery?.fireproof&&<section aria-label="交战防火与火情"><h3>防火与火情</h3><p>防火仅降低新起火概率；已有火情需要损管灭火。</p>
      {view.snapshot.gunnery.fireproof.map(s=>{const ship=view.geometry.ships.find(v=>v.id===s.ship_id);const fires=view.snapshot.gunnery?.damage_control?.fires.filter(f=>f.ship_id===s.ship_id)??[];
        return <article key={s.ship_id}><h4>{ship?.name??s.ship_id}</h4><p>{s.decks.map(d=>`第 ${d.deck_level} 层：${d.multiplier<1?`起火概率降低 ${((1-d.multiplier)*100).toFixed(0)}%`:'无额外防火'}`).join(' · ')}</p>
          <p>{fires.length?fires.map(f=>`${ship?.modules.find(m=>m.id===f.module_id)?.name??f.module_id} 正在燃烧（强度 ${(f.intensity_units/1000).toFixed(2)}）`).join('、'):'当前无火情'}</p></article>;})}</section>}
    {view?.snapshot.gunnery?.fuel&&<section aria-label="燃料储备"><h3>燃料储备</h3><p>发动机运行不消耗燃料；仅燃料槽耐久归零时损失其中燃料。</p>
      {view.snapshot.gunnery.fuel.map(s=><details key={s.ship_id} open={s.ship_id===state?.direct_ship_id}><summary>{view.geometry.ships.find(v=>v.id===s.ship_id)?.name} · 燃料 {s.total_units.toFixed(2)}</summary>
        {s.tanks.map(t=><p key={t.tank_id}>{fuelTankName(t,Object.fromEntries(view.geometry.ships.find(v=>v.id===s.ship_id)?.modules.map(m=>[m.id,m.name])??[]))}：燃料 {t.quantity_units.toFixed(2)} / {t.capacity_units} · 耐久 {t.durability_points.toFixed(1)} / {t.maximum_points}{t.durability_points<=0?' · 已损毁':''}</p>)}</details>)}</section>}
    {(state?.settlement || historyResult || !state) && <SettlementPanel
      current={state?.settlement && (!historyResult || historyResult.result.settlement_id === state.settlement.result.settlement_id) ? state.settlement : historyResult}
      library={library} busy={busy || !active} canDeploy={!state || !!state.settlement?.saved}
      onPrepare={preparedLaunch ? ()=>void action('close') : undefined}
      canPrepare={!entryUncertain && !!state?.settlement?.saved}
      onSave={id => void storedAction("save", id)} onInspect={id => void storedAction("inspect", id)}
      onRefresh={() => void storedAction("refresh")} onDeploy={(id, revision) => void storedAction("deploy", id, revision)} />}
    {view?.snapshot.gunnery?.damage && <details open><summary>命中记录 · {view.snapshot.gunnery.damage.hits} 次</summary>
      {view.snapshot.gunnery.damage.recent.slice(-5).map(hit => <p key={hit.projectile_id}>{view.geometry.ships.find(s => s.id === hit.ship_id)?.name} · 第 {hit.deck_level} 层 · {hit.projectile_type ? ammunitionName(hit.projectile_type)+' · ' : ''}{{ penetrated: "击穿", stopped: "装甲阻挡", ricochet: "跳弹", module: "外部模块命中" }[hit.outcome] ?? hit.outcome}{hit.module_ids.length ? ` · ${hit.module_ids.map(id => view.geometry.ships.find(s => s.id === hit.ship_id)?.modules.find(m => m.id === id)?.name ?? id).join("、")} −${hit.module_damage.toFixed(1)}` : ""}</p>)}
    </details>}
    {view && <><TacticalViewport view={view} active={active} selected={selected} onSelect={setSelected}
      gunControl={view.snapshot.gunnery && state ? { ownShipId: state.direct_ship_id, weaponId, weaponIds: controlledGuns.map(g=>g.module_id), selectionKey: groupId ?? weaponId ?? "", mode: gunMode ?? "auto", attackLayer: gunLayer,
        enabled: active && state.status.running && state.available && !busy && gunUncertain === null, canAim: !!gunMode && !!gunLayer,
        onWeapon: chooseCanvasWeapon, onTarget: (shipId, moduleId) => { void sendGun({ kind: "target", arguments: { ship_id: shipId, module_id: moduleId } }); },
        onAim: point => { pointerAim.current = point; }, onFire: point => { pointerAim.current = null;
          if (acting.current && weaponRef.current) queuedFire.current = { weapon: groupRef.current ?? weaponRef.current, point };
          else void sendGun({ kind: "fire", arguments: { point: [point.x, point.y] } }); },
        onLeave: () => { pointerAim.current = null; queuedFire.current = null; },
      } : undefined} />
      <div className="editor-row">{view.geometry.ships.map(s => <button key={s.id} onClick={() => setSelected(s.id)}>{s.name}</button>)}</div>
      {pose && <p>速度 {pose.speed_mps.toFixed(2)} m/s · 转速 {(pose.yaw_rate_radps * 180 / Math.PI).toFixed(2)} °/s · 船壳 {(pose.hull_integrity * 100).toFixed(1)}%</p>}
      {pose && durability && <p>结构耐久 {(pose.hull_integrity*durability.maximum_points).toFixed(0)} / {durability.maximum_points.toFixed(0)} · 由结构体积与材料冗余决定</p>}
      {pose && <details><summary>所选舰船模块耐久</summary>{pose.modules.map(m => <p key={m.id}>{view.geometry.ships.find(s => s.id === pose.id)?.modules.find(v => v.id === m.id)?.name ?? m.id}：{m.durability.toFixed(1)}{m.durability <= 0 ? " · 已损毁" : ""}</p>)}</details>}
      <details><summary>实时推进响应</summary>{state?.engines.map(e => <p key={e.id}>{view.geometry.ships.find(s => s.id === state.direct_ship_id)?.modules.find(m => m.id === e.id)?.name ?? e.id}：目标 {e.target}% / 实际 {e.actual}%</p>)}</details></>}
  </section>;
}
