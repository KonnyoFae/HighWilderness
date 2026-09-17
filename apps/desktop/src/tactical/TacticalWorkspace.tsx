import { useRef, useState } from "react";
import type { BridgeTransport } from "../bridge/transport";
import { normalizeHostFailure } from "../bridge/model";
import type { ModeResult } from "./model";
import type { PreparedLaunch } from "./preparation";
import { PreparationWorkspace } from "./PreparationWorkspace";
import { RealtimePanel } from "./RealtimePanel";
import { PreparationRecovery } from "./PreparationRecovery";
import { TacticalTestReset } from "./TacticalTestReset";

export function TacticalWorkspace({ transport, instance }: {
  transport: BridgeTransport; instance: string;
}) {
  const [launch, setLaunch] = useState<PreparedLaunch | null>(null);
  const [preparationEpoch, setPreparationEpoch] = useState(0);
  const [recovered, setRecovered] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState<{ launch: PreparedLaunch | null } | null>(null);
  const pending = useRef(false);
  const resetGeneration = useRef(0), generation = resetGeneration.current;
  const [resetLocked, setResetLocked] = useState(false), [resetNotice, setResetNotice] = useState('');

  async function changeScene(next: PreparedLaunch | null) {
    if (pending.current || resetLocked || generation !== resetGeneration.current) return;
    pending.current = true; setBusy(true); setError("");
    try {
      const mode = next ? "tactical" : "editor";
      const result = await transport.tactical<ModeResult>({ backend_instance_id: instance,
        method: "tactical.set_mode", params: { mode }, session_id: null, expected_revision: null });
      if (generation !== resetGeneration.current) return;
      if (result.mode !== mode || !result.paused) throw new Error("场景切换尚未确认，请重试。");
      setLaunch(next); setRetry(null);
      if (!next) setPreparationEpoch(value => value + 1);
    } catch (failure) {
      if (generation !== resetGeneration.current) return;
      setError(normalizeHostFailure(failure).message);
      // Preserve the exact launch after a lost reply; never import or charge again.
      setRetry({ launch: next });
    } finally { if (generation === resetGeneration.current) { pending.current = false; setBusy(false); } }
  }

  function cleared() {
    resetGeneration.current++; pending.current = false;
    setLaunch(null); setRecovered(false); setRetry(null); setError(''); setBusy(false);
    setPreparationEpoch(value => value + 1);
    setResetNotice('战术测试数据已清空，可以重新导入舰艇开始测试。');
  }

  return <div className="tactical-workspace">
    <nav className="tactical-journey" aria-label="战术流程">
      <span aria-current={!launch ? "step" : undefined}>01 战前准备</span>
      <span aria-current={launch ? "step" : undefined}>02 交战与结算</span>
      <p>准备舰队 · 指定旗舰 · 保存战果</p>
      <TacticalTestReset transport={transport} instance={instance} disabled={busy}
        onLocked={setResetLocked} onComplete={cleared} />
    </nav>
    {resetNotice && <p role="status">{resetNotice}</p>}
    <div inert={resetLocked}>
    {error && <div className="tactical-transition-error" role="alert">
      <p>{error}</p>
      <button disabled={busy} onClick={() => retry && void changeScene(retry.launch)}>重试场景切换</button>
    </div>}
    {busy && <p role="status">正在切换场景…</p>}
    {!launch && !recovered ? <PreparationRecovery key={preparationEpoch} transport={transport} instance={instance} onReady={() => setRecovered(true)} /> : !launch ? <div inert={busy || retry !== null}>
      <PreparationWorkspace key={preparationEpoch} transport={transport} instance={instance}
        active={!busy && !resetLocked && retry === null} onEnter={next => void changeScene(next)} />
    </div> : <div inert={busy || retry !== null}>
      <RealtimePanel key={launch.launch_id} transport={transport} instance={instance}
        active={!busy && !resetLocked && retry === null} preparedLaunch={launch} battleLayout
        onClose={() => void changeScene(null)} />
    </div>}
    </div>
  </div>;
}
