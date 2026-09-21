import { useEffect, useState } from "react";
import type { HullCommand, HullDeck, HullRegion, MaterialOption } from "./model";
import type { SourceSide } from "./symmetry";
import { onGrid } from "./interaction";
import type { Point, Selection } from "./viewport";
export function HullInspector({ deck, decks, region, selection, materials, busy, drawing, armorMode, onCommand, onPoint, onSymmetry }: {
  deck: HullDeck | undefined; decks: HullDeck[]; region: HullRegion | undefined; selection: Selection | null;
  materials: MaterialOption[]; busy: boolean; drawing: boolean; armorMode: boolean; onCommand: HullCommand; onPoint: (p: Point) => void; onSymmetry: (side: SourceSide) => void;
}) {
  const [x, setX] = useState("0"), [y, setY] = useState("0");
  const [structure, setStructure] = useState("");
  const [structureMm, setStructureMm] = useState("100");
  const [armorId, setArmorId] = useState("");
  const [thickness, setThickness] = useState("0");
  const [angle, setAngle] = useState("0");
  const i = selection?.edge ?? selection?.vertex;
  const vertex = selection?.vertex != null ? region?.vertices_m[selection.vertex] : undefined;
  const edge = i != null ? region?.edge_armor[i] : undefined;
  const structures = materials.filter(m => m.category === "structure"), armors = materials.filter(m => m.category === "base_armor");
  useEffect(() => { setStructure(deck ? `${deck.structure_material.id}@${deck.structure_material.version}` : ""); }, [deck?.structure_material.id, deck?.structure_material.version]);
  useEffect(() => { setStructureMm(String(Math.round((deck?.structure_thickness_m ?? .1) * 1000))); }, [deck?.id, deck?.structure_thickness_m]);
  useEffect(() => { if (vertex) { setX(String(vertex[0])); setY(String(vertex[1])); } }, [vertex?.[0], vertex?.[1], region?.id, i]);
  useEffect(() => { if (edge) { setArmorId(`${edge.material.id}@${edge.material.version}`); setThickness(String(edge.thickness_m * 1000)); setAngle(String(edge.flare_angle_deg ?? 0)); } }, [edge?.material.id, edge?.material.version, edge?.thickness_m, edge?.flare_angle_deg, region?.id, i]);
  const material = (choices: MaterialOption[], key: string) => {
    const found = choices.find(m => `${m.id}@${m.version}` === key);
    return found && { id: found.id, version: found.version };
  };
  const chosenArmor = material(armors, armorId), chosenStructure = material(structures, structure);
  const validPoint = x.trim() !== "" && y.trim() !== "" && onGrid(Number(x)) && onGrid(Number(y));
  const validArmor = chosenArmor && thickness.trim() !== "" && Number.isFinite(Number(thickness)) && Number(thickness) >= 0 && (Number(angle) === 0 || Number(thickness) > 0);
  const profile = {material: chosenArmor, thickness_m: Number(thickness)/1000, flare_angle_deg: Number(angle)};
  const context = { deck_id: deck?.id, region_id: region?.id };
  const base = decks.find(d => d.is_base);
  const baseMm = Math.round((base?.structure_thickness_m ?? .1) * 1000);
  const proposedMm = Number(structureMm);
  const validThickness = structureMm.trim() !== "" && Number.isInteger(proposedMm) && proposedMm >= 15 && proposedMm <= 100 && proposedMm % 5 === 0;
  const conflicts = validThickness && deck?.is_base ? decks.filter(d => !d.is_base && (d.structure_thickness_m ?? .1) * 1000 > proposedMm + 1e-8) : [];
  const exceedsBase = !deck?.is_base && proposedMm > baseMm;
  return <div className="hull-inspector">
    <fieldset disabled={busy}>
      {deck && !drawing && !armorMode && <><label>结构材料<select aria-label="结构材料" value={structure} onChange={e => setStructure(e.target.value)}>
        {!chosenStructure && <option value={structure}>{deck.structure_material.id}（目录未提供）</option>}
        {structures.map(m => <option key={`${m.id}@${m.version}`} value={`${m.id}@${m.version}`}>{m.name} · v{m.version}</option>)}</select></label>
        <button disabled={!chosenStructure} onClick={() => void onCommand("hull.set_structure_material", { deck_id: deck.id, material: chosenStructure })}>应用结构材料</button>
        <label>本层结构厚度 / 毫米<input aria-label="本层结构厚度" type="number" min="15" max={deck.is_base ? 100 : baseMm} step="5"
          value={structureMm} onChange={e => setStructureMm(e.target.value)} /></label>
        <input aria-label="结构厚度滑块" type="range" min="15" max={deck.is_base ? 100 : baseMm} step="5"
          value={validThickness ? proposedMm : Math.round((deck.structure_thickness_m ?? .1) * 1000)} onChange={e => setStructureMm(e.target.value)} />
        <small>15～100毫米，每档5毫米。{deck.is_base ? "本层为全舰结构厚度上限。" : `基底层上限：${baseMm}毫米；连接结构另计本层厚度的20%。`}</small>
        {!validThickness && <small role="status">请输入15～100毫米之间、5毫米整数档的厚度。</small>}
        {validThickness && exceedsBase && <small role="status">本层不能厚于基底层的{baseMm}毫米。</small>}
        {conflicts.length > 0 && <small role="status">以下甲板超过新基底层厚度：{conflicts.map(d => `${d.id}（${Math.round((d.structure_thickness_m ?? .1) * 1000)}毫米）`).join("、")}。请先减薄这些层，或明确统一全部甲板。</small>}
        <button disabled={!validThickness || exceedsBase || conflicts.length > 0}
          onClick={() => void onCommand("hull.set_structure_thickness", {deck_id: deck.id, thickness_m: proposedMm / 1000})}>应用本层厚度</button>
        {deck.is_base && decks.length > 1 && <button disabled={!validThickness}
          onClick={() => void onCommand("hull.set_all_structure_thickness", {thickness_m: proposedMm / 1000})}>全部甲板统一为此厚度</button>}
        <label>本层边缘填充<select aria-label="本层边缘填充" value={deck.filling?.id ?? "gtw.filling.none"}
          onChange={e => void onCommand("hull.set_filling", {deck_id: deck.id, configuration: {id: e.target.value, version: 1}})}>
          <option value="gtw.filling.none">不额外填充</option><option value="gtw.filling.rack">货架填充</option>
          <option value="gtw.filling.spirit_fuel">灵烷储存设施</option>
          <option value="gtw.filling.fireproof">防火材料</option>
        </select></label><small>整层边缘空间统一配置，含同层分离区域。灵烷与防火填充目前仅保存设计，暂不支持入战。</small>
        <button disabled={deck.is_base} onClick={() => void onCommand("hull.set_base_deck", { deck_id: deck.id })}>设为基底层</button></>}
      {(drawing || vertex && !armorMode) && <><div className="point-inputs"><label>X / m<input aria-label="端点 X" type="number" step="2.5" value={x} onChange={e => setX(e.target.value)} /></label>
        <label>Y / m<input aria-label="端点 Y" type="number" step="2.5" value={y} onChange={e => setY(e.target.value)} /></label></div>
        {!validPoint && <small>坐标必须为 2.5 米的倍数。</small>}
        {drawing ? <button disabled={!validPoint} onClick={() => onPoint({ x: Number(x), y: Number(y) })}>添加绘图点</button>
          : <><button disabled={!validPoint} onClick={() => void onCommand("hull.move_vertex", { ...context, vertex_index: i, point_m: [Number(x), Number(y)] })}>应用端点坐标</button>
          <button disabled={!validPoint} onClick={() => void onCommand("hull.insert_vertex", { ...context, edge_index: i, point_m: [Number(x), Number(y)] })}>在此点后插入端点</button></>}
      </>}
      {edge && !drawing && <><h4>基础装甲 · 边 {(i ?? 0) + 1}</h4>
        <label>边装甲材料<select aria-label="边装甲材料" value={armorId} onChange={e => setArmorId(e.target.value)}>
          {!chosenArmor && <option value={armorId}>请选择材料</option>}
          {armors.map(m => <option key={`${m.id}@${m.version}`} value={`${m.id}@${m.version}`}>{m.name} · v{m.version}</option>)}</select></label>
        <label>边装甲厚度 / 毫米<input aria-label="边装甲厚度" type="number" step="5" min="0" value={thickness} onChange={e => setThickness(e.target.value)} /></label>
        <label>向外倾角<select aria-label="装甲外飘倾角" value={angle} onChange={e=>setAngle(e.target.value)}>
          <option value="0">关闭 · 竖直装甲</option>{[30,45,60].map(a=><option key={a} value={a}>{a}°</option>)}
        </select></label>
        <small>相对竖直侧面的角度。外飘边禁止侧挂；上层外飘会占用下层露天安装格。</small>
        {Number(angle)>0 && Number(thickness)<=0 && <small role="status">启用外飘需要大于零的装甲厚度。</small>}
        <button disabled={!validArmor} onClick={() => void onCommand("hull.set_edge_armor_profile", { ...context, edge_index: i, ...profile })}>应用边装甲</button>
        <small>同时更新本层镜像边；非法外形会明确报错，可撤销调整。</small>
        <button disabled={!validArmor} onClick={() => void onCommand("hull.set_deck_armor_profile", {deck_id: deck?.id, ...profile})}>本层所有边统一为此配置</button>
        {!armorMode && <><button disabled={!validArmor || (region?.vertices_m.length ?? 0) <= 3} onClick={() => void onCommand("hull.remove_vertex", { ...context, vertex_index: i, merged_armor: { ...edge, material: chosenArmor, thickness_m: Number(thickness)/1000 } })}>删除端点并合并边</button>
        <small>合边保留原倾角，并使用上方所选材料和厚度；操作可撤销。</small></>}</>}
      {region && !drawing && !armorMode && <><button onClick={() => onSymmetry("left")}>以左侧为准</button>
        <button onClick={() => onSymmetry("right")}>以右侧为准</button>
        <small>预览后应用：用所选侧的轮廓及边装甲替换另一侧，不新增区域。</small>
        <button className="secondary" onClick={() => void onCommand("hull.remove_region", context)}>删除当前区域</button></>}
    </fieldset>
  </div>;
}
