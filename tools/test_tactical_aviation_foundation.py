"""AV0 real catalog/compiler, capacity, archive and aviation ownership checks."""
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import json
import unittest

from 高天荒野舰艇数据契约 import ContractError, ModuleCapability, ResourceReference
import 高天荒野舰艇人员舱容量 as housing
from backend.high_wilderness_sidecar import aviation_catalog as aviation, aviation_manifest as manifest
from backend.high_wilderness_sidecar import battle_preparation as bp, outfit_documents, outfits, persistent_ship as ps
from backend.high_wilderness_sidecar.preparation_policy import load_current
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools.test_battle_preparation import fixture, ROOT


class AviationDataTests(unittest.TestCase):
    def setUp(self):
        self.catalog = aviation.load()

    def test_fixed_airframes_volume_and_personnel(self):
        for key, slots, pilots, volume in [('e1',1,2,25), ('f1',1,1,25), ('b1',2,3,50)]:
            m = aviation.model(self.catalog, 'gtw.aircraft.'+key)
            self.assertEqual((m['berth_slots'],m['pilots_required']), (slots,pilots))
            self.assertEqual(aviation.cargo_volume_cm3(self.catalog,m['id']),volume*1_000_000)
        self.assertEqual(self.catalog['cargo_volume_m3']['3'],125)
        changed = deepcopy(self.catalog); changed['cargo_volume_m3']['3']=75
        with self.assertRaises(ContractError): aviation.validate(changed)

    def test_explicit_hardpoint_compatibility_and_e1_defence(self):
        aviation.validate_loadout(self.catalog,'gtw.aircraft.e1',dict(p1='self_defense',p2='self_defense'))
        for key, loadout in [('e1',{}), ('e1',dict(p1='small_missile',p2='self_defense')),
                             ('f1',dict(p1='large_missile')), ('b1',dict(p9='large_bomb'))]:
            with self.subTest(key=key,loadout=loadout), self.assertRaises(ContractError):
                aviation.validate_loadout(self.catalog,'gtw.aircraft.'+key,loadout)
        aviation.validate_loadout(self.catalog,'gtw.aircraft.b1',dict(p1='large_guided_bomb'))

    def test_bombs_have_no_propulsion_and_inherit_velocity(self):
        for payload in self.catalog['payloads']:
            if 'bomb' in payload['kind']:
                self.assertFalse(payload['powered']); self.assertTrue(payload['inherits_launch_velocity'])
        bad=deepcopy(self.catalog); next(p for p in bad['payloads'] if p['kind']=='guided_bomb')['powered']=True
        with self.assertRaises(ContractError): aviation.validate(bad)

    def test_invalid_catalog_fields_duplicates_and_nonfinite_rejected(self):
        for mutate in (lambda x:x['aircraft'].append(deepcopy(x['aircraft'][0])),
                       lambda x:x['aircraft'][0].update(speed_mps=float('nan')),
                       lambda x:x['aircraft'][0].update(unknown=True),
                       lambda x:x['aircraft'][0]['hardpoints'][0]['compatible_payloads'].append('large_missile')):
            bad=deepcopy(self.catalog); mutate(bad)
            with self.assertRaises((ContractError,ValueError)): aviation.validate(bad)

    def plane(self):
        return dict(id='plane.e1.1',model_id='gtw.aircraft.e1',home_ship_id='ship.carrier',location='catapult',
            ship_id='ship.carrier',module_id='catapult.1',condition='intact',
            loadout=dict(p1='self_defense',p2='self_defense'),cannon_rounds=0,
            crew=[dict(id='pilot.'+str(i),health='fit',modifiers={'speed':1.1} if i==1 else {}) for i in (1,2)])

    def test_airborne_crew_leave_ship_and_supply_inputs_keep_temporary_people(self):
        value=manifest.empty(self.catalog); value['aircraft']=[self.plane()]
        value['personnel']=[dict(id='pilot.3',health='wounded',modifiers={},housing='temporary_cargo',ship_id='ship.carrier',module_id=None)]
        value=manifest.validate(value,self.catalog)
        self.assertEqual(manifest.supply_inputs(value),[dict(ship_id='ship.carrier',total=3,fit=2,wounded=1,temporary_cargo=1)])
        flying=deepcopy(value); flying['aircraft'][0].update(location='airborne',ship_id=None,module_id=None)
        manifest.validate(flying,self.catalog)
        self.assertEqual(manifest.supply_inputs(flying)[0]['total'],1)
        self.assertEqual(value['personnel'],flying['personnel'])
        # Saving aircrew preserves unique ace identity; no copy stays on mother ship.
        self.assertEqual(manifest.validate(ps.decode(ps.encode(flying)),self.catalog),flying)
        duplicate=dict(value['aircraft'][0]['crew'][0],housing='hangar',ship_id='ship.carrier',module_id='hangar.1')
        flying['personnel'].append(duplicate)
        with self.assertRaises(ContractError): manifest.validate(flying,self.catalog)

    def test_recovered_planes_are_empty_and_destroyed_pilots_cannot_be_salvaged_alive(self):
        value=manifest.empty(self.catalog); plane=self.plane(); value['aircraft']=[plane]
        plane.update(location='cargo',module_id=None)
        with self.assertRaises(ContractError): manifest.validate(value,self.catalog)
        plane.update(crew=[],loadout={})
        manifest.validate(value,self.catalog)
        value['personnel']=[dict(id='pilot.dead',health='dead',modifiers={},housing='salvage',ship_id=None,module_id=None)]
        with self.assertRaises(ContractError): manifest.validate(value,self.catalog)

    def test_salvage_preserves_remaining_loadout_and_catalog_binding_is_exact(self):
        value=manifest.empty(self.catalog); plane=self.plane();value['aircraft']=[plane]
        plane.update(location='salvage',ship_id=None,module_id=None,crew=[],condition='damaged')
        manifest.validate(value,self.catalog)
        changed=deepcopy(self.catalog);changed['aircraft'][0]['speed_mps']+=1
        with self.assertRaises(ContractError):manifest.validate(value,changed)


class AviationFacilitiesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index=ResourceIndex(ROOT);cls.catalog=outfits.module_catalog(cls.index)

    def cabin(self, key, reference, version=1):
        return SimpleNamespace(id=key,prototype=self.catalog.module(ResourceReference(reference,version)))

    def test_shared_capacity_is_not_multiplied_by_personnel_types(self):
        cabins=[self.cabin('a','gtw.module.fixture.crew_quarters',2),self.cabin('b','gtw.module.aviation.officer_quarters')]
        occupants, missing=housing.allocate(cabins,dict(ordinary=40,pilot=20))
        self.assertFalse(missing);self.assertEqual(sum(occupants['a'].values()),40)
        _, missing=housing.allocate(cabins,dict(ordinary=40,pilot=20,officer=1))
        self.assertEqual(sum(missing.values()),1)
        _, missing=housing.allocate(cabins,dict(ordinary=41))
        self.assertEqual(missing,{'ordinary':1})
        # A greedy allocator can use ordinary beds for officers and reject pilots.
        _, missing=housing.allocate(cabins,dict(ordinary=30,officer=10,pilot=20))
        self.assertFalse(missing)
        provision=housing.bounded_crew(cabins,dict(ordinary=100,pilot=30,officer=10))
        self.assertEqual(sum(provision.values()),60)

    def test_legacy_typed_capacity_and_invalid_shared_definition(self):
        cabins=[self.cabin('old','gtw.module.fixture.crew_quarters')]
        _, missing=housing.allocate(cabins,dict(ordinary=11,officer=1))
        self.assertEqual(missing,{'ordinary':1});self.assertFalse(housing.has_shared(cabins))
        with self.assertRaises(ContractError):
            ModuleCapability.parse(dict(kind='crew_quarters',shared_capacity=20,capacities=[dict(crew_type='pilot',capacity=40)]),'$')

    def test_all_hangars_compile_exact_installation_and_capacity(self):
        for key, cells, slots, stations in [('small',8,5,1),('medium',12,8,3),('advanced',8,10,3)]:
            document,_,_=fixture(self.index)
            document['outfit']['modules']=[m for m in document['outfit']['modules'] if m['prototype']['id'] in ('gtw.module.fixture.cic','gtw.module.fixture.lift_fuel_tank')]
            document['outfit']['modules'].append(dict(id='hangar.test',prototype=dict(id='gtw.module.aviation.hangar.'+key,version=1),
                placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=[0 if key=='medium' else 1,9],rotation_deg=0)))
            compiled=outfits.document(document['outfit'],self.index,document['hull_binding']['hull']).compile()
            m=next(m for m in compiled.instances if m.id=='hangar.test');c=m.prototype.capability.to_dict()
            self.assertEqual(len(m.internal_cells),cells)
            self.assertEqual((c['ready_slots'],c['workstations'],c['pilot_capacity']),(slots,stations,slots*2))

    def test_catapult_clearance_blocks_top_equipment_and_rotates(self):
        for rotation,anchor,block in [(0,[0,17],[0,20]),(90,[1,18],[4,18])]:
            document,_,_=fixture(self.index)
            document['outfit']['modules']=[m for m in document['outfit']['modules'] if m['prototype']['id'] in ('gtw.module.fixture.cic','gtw.module.fixture.lift_fuel_tank')]
            document['outfit']['modules'].append(dict(id='catapult.test',prototype=dict(id='gtw.module.aviation.catapult',version=1),
                placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=anchor,rotation_deg=rotation)))
            doc=outfits.document(document['outfit'],self.index,document['hull_binding']['hull']);doc.compile()
            document['outfit']['modules'].append(dict(id='block',prototype=dict(id='gtw.module.aviation.command',version=1),
                placement=dict(kind='grid',deck_id='deck.0',anchor_half_cell=block,rotation_deg=0)))
            with self.assertRaises(ContractError):
                outfits.document(document['outfit'],self.index,document['hull_binding']['hull']).compile()

    def test_new_preparation_upgrades_quarters_without_adding_people_and_old_archive_restores(self):
        old=next(i for i in outfit_documents.catalog_generations(self.index) if not any(d['id']==outfit_documents.AVIATION_CATALOG for d,_ in i.resources.values()))
        document,deployment,policy=fixture(old)
        historic=bp.compile_design(document,old,deployment,policy,ship_id='ship.old')
        self.assertEqual(bp.restore_design(historic.archive(),self.index),historic)
        current=bp.compile_design(document,self.index,deployment,load_current(ROOT),ship_id='ship.new')
        cabins=[m for m in current.snapshot.outfit.instances if m.prototype.category=='crew_quarters']
        self.assertTrue(cabins);self.assertTrue(all(m.prototype.capability.to_dict()['shared_capacity']==40 for m in cabins))
        record=bp.new_record(current,'instance.new')
        self.assertEqual(record['state']['crew'],bp.new_record(historic,'instance.old')['state']['crew'])
        self.assertEqual(bp.restore_design(current.archive(),self.index),current)

    def test_real_sortie_rejects_combined_shared_overbooking(self):
        document,deployment,_=fixture(self.index)
        # Two crew quarters offer 80 total places, not 80 for each type.
        for row in deployment['crew']:
            row['count']=30
        with self.assertRaisesRegex(ContractError,'shared_crew_capacity_exceeded'):
            bp.compile_design(document,self.index,deployment,load_current(ROOT),ship_id='ship.overbooked')

    def test_indexed_carrier_fixture_compiles_prepares_and_restores(self):
        source=next(s for d,s in self.index.resources.values() if d['id']=='gtw.outfit.aviation.foundation')
        compiled=outfits.document(source,self.index).compile()
        self.assertFalse(housing.allocate(compiled.instances,dict(compiled.standard_crew))[1])
        _,deployment,_=fixture(self.index)
        deployment['crew']=[dict(crew_type=k,count=v) for k,v in housing.bounded_crew(compiled.instances,dict(compiled.standard_crew)).items()]
        design=bp.compile_design(source,self.index,deployment,load_current(ROOT),ship_id='ship.carrier')
        self.assertEqual(bp.restore_design(design.archive(),self.index),design)
        record=bp.new_record(design,'instance.carrier')
        self.assertEqual(record['state']['cargo'],[])  # No free planes or pilots.
        self.assertFalse(any(r['crew_type']=='pilot' and r['count'] for r in record['state']['crew']))


if __name__=='__main__': unittest.main()
