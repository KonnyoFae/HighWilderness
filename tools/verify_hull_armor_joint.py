"""A5 reproducible joint calibration. Writes only isolated report artifacts."""
import argparse
from copy import deepcopy
from dataclasses import replace, asdict
import json
from math import cos, radians, isclose
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.test_battle_preparation import fixture
from tools.test_structure_thickness import uniform
from backend.high_wilderness_sidecar import outfits, outfit_documents, tactical_layers
from backend.high_wilderness_sidecar import battle_preparation as bp, tactical_ammunition
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from backend.high_wilderness_sidecar.structural_durability import compile_durability
from 高天荒野舰艇装甲外飘 import upgrade_armor_source
from 高天荒野舰艇编辑器领域层 import HullEditorDocument
from 高天荒野舰艇船坞后勤与战略工时 import _hull_material_bill
from 高天荒野舰艇炮弹与甲弹公式 import ArmorState, armor_tilt_incidence_deg, resolve_armor_impact
from 高天荒野舰艇运行时参数编译器 import STANDARD_GRAVITY_MPS2


def source(index, thickness_mm=15, angle=45, material='gtw.material.base_armor.armor_steel'):
    document, deployment, policy = fixture(index)
    hull = uniform(document['hull_binding']['hull'], thickness_mm / 1000)
    upgrade_armor_source(hull)
    for edge in hull['decks'][0]['regions'][0]['edge_armor']:
        edge.update(thickness_m=.05, flare_angle_deg=angle, material=dict(id=material, version=1))
    # The comparison holds this equipment constant, including at zero tilt.
    document['outfit']['modules'] = [m for m in document['outfit']['modules'] if m['placement']['kind'] != 'side']
    document['hull_binding'] = outfit_documents.bind(hull, index)
    return document, deployment, policy


def design(index, thickness_mm=15, angle=45, material='gtw.material.base_armor.armor_steel'):
    document, deployment, policy = source(index, thickness_mm, angle, material)
    return bp.compile_design(document, index, deployment, policy, ship_id='ship.armor.joint')


def measure(index, thickness_mm, angle, material, equipment):
    document, _, _ = source(index, thickness_mm, angle, material.reference.id)
    hull = HullEditorDocument(document['hull_binding']['hull'], index.registry).compile()
    mass = hull.hull_mass_kg + equipment.module_mass_kg
    inertia = hull.hull_inertia_kg_m2 + equipment.module_inertia_kg_m2
    navigation = tactical_layers.entry_navigation(mass, equipment.lift_force_n)
    bill, work = _hull_material_bill(SimpleNamespace(hull=hull), index.registry)
    materials = index.registry.structures | index.registry.base_armors
    cost = sum(amount * materials[ref].cost_coefficient for ref, amount in bill.items())
    return dict(structure_mm=thickness_mm, base_flare_deg=angle, base_armor=material.reference.id,
        base_armor_name=material.name, dry_mass_kg=mass, armor_mass_kg=hull.base_armor_mass_kg,
        structure_mass_kg=hull.structure_mass_kg, yaw_inertia_kg_m2=inertia,
        structure_hp=compile_durability(hull, index.registry).maximum_points,
        armor_hp=sum(r[-1] for r in hull.local_armor_durability_proxy),
        lift_reserve_ratio=equipment.lift_force_n/(mass*STANDARD_GRAVITY_MPS2)-1,
        adjacent_layer_seconds=navigation.base_duration_s,
        acceleration_per_meganewton=1e6/mass, yaw_acceleration_per_meganewton_metre=1e6/inertia,
        safe_longitudinal_mps2=hull.safe_longitudinal_mps2,
        safe_lateral_mps2=hull.safe_lateral_mps2,
        exposed_cells=sum(len(d.exposed_top_cells) for d in hull.decks),
        internal_cells=sum(len(d.internal_cells) for d in hull.decks),
        material_cost_proxy=cost, construction_work_units=work,
        radar_distance_factors=[((hull.hull_rcs_cache.directions[a].total_m2*equipment.coating_rcs_multiplier
            +equipment.known_external_rcs_m2)/1000)**.25 for a in (0,90)],
        hull_source_sha256=hull.source_sha256)


def penetration_matrix(index):
    from backend.high_wilderness_sidecar.realtime_view import RealtimeViewService
    policy=load_current(ROOT)
    scenario=RealtimeViewService('backend.armor.calibration',ROOT)._template()[1]
    profiles=tactical_ammunition.compile_profiles(scenario.projectile_catalog.profile('gtw.munition.fixture.76mm.standard'))
    rows=[]
    for material in index.registry.base_armors.values():
        for caliber in (30,50,75,120):
            for kind in ('ordinary','armor_piercing','incendiary'):
                id=f'projectile.3a.{caliber}mm.{kind}'
                version=max(p['version'] for p in policy['projectiles'] if p['id']==id)
                profile=profiles[id,version].penetration
                for angle in (0,30,45,60):
                    for bearing in (0,30,60):
                        for speed in (600,1000,2000):
                            result=resolve_armor_impact(profile,ArmorState(material.protection_coefficient,50.,1.),
                                speed,armor_tilt_incidence_deg(bearing,cos(radians(angle))))
                            rows.append(dict(material=material.reference.id,caliber=caliber,kind=kind,version=version,
                                tilt=angle,bearing=bearing,speed=speed,**asdict(result)))
    return rows


