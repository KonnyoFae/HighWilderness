import type { ResourceIndex } from "./model";

type Recovery = { key: string; name: string; revision: number; valid_record: boolean };
export function EditorStart({ index, busy, hullName, outfitName, onHullName, onOutfitName, onNewHull, onNewOutfit, onOpen, onExample, recoveries, onRecover }: {
  index: ResourceIndex | null; busy: boolean; hullName: string; outfitName: string;
  onHullName: (name: string) => void; onOutfitName: (name: string) => void;
  onNewHull: () => void; onNewOutfit: () => void; onOpen: (kind: "HullBlueprint" | "OutfitPlan") => void;
  onExample: (key: string) => void; recoveries: Recovery[]; onRecover: (key: string) => void;
}) {
  return <div className="editor-start" aria-label="编辑器启动入口">
    <header><p className="panel-kicker">舰艇设计</p><h2>选择编辑器</h2><p>新建一份设计，或继续编辑已有文件。</p></header>
    <div className="editor-start-cards">
      {(["HullBlueprint", "OutfitPlan"] as const).map(kind => {
        const hull = kind === "HullBlueprint";
        const name = hull ? hullName : outfitName;
        const examples = index?.resources.filter(item => item.editable && item.kind === kind) ?? [];
        return <section className="editor-start-card" key={kind} aria-label={hull ? "船壳编辑器入口" : "舾装编辑器入口"}>
          <div className="editor-start-icon" aria-hidden="true">{hull ? "◇" : "▦"}</div>
          <h3>{hull ? "船壳编辑器" : "舾装编辑器"}</h3>
          <p>{hull ? "绘制船体轮廓，编辑甲板、材料和装甲。" : "在船壳上安装部件，调整位置、朝向和武器组。"}</p>
          <label>{hull ? "新船壳名称" : "新舾装名称"}<input aria-label={hull ? "新船壳名称" : "新舾装名称"} value={name} maxLength={256} disabled={busy}
            onChange={e => (hull ? onHullName : onOutfitName)(e.target.value)} /></label>
          <button className="editor-start-primary" disabled={busy || !index || !name.trim()} onClick={hull ? onNewHull : onNewOutfit}>{hull ? "新建船壳" : "选择船壳并新建舾装"}</button>
          <button className="secondary" disabled={busy} onClick={() => onOpen(kind)}>{hull ? "打开船壳文件" : "打开舾装文件"}</button>
          <small>{hull ? "从空白甲板开始；保存的船壳可用于制作舾装。" : "新建时选择已保存的合法船壳；继续已有设计请打开舾装文件。"}</small>
          <details className="editor-start-examples"><summary>{hull ? "内置船壳示例" : "内置舾装示例"}</summary>
            {!index && <p>正在读取示例…</p>}
            {examples.map(item => <button key={item.key} disabled={busy} onClick={() => onExample(item.key)}>{item.name}</button>)}
          </details>
        </section>;
      })}
    </div>
    {recoveries.length > 0 && <section className="editor-start-recoveries" aria-label="恢复草稿">
      <h3>继续未完成的设计</h3>
      {recoveries.map(record => <button key={record.key} disabled={busy || !record.valid_record} onClick={() => onRecover(record.key)}>恢复：{record.name}</button>)}
    </section>}
  </div>;
}
