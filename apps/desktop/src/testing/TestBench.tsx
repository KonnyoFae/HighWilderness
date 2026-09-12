import { useEffect, useRef, useState, type ReactNode } from 'react';
import { invoke } from '@tauri-apps/api/core';
import './testbench.css';

type Run = {id:string; scenario:string; name:string; created_at:string; directory:string; fixture_note:string};
const scenes = [
  {id:'roundtrip',name:'完整交战与返回',description:'从准备到交战，再把原舰带入下一场。',steps:['选择测试舰，点击“准备所选舰船”，选择武器预装弹种。','核对并保存准备，选择旗舰，进入交战。','开火或主动撤离，保存全部战后结果，再返回战前准备。','用下方“显式重启”重启后台，重新准备原舰，检查战损和余量。']},
  {id:'gunnery',name:'火炮与弹药',description:'检查炮向、自动与手动射击，以及特殊弹消费。',steps:['准备舰船，选择普通弹、穿甲弹或燃烧弹预装。','入战后右键选炮，左键指定目标，观察实际炮向和命中。','切换手动射击；选择下一批弹种，观察原待发弹与材料消耗。','撤离并保存，核对弹药、特殊合金和高能燃料的余量。']},
  {id:'damage-control',name:'防火与损管',description:'防火填充、受损货舱和预设火情，便于直接观察。',steps:['此样本预设一处火情和受损货舱，不代表炮击已经发生。','保存准备并入战，开始运行后在“旗舰损管”启动设备。','观察先灭火后维修、资源消费、目标切换及暂停冻结。','保存战后状态后再入战，核对耐久、余量和剩余火情。']},
  {id:'fuel',name:'燃料与货舱',description:'预装 120 单位填充槽燃料，观察库存与保存。',steps:['在准备中查看逐槽油量；可装卸物资并核对有限供给。','入战运行推进与转向，燃料不应因此减少。','若燃料槽被击毁，只损失该槽余油；普通掉血不漏油。','结算保存并再次准备，核对逐槽状态及货物余量。']},
];
const message = (e:unknown) => typeof e === 'string' ? e : e instanceof Error ? e.message : JSON.stringify(e);

export function TestBench({renderWorkspace}:{renderWorkspace:()=>ReactNode}) {
  const [runs,setRuns]=useState<Run[]>([]),[current,setCurrent]=useState<Run|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[saved,setSaved]=useState(false);
  const pending=useRef(false);
  async function action(work:()=>Promise<void>) {
    if(pending.current)return; pending.current=true;setBusy(true);setError('');
    try {await work();} catch(e){setError(message(e));} finally{pending.current=false;setBusy(false);}
  }
  const refresh=async()=>setRuns(await invoke<Run[]>('testbench_request',{action:'list'}));
  useEffect(()=>{void action(refresh);},[]);
  async function open(run:Run) {
    await invoke('testbench_open',{runId:run.id});setCurrent(run);setNote('');setSaved(false);
  }
  const scene=scenes.find(s=>s.id===current?.scenario);
  return <>
    <section className="testbench-shell" aria-label="战术测试台">
      <header className="testbench-heading"><div><p className="eyebrow">HIGH WILDERNESS · TEST WORKSPACE</p><h1>战术测试台</h1>
        <p>选择样本，使用真实游戏规则测试。各次测试独立保存，可关闭后继续。</p></div><span className="testbench-badge">独立测试存档</span></header>
      {error&&<p role="alert" className="editor-error">{error}</p>}
      {current ? <>
        <div className="testbench-toolbar"><h2>{current.name}</h2><button disabled={busy} onClick={()=>void action(async()=>{
          await invoke('bridge_stop');setCurrent(null);await refresh();
        })}>关闭后台并返回测试台</button></div>
        <p className="muted">关闭前请保存编辑和准备内容。未结束的交战恢复入战存档；已生成结算可继续处理。</p>
        <details open><summary>本次操作提示</summary><ol>{scene?.steps.map(s=><li key={s}>{s}</li>)}</ol>
          <p>{current.fixture_note} 当前使用已有准备入口和技术测试敌舰，不提供战略遭遇界面。</p>
          <p>单场最大预期 15 分钟，不必跑满；超过时记录配置和原因，供整体平衡调整。</p></details>
        <details><summary>记录问题与存档位置</summary><p className="testbench-path">{current.directory}</p>
          <label htmlFor="testbench-note">发生了什么？操作、预期与实际表现</label>
          <textarea id="testbench-note" value={note} maxLength={8000} onChange={e=>{setNote(e.target.value);setSaved(false);}}/>
          <button disabled={busy||!note.trim()} onClick={()=>void action(async()=>{
            await invoke('testbench_request',{action:'note',runId:current.id,note});setSaved(true);setNote('');
          })}>保存问题记录</button>{saved&&<span role="status"> 已保存到本次测试目录。</span>}</details>
      </> : <>
        <div className="testbench-grid">{scenes.map(s=><article key={s.id}><h2>{s.name}</h2><p>{s.description}</p>
          <button disabled={busy} onClick={()=>void action(async()=>{
            const run=await invoke<Run>('testbench_request',{action:'create',scenario:s.id});await open(run);
          })}>新建测试</button></article>)}</div>
        {busy&&<p role="status">正在准备测试，请稍候…</p>}
        <section className="testbench-history"><div className="testbench-toolbar"><h2>继续上次测试</h2><button disabled={busy} onClick={()=>void action(refresh)}>刷新</button></div>
          {!runs.length&&!busy&&<p>还没有测试记录。先从上方选择一个样本。</p>}
          {runs.map(r=><div className="testbench-run" key={r.id}><div><strong>{r.name}</strong><p>{new Date(r.created_at).toLocaleString()}</p></div>
            <button disabled={busy} onClick={()=>void action(()=>open(r))}>继续测试</button></div>)}</section>
        <p className="muted">此简版提供人工操作入口和问题记录，不会自动判定测试通过。O4 无需重验，两小时测试已取消。</p>
      </>}
    </section>
    {current&&renderWorkspace()}
  </>;
}