def matrix(index):
    equipment=design(index,15,0).snapshot.outfit
    steel=next(m for m in index.registry.base_armors.values() if m.name=='装甲钢')
    grid=[measure(index,t,a,steel,equipment) for t in (15,25,50,100) for a in (0,30,45,60)]
    materials=[measure(index,25,a,m,equipment) for m in index.registry.base_armors.values() for a in (0,30,45,60)]
    # Compare independently varying inputs, not just successful serialization.
    for a in (0,30,45,60):
        group=[r for r in grid if r['base_flare_deg']==a]
        assert all(isclose(r['armor_mass_kg'],group[0]['armor_mass_kg'],rel_tol=1e-12,abs_tol=1e-6) for r in group)
        assert len({tuple(r['radar_distance_factors']) for r in group})==1
        assert all(x['dry_mass_kg']<y['dry_mass_kg'] and x['structure_hp']<y['structure_hp'] for x,y in zip(group,group[1:]))
    for t in (15,25,50,100):
        group=[r for r in grid if r['structure_mm']==t]
        assert len({r['structure_hp'] for r in group})==1
        assert all(x['dry_mass_kg']<y['dry_mass_kg'] and x['material_cost_proxy']<y['material_cost_proxy'] for x,y in zip(group,group[1:]))
    for a in (0,30,45,60):
        assert len({tuple(r['radar_distance_factors']) for r in materials if r['base_flare_deg']==a})==1
    return dict(scope='155m reference hull; all decks share selected structure; only base armor varies; identical equipment and 30 MN lift',
        structure_and_tilt=grid,materials=materials,penetration=penetration_matrix(index))


def workload(index, steps):
    from tools.profile_tactical_fire_control import run
    frozen=json.loads((ROOT/'artifacts/tactical-fire-control-f1f2-20260920/fixture.json').read_text(encoding='utf-8'))
    copies=[]
    for angle in (0,45):
        scene=deepcopy(frozen)
        for row in scene['ships']:
            archive=row['archive']; document=archive['document'];hull=uniform(document['hull_binding']['hull'],.025)
            upgrade_armor_source(hull)
            occupied={(m['placement']['deck_id'],m['placement']['region_id'],m['placement']['edge_index'])
                for m in document['outfit']['modules'] if m['placement']['kind']=='side'}
            # The saved fixture has zero armor. Add matching 20 mm bow plates
            # in both cases; only their angle differs. Keep mounted sides intact.
            for deck in hull['decks']:
                if not deck['is_base']:continue
                for region in deck['regions']:
                    vertices=region['vertices_m']
                    candidates=[(i,(a[1]+b[1])/2) for i,(a,b) in enumerate(zip(vertices,vertices[1:]+vertices[:1]))
                                if (deck['id'],region['id'],i) not in occupied]
                    bow=max(y for _,y in candidates)
                    for i,y in candidates:
                        if abs(y-bow)<1e-8:region['edge_armor'][i].update(thickness_m=.02,flare_angle_deg=angle)
            document['hull_binding']=outfit_documents.bind(hull,index)
            d=bp.compile_design(document,index,archive['deployment'],archive['policy'],ship_id=archive['ship_id'])
            assert d.snapshot.hull.armor_geometry.has_flare==bool(angle)
            row['archive']=d.archive()
            r=bp.new_record(d,row['record']['state']['instance_id'])
            # Preserve the saved test inventory, but bind fresh hull/armor identity.
            for key in ('weapons','magazines','cargo'):
                r['state'][key]=deepcopy(row['record']['state'][key])
            row['record']=bp.validate_record(r,d)
        result=run(scene,index,dict(name=f'joint_{angle}',speed=100,accelerate=True,stock='loaded',distance=6000),
            'optimized',steps,runtime='f3')
        result['flared_edges_per_ship']=[sum(bool(e['flare_angle_deg']) for d in r['archive']['document']['hull_binding']['hull']['decks']
            for region in d['regions'] for e in region['edge_armor']) for r in scene['ships']]
        for key in ('trace','inventory','threat_trace'):result.pop(key)
        copies.append(result)
        print(json.dumps(dict(angle=angle,steps=result['steps'],shots=result['shots'],timing=result['warm_step']),ensure_ascii=False),flush=True)
    assert all(r['shots']>0 and r['peak_projectiles']>100 and r['steps']>300 for r in copies)
    return copies


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--steps',type=int,default=7200)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True);index=ResourceIndex(ROOT)
    result=matrix(index)
    (args.out/'calibration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Calibration matrix saved',flush=True)
    result=workload(index,args.steps)
    (args.out/'workload.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
