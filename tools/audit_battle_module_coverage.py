"""Read-only module/entry audit; writes evidence only, never edits saves or rules."""
import json
from collections import Counter
from pathlib import Path

from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, outfits
from backend.high_wilderness_sidecar.simplified_propulsion import AXES, direction_error
from backend.high_wilderness_sidecar.preparation_policy import CURRENT_PATH, load_current
from 高天荒野舰艇数据契约 import ContractError, canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / CURRENT_PATH
GROUPS = {'cargo_hold', 'ammunition_magazine', 'weapon', 'damage_control'}


def audit_document(document, index, policy, label):
    row = {'source': label, 'source_sha256': canonical_sha256(document), 'editor_compile': False, 'battle_compile': False}
    try:
        source, binding = outfit_documents.unpack(document, index)
        compiled = outfits.document(source, index, binding['hull'] if binding else None).compile()
        row['editor_compile'] = True
        row['module_count'] = len(compiled.instances)
        row['prototype_counts'] = dict(Counter(m.prototype.reference.id for m in compiled.instances))
        rules = {(r['prototype']['id'], r['prototype']['version']) for r in policy['modules']}
        row['missing_bindings'] = [dict(instance_id=m.id, prototype=m.prototype.reference.to_dict(), name=m.prototype.name)
            for m in compiled.instances if m.prototype.category in GROUPS
            and (m.prototype.reference.id, m.prototype.reference.version) not in rules]
        row['non_cardinal_engines'] = [dict(instance_id=m.id, name=m.prototype.name,
            category=m.prototype.category, direction=list(m.actuator.direction_body))
            for m in compiled.instances if m.actuator and m.actuator.direction_body not in AXES]
        row['direction_issues'] = [dict(instance_id=m.id, message=direction_error(m.prototype.category, m.actuator.direction_body))
            for m in compiled.instances if m.actuator and direction_error(m.prototype.category, m.actuator.direction_body)]
        capacity = dict(compiled.crew_capacity)
        crew = [dict(crew_type=k, count=min(v, capacity.get(k, 0))) for k, v in compiled.standard_crew]
        remote = next((m.id for m in compiled.instances if m.prototype.category == 'remote_core'), None)
        use_remote = not any(c['count'] for c in crew) and remote is not None
        deployment = dict(id='deployment.preparation.technical.v1', version=1, crew=crew,
            fuel_units=sum(m.prototype.capability.to_dict()['fuel_capacity_units'] for m in compiled.instances if m.prototype.category == 'lift_fuel_tank'),
            height_layer='upper', control_mode='remote_core' if use_remote else 'crewed',
            active_remote_core_instance_id=remote if use_remote else None)
        design = bp.compile_design(document, index, deployment, policy, ship_id='ship.audit.design')
        row['battle_compile'] = True
        row['battle_resources'] = {key: len(design.resources.definition().get(key, []))
            for key in ('holds', 'magazines', 'weapons', 'damage_controls', 'fuel_tanks')}
    except ContractError as exc:
        row['error'] = str(exc)
    return row


def main():
    index = ResourceIndex(ROOT)
    policy = load_current(ROOT)
    rules = {(r['prototype']['id'], r['prototype']['version']): r for r in policy['modules']}
    timing = {(r['prototype']['id'], r['prototype']['version']): r for r in policy['propulsion_timing']}
    modules, documents = [], []
    for descriptor, source in index.resources.values():
        if descriptor['kind'] == 'ModulePrototypeCatalog':
            for m in source['modules']:
                key = m['id'], m['version']
                rule = rules.get(key)
                modules.append(dict(id=m['id'], version=m['version'], name=m['name'], category=m['category'],
                    catalog=descriptor['id'], capability=m['capability'], damage_responses=m['damage_responses'],
                    binding_required=m['category'] in GROUPS, binding=rule['binding'] if rule else None,
                    binding_hash_matches=canonical_sha256(m) == rule['prototype_sha256'] if rule else None,
                    propulsion_timing=timing.get(key)))
        elif descriptor['kind'] == 'OutfitPlan':
            documents.append(audit_document(source, index, policy, descriptor['name']))
    for path in sorted((ROOT/'artifacts/o3c').glob('*.json')):
        source = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(source, dict) and 'outfit' in source and 'hull_binding' in source:
            documents.append(audit_document(source, index, policy, str(path.relative_to(ROOT))))
    result = dict(scope='Current editor catalogs and current preparation policy; compile audit, not combat acceptance',
        policy=str(POLICY.relative_to(ROOT)), policy_sha256=canonical_sha256(policy),
        module_count=len(modules), categories=dict(Counter(m['category'] for m in modules)),
        missing_binding_prototypes=[m['id'] for m in modules if m['binding_required'] and m['binding'] is None],
        modules=modules, documents=documents)
    output = ROOT/'artifacts/module-battle-repair/compile-report.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'modules'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
