import { useRef, useState } from "react";
import type { BridgeTransport } from "./bridge/transport";
import { normalizeHostFailure } from "./bridge/model";
import { EditorPanel } from "./editor/EditorPanel";
import { TacticalPanel } from "./tactical/TacticalPanel";
import { PreparationPanel } from "./tactical/PreparationPanel";
import type { PreparedLaunch } from "./tactical/preparation";
import type { ModeResult, WorkspaceMode } from "./tactical/model";

export function Workspace({ transport, instance, tacticalAvailable, preparationFirst=false }: {
  transport: BridgeTransport; instance: string; tacticalAvailable: boolean; preparationFirst?:boolean;
}) {
  const [mode, setMode] = useState<WorkspaceMode | "preparation">(preparationFirst ? "preparation" : "editor");
  const [switching, setSwitching] = useState(false), [confirmed, setConfirmed] = useState(true);
  const [editorBlocked, setEditorBlocked] = useState(false), [tacticalBusy, setTacticalBusy] = useState(false);
  const [preparationBusy,setPreparationBusy]=useState(false);
  const [preparedLaunch,setPreparedLaunch]=useState<PreparedLaunch|null>(null);
  const [preparationEpoch,setPreparationEpoch]=useState(0);
  const [error, setError] = useState("");
  const pending = useRef(false);
  async function switchMode(next: WorkspaceMode | "preparation", launch?:PreparedLaunch, closed=false) {
    if (pending.current || editorBlocked || (tacticalBusy&&!closed) || preparationBusy) return;
    pending.current = true; setSwitching(true); setError("");
    try {
      const result = await transport.tactical<ModeResult>({ backend_instance_id: instance,
        method: "tactical.set_mode", params: { mode: next === "preparation" ? "editor" : next }, session_id: null, expected_revision: null });
      if (result.mode !== (next === "preparation" ? "editor" : next) || !result.paused) throw new Error("尚未确认暂停与视图切换，请重新选择视图以恢复。");
      setMode(next); setConfirmed(true);
      if(launch)setPreparedLaunch(launch);
    } catch (failure) {
      // An acknowledgement can be lost after the mode changes. Keep both surfaces
      // locked until an explicit, idempotent set_mode receives a matching reply.
      setConfirmed(false); setError(normalizeHostFailure(failure).message);
    } finally { pending.current = false; setSwitching(false); }
  }
  const enabled = confirmed && !switching;
  return <>
    {tacticalAvailable && <nav className="workspace-nav" aria-label="工作视图">
      <button aria-pressed={mode === "editor" && confirmed} disabled={switching || editorBlocked || tacticalBusy || preparationBusy} onClick={() => void switchMode("editor")}>舰艇编辑</button>
      <button aria-pressed={mode === "preparation" && confirmed} disabled={switching || editorBlocked || tacticalBusy || preparationBusy} onClick={() => void switchMode("preparation")}>战前准备</button>
      <button aria-pressed={mode === "tactical" && confirmed} disabled={switching || editorBlocked || tacticalBusy || preparationBusy} onClick={() => void switchMode("tactical")}>战术视角</button>
      <span className="muted">{switching ? "正在确认视图切换…" : editorBlocked ? "请先完成或取消当前绘图、拖动与提交操作。" : "切换视图保留当前编辑内容。"}</span>
      {error && <p role="alert" className="editor-error">{error} 请重新选择视图以恢复操作。</p>}
    </nav>}
    {/* Keep all form state, local previews and close guards mounted across modes. */}
    <div hidden={mode !== "editor"} inert={!enabled || mode !== "editor"}>
      <EditorPanel instance={instance} transport={transport} active={enabled && mode === "editor"} onSwitchBlocked={setEditorBlocked} />
    </div>
    {tacticalAvailable && <div hidden={mode !== "preparation"} inert={!enabled || mode !== "preparation"}>
      <PreparationPanel key={preparationEpoch} instance={instance} transport={transport} active={enabled && mode === "preparation"} onBusy={setPreparationBusy}
        onEnter={launch=>void switchMode('tactical',launch)}/>
    </div>}
    {tacticalAvailable && <div hidden={mode !== "tactical"} inert={!enabled || mode !== "tactical"}>
      <TacticalPanel instance={instance} transport={transport} active={enabled && mode === "tactical"} onBusy={setTacticalBusy} preparedLaunch={preparedLaunch}
        onPreparedClose={()=>{setPreparedLaunch(null);setPreparationEpoch(n=>n+1);void switchMode('preparation',undefined,true);}} />
    </div>}
  </>;
}
