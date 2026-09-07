import { useEffect, useState } from "react";
import type { HullCommand, HullDeck, HullRegion, MaterialOption } from "./model";
import type { SourceSide } from "./symmetry";
import { onGrid } from "./interaction";
import type { Point, Selection } from "./viewport";
export function HullInspector({ deck, region, selection, materials, busy, drawing, onCommand, onPoint, onSymmetry }: {
  deck: HullDeck | undefined; region: HullRegion | undefined; selection: Selection | null;
  materials: MaterialOption[]; busy: boolean; drawing: boolean; onCommand: HullCommand; onPoint: (p: Point) => void; onSymmetry: (side: SourceSide) => void;
}) {
  const [x, setX] = useState("0"), [y, setY] = useState("0");
  const [structure, setStructure] = useState("");
  const [armorId, setArmorId] = useState("");
  const [thickness, setThickness] = useState("0");
  const i = selection?.vertex;
  const vertex = i != null ? region?.vertices_m[i] : undefined;
  const edge = i != null ? region?.edge_armor[i] : undefined;
  const structures = materials.filter(m => m.category === "structure"), armors = materials.filter(m => m.category === "base_armor");
  useEffect(() => { setStructure(deck ? `${deck.structure_material.id}@${deck.structure_material.version}` : ""); }, [deck?.structure_material.id, deck?.structure_material.version]);
  useEffect(() => { if (vertex) { setX(String(vertex[0])); setY(String(vertex[1])); } }, [vertex?.[0], vertex?.[1], region?.id, i]);
  useEffect(() => { if (edge) { setArmorId(`${edge.material.id}@${edge.material.version}`); setThickness(String(edge.thickness_m)); } }, [edge?.material.id, edge?.material.version, edge?.thickness_m, region?.id, i]);
  const material = (choices: MaterialOption[], key: string) => {
    const found = choices.find(m => `${m.id}@${m.version}` === key);
    return found && { id: found.id, version: found.version };
  };
  const chosenArmor = material(armors, armorId), chosenStructure = material(structures, structure);
  const validPoint = x.trim() !== "" && y.trim() !== "" && onGrid(Number(x)) && onGrid(Number(y));
  const validArmor = chosenArmor && thickness.trim() !== "" && Number.isFinite(Number(thickness)) && Number(thickness) >= 0;
  const context = { deck_id: deck?.id, region_id: region?.id };
  return <div className="hull-inspector">
    <fieldset disabled={busy}>
      {deck && !drawing && <><label>结构材料<select aria-label="结构材料" value={structure} onChange={e => setStructure(e.target.value)}>
        {!chosenStructure && <option value={structure}>{deck.structure_material.id}（目录未提供）</option>}
        {structures.map(m => <option key={`${m.id}@${m.version}`} value={`${m.id}@${m.version}`}>{m.name} · v{m.version}</option>)}</select></label>
        <button disabled={!chosenStructure} onClick={() => void onCommand("hull.set_structure_material", { deck_id: deck.id, material: chosenStructure })}>应用结构材料</button>
        <button disabled={deck.is_base} onClick={() => void onCommand("hull.set_base_deck", { deck_id: deck.id })}>设为基底层</button></>}
      {(drawing || vertex) && <><div className="point-inputs"><label>X / m<input aria-label="端点 X" type="number" step="2.5" value={x} onChange={e => setX(e.target.value)} /></label>
        <label>Y / m<input aria-label="端点 Y" type="number" step="2.5" value={y} onChange={e => setY(e.target.value)} /></label></div>
        {!validPoint && <small>坐标必须为 2.5 米的倍数。</small>}
        {drawing ? <button disabled={!validPoint} onClick={() => onPoint({ x: Number(x), y: Number(y) })}>添加绘图点</button>
          : <><button disabled={!validPoint} onClick={() => void onCommand("hull.move_vertex", { ...context, vertex_index: i, point_m: [Number(x), Number(y)] })}>应用端点坐标</button>
          <button disabled={!validPoint} onClick={() => void onCommand("hull.insert_vertex", { ...context, edge_index: i, point_m: [Number(x), Number(y)] })}>在此点后插入端点</button></>}
      </>}
      {edge && !drawing && <><h4>边 {(i ?? 0) + 1} · 从选点到下一点</h4>
        <label>边装甲材料<select aria-label="边装甲材料" value={armorId} onChange={e => setArmorId(e.target.value)}>
          {!chosenArmor && <option value={armorId}>请选择材料</option>}
          {armors.map(m => <option key={`${m.id}@${m.version}`} value={`${m.id}@${m.version}`}>{m.name} · v{m.version}</option>)}</select></label>
        <label>边装甲厚度 / m<input aria-label="边装甲厚度" type="number" step="0.01" min="0" value={thickness} onChange={e => setThickness(e.target.value)} /></label>
        <button disabled={!validArmor} onClick={() => void onCommand("hull.set_edge_armor", { ...context, edge_index: i, material: chosenArmor, thickness_m: Number(thickness) })}>应用边装甲</button>
        <button disabled={!validArmor || (region?.vertices_m.length ?? 0) <= 3} onClick={() => void onCommand("hull.remove_vertex", { ...context, vertex_index: i, merged_armor: { material: chosenArmor, thickness_m: Number(thickness) } })}>删除端点并合并边</button>
        <small>合并后的连接边使用上方所选材料和厚度；操作可撤销。</small></>}
      {region && !drawing && <><button onClick={() => onSymmetry("left")}>以左侧为准</button>
        <button onClick={() => onSymmetry("right")}>以右侧为准</button>
        <small>预览后应用：用所选侧的轮廓及边装甲替换另一侧，不新增区域。</small>
        <button className="secondary" onClick={() => void onCommand("hull.remove_region", context)}>删除当前区域</button></>}
    </fieldset>
  </div>;
}
