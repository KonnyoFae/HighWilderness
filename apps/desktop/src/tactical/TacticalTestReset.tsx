import { useRef, useState } from 'react';
import type { BridgeTransport } from '../bridge/transport';
import { normalizeHostFailure } from '../bridge/model';

export function TacticalTestReset({ transport, instance, disabled, onLocked, onComplete }: {
  transport: BridgeTransport; instance: string; disabled: boolean;
  onLocked: (locked: boolean) => void; onComplete: () => void;
}) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const request = useRef<{ reset_id: string; scope: string } | null>(null), working = useRef(false);
  async function clear() {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    request.current ??= { reset_id: `reset.${crypto.randomUUID()}`, scope: 'all_tactical_test_state' };
    try {
      const result = await transport.tactical<{ reset_id: string; cleared: boolean; mode: string }>({
        backend_instance_id: instance, method: 'tactical.reset_test_state', params: request.current,
        session_id: null, expected_revision: null,
      });
      if (!result.cleared || result.reset_id !== request.current.reset_id || result.mode !== 'editor')
        throw new Error('清空结果尚未确认，请重试同一次操作。');
      onComplete(); request.current = null; setOpen(false); onLocked(false);
    } catch (failure) { setError(normalizeHostFailure(failure).message); }
    finally { working.current = false; setBusy(false); }
  }
  return <>
    <button className="tactical-reset-button" disabled={disabled || open} onClick={() => { setOpen(true); setError(''); onLocked(true); }}>清空战术测试数据</button>
    {open && <div className="tactical-reset-backdrop">
      <section className="tactical-reset-dialog" role="alertdialog" aria-modal="true" aria-labelledby="tactical-reset-title">
        <h2 id="tactical-reset-title">放弃现有战术测试成果</h2>
        <p>清空当前战斗、全部已保存及待保存战果、测试舰艇实例、准备记录和测试物资库存，然后返回全新的战前准备。</p>
        <p>已保存的船壳、舾装设计文件与编辑器草稿会保留。清空后需重新导入舰艇，测试物资按初始配置重新建立。</p>
        <p><strong>这会永久删除战术测试进度，无需先保存或完成结算。</strong></p>
        {error && <p role="alert">{error}。清空结果尚未确认，请重试；重试不会重复清除新测试。</p>}
        {busy && <p role="status">正在清空战术测试数据…</p>}
        <div className="editor-row">
          <button autoFocus disabled={busy || request.current !== null} onClick={() => { setOpen(false); onLocked(false); }}>取消</button>
          <button disabled={busy} className="tactical-reset-confirm" onClick={() => void clear()}>{request.current ? '重试清空' : '确认清空并重新开始'}</button>
        </div>
      </section>
    </div>}
  </>;
}
