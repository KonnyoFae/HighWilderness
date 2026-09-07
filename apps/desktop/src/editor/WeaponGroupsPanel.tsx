import { useEffect, useState } from "react";
import type { HullCommand, SessionSnapshot } from "./model";
import { arcText, mergeGroups, sameWeapon, splitGroup } from "./weaponGroups";
import type { WeaponControl, WeaponGroup } from "./weaponGroups";

export function WeaponGroupsPanel({ session, busy, onCommand, onLocalDraft, onSelectWeapon }: {
  session: SessionSnapshot; busy: boolean; onCommand: HullCommand; onLocalDraft: (dirty: boolean) => void; onSelectWeapon: (id: string) => void;
}) {
  const value = session.preview.model.weapon_control as WeaponControl | undefined;
  const control = value && ["gaotian.weapon-control-view/v1alpha1", "gaotian.weapon-control-view/v2alpha1"].includes(value.interface) ? value : undefined;
  const [groups, setGroups] = useState<WeaponGroup[]>(control?.groups ?? []);
  const [selected, setSelected] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [name, setName] = useState("新武器组");
  const [targetId, setTargetId] = useState("");
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const group = groups.find(g => g.id === selected) ?? groups[0];
  const targets = group ? groups.filter(g => g.id !== group.id && sameWeapon(g, group)) : [];
  const target = targets.find(g => g.id === targetId) ?? targets[0];
  useEffect(() => { setGroups(control?.groups ?? []); setMembers([]); setDirty(false); setError(""); }, [session.revision, control]);
  useEffect(() => { onLocalDraft(dirty); return () => onLocalDraft(false); }, [dirty, onLocalDraft]);
  function operation(run: () => WeaponGroup[]) {
    try { setGroups(run()); setMembers([]); setDirty(true); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  return <section className="weapon-groups" aria-label="武器组配置">
    <h3>武器组</h3>
    {control?.interface === "gaotian.weapon-control-view/v1alpha1" && <p role="status">后台仍使用旧射界提示。请显式重启后台并恢复草稿，以启用水平遮挡计算。</p>}
    <p>同型号、同版本的武器默认成组，可以拆分、改名或重新合并。弹药、目标和开火指令将在出击与战术阶段设置。</p>
    {!control ? <p>请更新后台并重新打开设计，以加载武器组。</p> : !group ? <p>尚未安装武器，请先从模块目录放置武器。</p> : <fieldset disabled={busy}>
      <div className="editor-row">
        <label>当前武器组<select aria-label="当前武器组" value={group.id} onChange={e => { setSelected(e.target.value); setMembers([]); }}>{groups.map(g => <option key={g.id} value={g.id}>{g.name} · {g.weapon_instance_ids.length} 件</option>)}</select></label>
        <label>组名<input aria-label="武器组名称" maxLength={80} value={group.name} onChange={e => { setGroups(groups.map(g => g.id === group.id ? { ...g, name: e.target.value } : g)); setDirty(true); }} /></label>
      </div>
      <p className="muted">{group.prototype.id} · v{group.prototype.version} · {dirty ? "尚未应用修改" : control.grouping === "automatic" ? "默认分组" : "已配置分组"}</p>
      <div className="weapon-members">{group.weapon_instance_ids.map(id => <div key={id}>
        <label><input type="checkbox" aria-label={`拆出 ${id}`} checked={members.includes(id)} onChange={e => setMembers(e.target.checked ? [...members, id] : members.filter(m => m !== id))} />{id}</label>
        <button disabled={dirty} onClick={() => onSelectWeapon(id)}>在画布中选中</button>
        <span>{arcText(control.arcs.find(a => a.instance_id === id))}</span>
      </div>)}</div>
      <div className="editor-row">
        <label>新组名<input aria-label="拆分后的组名" value={name} maxLength={80} onChange={e => setName(e.target.value)} /></label>
        <button disabled={!members.length || members.length === group.weapon_instance_ids.length} onClick={() => operation(() => splitGroup(groups, group.id, members, name))}>将勾选武器拆成新组</button>
      </div>
      <div className="editor-row">
        <label>合并到<select aria-label="合并目标组" value={target?.id ?? ""} disabled={!target} onChange={e => setTargetId(e.target.value)}>{!target && <option value="">没有同型号的其他组</option>}{targets.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
        <button disabled={!target} onClick={() => operation(() => mergeGroups(groups, group.id, target!.id))}>合并当前组</button>
      </div>
      {dirty && <div className="editor-row"><button disabled={groups.some(g => !g.name.trim())} onClick={async () => {
        if (await onCommand("outfit.set_weapon_groups", { groups })) { setDirty(false); setError(""); }
      }}>应用武器组</button><button onClick={() => { setGroups(control.groups); setMembers([]); setDirty(false); setError(""); }}>取消分组修改</button></div>}
    </fieldset>}
    {error && <p role="alert">{error}</p>}
    <p className="muted">水平射界只按更高层船壳计算，贴边方向也禁止开火；不计其他设备遮挡。扇区大小不表示射程，开火仍须满足弹药、冷却和火控条件。</p>
  </section>;
}
