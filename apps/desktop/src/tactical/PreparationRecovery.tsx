import { useEffect, useRef, useState } from 'react';
import type { BridgeTransport } from '../bridge/transport';
import { normalizeHostFailure } from '../bridge/model';
import { SettlementPanel } from './SettlementPanel';
import type { SettlementEnvelope, SettlementLibrary } from './settlement';
import type { TacticalRequest } from './model';

export function PreparationRecovery({ transport, instance, onReady }: {
  transport: BridgeTransport; instance: string; onReady: () => void;
}) {
  const [library, setLibrary] = useState<SettlementLibrary | null>(null);
  const [current, setCurrent] = useState<SettlementEnvelope | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const working = useRef(false), mounted = useRef(true);
  function call<T>(method: TacticalRequest['method'], params: Record<string, unknown>) {
    return transport.tactical<T>({ backend_instance_id: instance, method, params, session_id: null, expected_revision: null });
  }
  async function run(action: 'refresh' | 'inspect' | 'save', id?: string) {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    try {
      let result = current;
      const identity = id ?? current?.result.settlement_id;
      if (identity) result = await call<SettlementEnvelope>(action === 'save' ? 'tactical.realtime.save' : 'tactical.realtime.settlement', { settlement_id: identity });
      if (mounted.current) setCurrent(result);
      const all = await call<SettlementLibrary>('tactical.realtime.settlements', {});
      const pending = { ...all, results: all.results.filter(r => !r.saved) };
      if ((!result || result.saved) && pending.results.length) result = await call<SettlementEnvelope>('tactical.realtime.settlement', { settlement_id: pending.results[0].settlement_id });
      if (!mounted.current) return;
      setCurrent(result); setLibrary(pending);
      if (!pending.results.length && !result) onReady();
    } catch (failure) { if (mounted.current) setError(normalizeHostFailure(failure).message); }
    finally { working.current = false; if (mounted.current) setBusy(false); }
  }
  useEffect(() => { mounted.current = true; void run('refresh'); return () => { mounted.current = false; }; }, []);
  return <section className="panel preparation-recovery" aria-label="恢复战后结果">
    <h2>恢复战后结果</h2>
    <p>此前交战已经结束。保存待处理战果后，继续准备舰队。</p>
    {!!library?.results.length && <p role="status">还有 {library.results.length} 份战果待处理；保存本份后自动打开下一份。需要重新测试时，可使用上方“清空战术测试数据”。</p>}
    {busy && <p role="status">正在读取或保存战后结果…</p>}
    {error && <p role="alert">{error}<button disabled={busy} onClick={() => void run('refresh')}>重试读取战果</button></p>}
    {current && <SettlementPanel current={current} library={library} busy={busy} canDeploy={false}
      onSave={id => void run('save', id)} onInspect={id => void run('inspect', id)} onRefresh={() => void run('refresh')}
      onDeploy={() => {}} onPrepare={onReady} canPrepare={current.saved && library?.results.length === 0} />}
  </section>;
}
