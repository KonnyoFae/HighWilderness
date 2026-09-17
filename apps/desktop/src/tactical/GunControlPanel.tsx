import type { TacticalView, GunView } from './model';
import type { GunIntent } from './gunnery';
import { gunStatus, qualityLabel } from './gunnery';
import { ammunitionName } from './ammunition';
import { goodName } from './preparation';
import { GunBallisticsPanel } from './GunBallisticsPanel';
import { HEIGHT_LAYERS, isHeightLayer, layerName } from './layers';

export function commonGunValue<T>(guns: GunView[], read: (gun: GunView) => T): T | undefined {
  return guns.length && guns.every(g => read(g) === read(guns[0])) ? read(guns[0]) : undefined;
}

export function GunControlPanel({ view, shipId, weaponId, groupId, disabled, onWeapon, onGroup, onCommand }: {
  view: TacticalView; shipId: string; weaponId: string | null; groupId: string | null; disabled: boolean;
  onWeapon: (id: string) => void; onGroup: (id: string) => void; onCommand: (intent: GunIntent) => void;
}) {
  const all = view.snapshot.gunnery?.weapons.filter(g => g.ship_id === shipId) ?? [];
  const groups = view.snapshot.gunnery?.groups?.filter(g => g.ship_id === shipId) ?? [];
  const group = groups.find(g => g.group_id === groupId);
  const guns = all.filter(g => group ? group.weapon_ids.includes(g.module_id) : g.module_id === weaponId);
  const first = guns[0];
  const geometry = view.geometry.ships.find(s => s.id === shipId);
  const ownLayer = view.snapshot.ships.find(s => s.id === shipId)?.height_layer;
  const enemies = view.geometry.ships.filter(s => s.side_id !== geometry?.side_id &&
    view.snapshot.ships.some(p => p.id === s.id && p.hull_integrity > 0 && !p.wreck && p.physical_status !== 'exited'));
  const mode = commonGunValue(guns, g => g.mode);
  const layer = commonGunValue(guns, g => g.attack_layer ?? '');
  const recipe = commonGunValue(guns, g => g.selected_recipe_id);
  const targetId = commonGunValue(guns, g => g.target_ship_id);
  const targetModule = commonGunValue(guns, g => g.target_module_id);
  const targetGeometry = enemies.find(s => s.id === targetId);
  const policy = commonGunValue(guns, g => g.target_policy ?? 'automatic');
  const defense = commonGunValue(guns,g=>g.point_defense??false);
  const sameFlight = commonGunValue(guns, g => JSON.stringify(g.ballistics)) !== undefined;
  const name = (g: GunView) => geometry?.modules.find(m => m.id === g.module_id)?.name ?? g.module_id;
  return <fieldset aria-label="普通火炮操作" className="gun-control-panel">
    <legend>旗舰武器组</legend>
    <p>普通火炮自动选敌，30 毫米炮默认专注近防。选择武器组后可统一下令，指定敌舰目标会切回普通炮击。</p>
    <div className="gun-group-list" aria-label="武器组">
      {groups.map(g => <button key={g.group_id} aria-pressed={groupId === g.group_id} onClick={() => onGroup(g.group_id)}>
        <strong>{g.name}</strong><span>{g.weapon_ids.length} 门</span>
      </button>)}
    </div>
    <details className="gun-individual-selection"><summary>单炮细调</summary>
      <label>所控火炮<select aria-label="所控火炮" value={group ? '' : weaponId ?? ''} onChange={e => onWeapon(e.target.value)}>
        <option value="">选择一门火炮单独下令</option>
        {all.map(g => <option key={g.module_id} value={g.module_id}>{name(g)} · {g.module_id}</option>)}
      </select></label>
    </details>
    {!first ? <p>请选择武器组。</p> : <>
      <h4>{group ? `${group.name} · 整组 ${guns.length} 门` : `${name(first)} · 单炮`}</h4>
      <fieldset disabled={disabled} className="gun-orders" aria-label="所选武器命令">
      {guns.every(g=>g.point_defense_capable)&&<div aria-label="自动近防设置">
        <button aria-pressed={defense===true} onClick={()=>onCommand({kind:'point_defense',arguments:{enabled:defense!==true}})}>{defense===true?'关闭自动近防':'开启自动近防'}</button>
        <p>自动近防仅拦截预计撞上本舰或友舰的大型弹体，无威胁时待机。作用层仍由下方设置；相邻层射击照常降速。</p>
      </div>}
      <p className="gun-order-summary" role="status">{defense===true?'自动近防':mode === 'manual' ? '手动瞄准' : policy === 'automatic' ? '自动选敌' : policy === 'assigned' ? '指定目标' : policy === 'hold' ? '停止开火' : '组内指令不同'}
        {' · '}{targetId === undefined ? '成员目标不同' : targetGeometry?.name ?? '等待目标'}
        {targetModule ? ` / ${targetGeometry?.modules.find(m => m.id === targetModule)?.name ?? targetModule}` : ''}</p>
      <div className="editor-row">
        <button onClick={() => onCommand({kind:'auto_target',arguments:{}})}>恢复自动选敌</button>
        <button onClick={() => onCommand({kind:'clear',arguments:{}})}>停止开火</button>
        <label>火炮模式<select aria-label="火炮模式" value={mode ?? 'mixed'} onChange={e => onCommand({kind:'mode',arguments:{mode:e.target.value}})}>
          {!mode && <option value="mixed" disabled>组内模式不同</option>}
          <option value="auto">自动火控</option><option value="manual">手动瞄准</option>
        </select></label>
      </div>
      {mode === 'auto' && <>
        <p className="muted">点击敌舰指定整舰；点中模块则指定模块。指定目标会持续保留，超出射程时等待。</p>
        <details><summary>从列表指定目标</summary>
          <div className="editor-row">{enemies.map(s => <button key={s.id} onClick={() => onCommand({kind:'target',arguments:{ship_id:s.id,module_id:null}})}>瞄准{s.name}</button>)}</div>
          {targetGeometry && <label>指定模块<select aria-label="指定目标模块" value={targetModule === undefined ? 'mixed' : targetModule ?? ''}
            onChange={e => onCommand({kind:'target',arguments:{ship_id:targetGeometry.id,module_id:e.target.value || null}})}>
            {targetModule === undefined && <option value="mixed" disabled>成员模块目标不同</option>}
            <option value="">整舰</option>{targetGeometry.modules.map(m => <option key={m.id} value={m.id}>{m.name} · 第 {m.deck_level} 甲板 · {m.id}</option>)}
          </select></label>}
        </details>
      </>}
      {mode === 'manual' && <label>瞄准甲板（概率偏好）<select aria-label="瞄准甲板" value={commonGunValue(guns,g=>g.deck_level ?? '') ?? 'mixed'}
        onChange={e => onCommand({kind:'deck',arguments:{level:e.target.value === '' ? null : Number(e.target.value)}})}>
        <option value="mixed" disabled>成员设置不同</option>
        <option value="">不偏好特定甲板</option>
        {[...new Set(view.geometry.ships.flatMap(s=>s.decks.map(d=>d.level)))].sort((a,b)=>a-b).map(level=><option key={level} value={level}>第 {level} 甲板</option>)}
      </select></label>}
      {view.snapshot.gunnery?.deck_hit_policy && <div className="gun-deck-preference" aria-label="甲板瞄准规则">
        <p>炮弹接触舰艇时，按概率选择可命中的甲板；随后计算该甲板装甲和模块的实际受击。</p>
        <small>指定模块提高其所在甲板的命中权重，不保证命中该层或该模块。
          {view.snapshot.gunnery.deck_hit_policy.spanning_module_bonus === 'split' ? '跨甲板模块均分同一份加成。' : '跨甲板模块仅提高安装基底甲板的权重。'}</small>
        <p>{commonGunValue(guns,g=>JSON.stringify(g.aimed_deck_levels ?? [])) === undefined ? '成员的甲板瞄准偏好不同' :
          first.aimed_deck_levels?.length ? `当前偏好：${first.aimed_deck_levels.map(level=>`第 ${level} 甲板`).join('、')}` : '当前不偏好特定甲板'}</p>
      </div>}
      <div className="editor-row"><label>炮弹作用层<select aria-label="炮弹作用层" value={layer ?? 'mixed'}
        onChange={e => onCommand({kind:'layer',arguments:{layer:e.target.value || null}})}>
        {layer === undefined && <option value="mixed" disabled>成员设置不同</option>}
        <option value="">跟随本舰所在层</option>
        {HEIGHT_LAYERS.map(l=><option key={l} value={l} disabled={!isHeightLayer(ownLayer) || Math.abs(HEIGHT_LAYERS.indexOf(l)-HEIGHT_LAYERS.indexOf(ownLayer))>1}>{layerName(l)}</option>)}
      </select></label></div>
      {layer === undefined && <p>组内作用层不同，统一作用层后可从战场批量瞄准。</p>}
      {first.recipe_options && <label>下一批装填弹种<select aria-label="下一批装填弹种" value={recipe ?? 'mixed'}
        onChange={e=>onCommand({kind:'ammunition',arguments:{recipe_id:e.target.value}})}>
        {!recipe && <option value="mixed" disabled>成员弹种不同</option>}
        {first.recipe_options.filter(r=>guns.every(g=>g.recipe_options?.some(v=>v.id===r.id))).map(r=><option key={r.id} value={r.id}>{ammunitionName(r.id)}</option>)}
      </select></label>}
      <p>待发共 {guns.reduce((n,g)=>n+g.ready_rounds,0)} 发 · 已射击 {guns.reduce((n,g)=>n+g.shots,0)} 发</p>
      {guns.filter(g=>g.point_defense).map(g=><p key={g.module_id}>{name(g)}：{gunStatus[g.status]??g.status}
        {g.interception_target_id!=null?` · 弹体 #${g.interception_target_id} · 第 ${(g.interception_priority??0)+1} 优先档 · 尚需约 ${g.interception_needed_rounds??0} 发命中`:''}</p>)}
      {guns.some(g=>g.incendiary_effect==='surface')&&<p>表面燃烧弹：穿深很低，未击穿也可引燃舰外；火灾只在同一甲板相邻区域蔓延。</p>}
      {guns.some(g=>g.incendiary_effect==='internal')&&<p>旧型燃烧弹：击穿后尝试内部点燃。新导入舰船可装填表面燃烧弹。</p>}
      <p>本舰弹药资源 {first.ammo_resources} 点{recipe ? ` · 每门下一批消耗 ${first.batch_cost} 点，装填 ${first.batch_rounds} 发` : ''}</p>
      {recipe && first.recipe_options?.find(r=>r.id===recipe)?.cargo_costs.map(c=><p key={c.good_id}>{goodName(c.good_id)}每门每批 {c.quantity} 份
        （库存 {first.cargo?.find(g=>g.good_id===c.good_id)?.quantity ?? 0}，已预留 {first.cargo?.find(g=>g.good_id===c.good_id)?.reserved ?? 0}）</p>)}
      <small>命令作用于{group ? '整组' : '所选单炮'}。各炮独立转向、装填和开火；已装弹打空后换装。</small>
      {sameFlight ? <GunBallisticsPanel gun={first} ownLayer={ownLayer} hideLayerControl onCommand={onCommand} /> : <p>成员当前弹道不同，请展开查看各炮状态。</p>}
      <details className="gun-members"><summary>查看各炮状态（{guns.length} 门）</summary>{guns.map(g=><div key={g.module_id}>
        <strong>{name(g)} · {g.module_id}</strong>
        <p>{gunStatus[g.status] ?? g.status} · {qualityLabel[g.quality_reason] ?? g.quality_reason}</p>
        <p>目标：{view.geometry.ships.find(s=>s.id===g.target_ship_id)?.name ?? '未选定'} · {layerName(g.effective_layer)}</p>
        <p>待发 {g.ready_rounds} 发 · 装填 {(g.reload_steps/60).toFixed(1)} 秒 · 冷却 {(g.cooldown_steps/60).toFixed(1)} 秒 · 已射击 {g.shots} 发</p>
        <p>当前待发：{ammunitionName(g.loaded_recipe_id)}{g.loading_recipe_id ? ` · 装填中：${ammunitionName(g.loading_recipe_id)}` : ''}</p>
        {!sameFlight && g.ballistics && <p>初速 {Math.round(g.ballistics.effective_speed_mps)} 米/秒 · 参考射程 {(g.ballistics.reference_range_m/1000).toFixed(2)} 公里</p>}
        <button onClick={()=>onWeapon(g.module_id)}>单独调整此炮</button>
      </div>)}</details>
      </fieldset>
    </>}
  </fieldset>;
}
