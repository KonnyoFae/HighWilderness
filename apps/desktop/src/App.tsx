import { useEffect, useMemo, useReducer } from "react";

import {
  diagnosticReducer,
  initialDiagnosticModel,
  normalizeHostFailure,
} from "./bridge/model";
import { TauriBridgeTransport } from "./bridge/transport";
import type { BridgeStatus, DesktopBridgeEvent } from "./bridge/types";

function display(value: string | number | null) {
  return value ?? "—";
}

function shortInstance(value: string | null) {
  if (value === null || value.length <= 32) {
    return display(value);
  }
  return `${value.slice(0, 20)}…${value.slice(-8)}`;
}

export function App() {
  const transport = useMemo(() => new TauriBridgeTransport(), []);
  const [model, dispatch] = useReducer(diagnosticReducer, initialDiagnosticModel);

  const receiveEvent = (event: DesktopBridgeEvent) => {
    dispatch({ type: "event_received", event });
  };

  const runStatusAction = async (
    name: string,
    operation: () => Promise<BridgeStatus>,
  ) => {
    dispatch({ type: "action_started", name });
    try {
      dispatch({ type: "status_received", status: await operation() });
    } catch (error) {
      dispatch({ type: "action_failed", error: normalizeHostFailure(error) });
    }
  };

  useEffect(() => {
    void runStatusAction("start", () => transport.start(receiveEvent));
    // Rust owns shutdown on window exit; the component starts exactly one epoch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transport]);

  const ping = async () => {
    const nonce = `ui.${Date.now().toString(36)}`;
    dispatch({ type: "action_started", name: "ping" });
    try {
      const result = await transport.ping(nonce);
      dispatch({ type: "ping_received", nonce: result.nonce });
    } catch (error) {
      dispatch({ type: "action_failed", error: normalizeHostFailure(error) });
    }
  };

  const busy = model.activeAction !== null;
  const status = model.status;
  const failure = model.commandError ?? status.last_error;

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">HIGH WILDERNESS · W1</p>
          <h1>桌面桥接诊断</h1>
          <p className="lede">Tauri 进程监管 · Python 权威规则进程 · UTF-8 JSON Lines</p>
        </div>
        <div
          className={`state-pill state-${status.state.toLowerCase()}`}
          data-testid="bridge-state"
        >
          <span aria-hidden="true" />
          {status.state}
        </div>
      </header>

      <section className="status-grid" aria-label="桥接状态">
        <article>
          <span>后端实例</span>
          <strong title={status.backend_instance_id ?? undefined}>
            {shortInstance(status.backend_instance_id)}
          </strong>
        </article>
        <article>
          <span>桥接接口</span>
          <strong>{status.bridge_interface}</strong>
        </article>
        <article>
          <span>Sidecar 接口</span>
          <strong>{display(status.sidecar_interface)}</strong>
        </article>
        <article>
          <span>舰艇数据契约</span>
          <strong>{display(status.ship_schema)}</strong>
        </article>
        <article>
          <span>单帧上限</span>
          <strong>
            {status.max_frame_bytes === null
              ? "—"
              : `${(status.max_frame_bytes / 1024 / 1024).toFixed(0)} MiB`}
          </strong>
        </article>
        <article>
          <span>最近 Ping</span>
          <strong>{display(model.lastPingNonce)}</strong>
        </article>
      </section>

      <section className="panel capability-panel">
        <div>
          <p className="panel-kicker">NEGOTIATED CAPABILITIES</p>
          <h2>本切片只开放系统生命周期</h2>
        </div>
        <div className="capabilities">
          {status.capabilities.length === 0 ? (
            <span className="muted">等待握手</span>
          ) : (
            status.capabilities.map((capability) => <code key={capability}>{capability}</code>)
          )}
        </div>
      </section>

      <section className="actions" aria-label="桥接操作">
        <button
          type="button"
          disabled={busy || !["STOPPED", "FAILED"].includes(status.state)}
          onClick={() => void runStatusAction("start", () => transport.start(receiveEvent))}
        >
          启动
        </button>
        <button
          type="button"
          disabled={busy || status.state === "STOPPED"}
          onClick={() => void runStatusAction("restart", () => transport.restart(receiveEvent))}
        >
          显式重启
        </button>
        <button
          type="button"
          disabled={busy || status.state !== "READY"}
          onClick={() => void ping()}
        >
          Ping
        </button>
        <button
          className="secondary"
          type="button"
          disabled={busy || status.state === "STOPPED"}
          onClick={() => void runStatusAction("stop", () => transport.stop())}
        >
          停止
        </button>
        {busy && <span className="action-progress">正在执行：{model.activeAction}</span>}
      </section>

      {failure !== null && (
        <section className="failure-panel" role="alert">
          <div>
            <p className="panel-kicker">STRUCTURED FAILURE</p>
            <h2>{failure.code}</h2>
          </div>
          <p>{failure.message}</p>
          <code>{failure.path}</code>
        </section>
      )}

      <section className="panel event-panel">
        <div className="event-heading">
          <div>
            <p className="panel-kicker">BOUNDED EVENT CHANNEL</p>
            <h2>最近事件</h2>
          </div>
          <span>{model.events.length} / 200</span>
        </div>
        <ol aria-live="polite">
          {model.events.length === 0 ? (
            <li className="empty-event">等待宿主事件</li>
          ) : (
            model.events
              .slice(-8)
              .reverse()
              .map((event, index) => (
                <li key={`${event.backend_instance_id ?? "global"}-${model.events.length - index}`}>
                  <div>
                    <strong>{event.kind}</strong>
                    <span>{shortInstance(event.backend_instance_id)}</span>
                  </div>
                  <code>{JSON.stringify(event.payload)}</code>
                </li>
              ))
          )}
        </ol>
      </section>
    </main>
  );
}
