"""AV5 real ending records, independent ownership and retry-safe receipts."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sqlite3
import unittest
from backend.high_wilderness_sidecar import aviation_recovery as recovery,aviation_logistics as logistics,aviation_salvage as salvage
from backend.high_wilderness_sidecar import aviation_salvage_store as pools,persistent_ship as ps,tactical_settlement as settlement,tactical_test_scene as scene
from backend.high_wilderness_sidecar import battle_preparation as bp
from backend.high_wilderness_sidecar.preparation_transactions import PreparationStore
from backend.high_wilderness_sidecar.tactical_inventory import InventorySession
from backend.high_wilderness_sidecar.sessions import ResourceIndex
from tools import test_aviation_flight as fixtures


class SalvageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.FlightTests.setUpClass();cls.designs=fixtures.FlightTests.designs;cls.template=fixtures.FlightTests.template;cls.scenario=fixtures.FlightTests.scenario
        cls.index=ResourceIndex(Path(__file__).resolve().parents[1])
    battle=fixtures.FlightTests.battle
    command=fixtures.FlightTests.command

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=PreparationStore(self.temp.name,self.index);self.service=SimpleNamespace(store=self.store)

    def ending(self,*,destroyed=False,all_lost=False,ground=False):
        b,key=self.battle('e1')
        if not ground:
            self.command(b,'launch',[key])
            f=b.aviation.flights[key];f.update(position=(1234.,4321.),velocity=(0.,0.))
        w=logistics.Work(b.inventory.inventories[0]);w.plane(key)['crew'][0]['modifiers']={'speed':1.2,'radar_signature':.8};w.commit()
        if all_lost:
            from backend.high_wilderness_sidecar.tactical_devices import DeviceOperation
            world=b.session.world;operations=[]
            for n,s in enumerate(world.ships):
                mid=b.session._command_kernels[n].cic;kernel=b.session._device_kernels[n];i=kernel.by_id[mid]
                operations.append(DeviceOperation(world.epoch,s.ship_id,mid,s.devices.modules[i].sequence+1,'damage',kernel.seed.modules[i].maximum_durability_points,world.fixed_step+1,'closing'))
            b.step(device_operations=tuple(operations));self.assertEqual(b.ending['reason'],'draw')
        elif destroyed:
            f['hp']=0;b.step();b.withdraw()
        else:
            recovery.salvage(b.inventory.inventories[0],key,origin=salvage.origin(b.session.world,f['position'],f['layer'],'no_capacity'))
            del b.aviation.flights[key];b.withdraw()
        result=settlement.capture(b);settlement.validate_result(result)
        for d,row in zip(self.designs,result['ships']):
            self.store.create_ship(d,row['before']['state']['instance_id'])
            with self.store.connection() as db:self.store._write_ship(db,row['before'])
        self.store.stage(result)
        layout=scene.fresh();layout['revision']=1
        for side,row in zip(reversed(layout['sides']),result['ships']):
            key=row['after']['state']['instance_id'];side.update(flagship_instance_id=key,ships=[dict(instance_id=key,x_m=0.,y_m=0.,heading_rad=0.)])
        scene.save(self.service,layout,0)
        return b,result

    def request(self,packet=None):
        p=packet or pools.read(self.service);pool=p['pools'][0];r=p['receivers'][0]
        return dict(request_id='claim.test',pool_id=pool['pool_id'],pool_revision=pool['revision'],scene_revision=p['scene_revision'],
            receiver_instance_id=r['instance_id'],receiver_revision=r['revision'],entry_ids=[e['id'] for e in pool['entries'] if e['claimable']])

    def test_pending_then_atomic_save_and_restart_transfer_exactly_once(self):
        b,result=self.ending();pool=result['aviation_salvage'];self.assertEqual(len(pool['entries']),3)
        self.assertTrue(all(e['origin']['position_m']==[1234.,4321.] for e in pool['entries']))
        self.assertFalse(pools.read(self.service)['pools'])
        self.assertTrue(any(a['location']=='salvage' for a in b.inventory.inventories[0]._value['aviation']['manifest']['aircraft']))
        after=result['ships'][0]['after']['state']['aviation']['manifest'];self.assertFalse(after['aircraft']);self.assertFalse(after['personnel'])
        self.store.save(result['settlement_id']);self.store.save(result['settlement_id'])
        restarted=SimpleNamespace(store=PreparationStore(self.temp.name,self.index))
        self.assertEqual(len(pools.read(restarted)['pools']),1)
        p=self.request();preview=pools.claim(restarted,p,preview=True);self.assertFalse(preview['saved'])
        self.assertEqual(len(pools.read(restarted)['pools'][0]['entries']),3)
        receipt=pools.claim(restarted,p);self.assertTrue(receipt['saved']);self.assertEqual(pools.claim(restarted,p),receipt)
        self.assertFalse(pools.read(restarted)['pools'][0]['entries'])
        saved=self.store.load_ship(p['receiver_instance_id'],receipt['receiver_revision'])['state']
        self.assertEqual(len(saved['aviation']['manifest']['aircraft']),1);self.assertEqual(len(saved['aviation']['manifest']['personnel']),2)
        a=saved['aviation']['manifest']['aircraft'][0];self.assertEqual(a['loadout'],{});self.assertEqual(a['location'],'cargo')
        self.assertEqual(next(c['quantity'] for c in saved['cargo'] if c['good_id']=='cargo.aviation.self_defense'),2)
        self.assertEqual(saved['aviation']['manifest']['personnel'][0]['modifiers'],{'speed':1.2,'radar_signature':.8})
        self.store.save(result['settlement_id']);self.assertFalse(pools.read(restarted)['pools'][0]['entries'])

    def test_ship_and_pool_save_failure_rolls_back_and_is_retryable(self):
        _,result=self.ending();original=pools.write_pool
        def fail(*args):original(*args);raise sqlite3.OperationalError('disk fault after pool write')
        with patch.object(pools,'write_pool',side_effect=fail),self.assertRaises(ps.ContractError):self.store.save(result['settlement_id'])
        self.assertFalse(self.store.read(result['settlement_id'])['saved']);self.assertFalse(pools.read(self.service)['pools'])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT revision FROM ships WHERE id=?',('instance.flight.0',)).fetchone()[0],0)
        self.store.save(result['settlement_id']);p=self.request()
        with patch.object(pools,'write_pool',side_effect=fail),self.assertRaises(ps.ContractError):pools.claim(self.service,p)
        self.assertEqual(len(pools.read(self.service)['pools'][0]['entries']),3)
        self.assertEqual(self.store.load_ship(p['receiver_instance_id'],1)['state']['aviation']['manifest']['aircraft'],[])
        self.assertTrue(pools.claim(self.service,p)['saved'])

    def test_capacity_and_active_battle_reject_without_partial_receipt(self):
        _,result=self.ending();self.store.save(result['settlement_id']);p=self.request()
        with patch.object(InventorySession,'_capacity',return_value=0),self.assertRaises(ps.ContractError):pools.claim(self.service,p)
        self.assertEqual(len(pools.read(self.service)['pools'][0]['entries']),3)
        with self.store.connection() as db:db.execute('INSERT INTO battle_instance_claims VALUES (?,?)',(p['receiver_instance_id'],'scene.other'))
        with self.assertRaises(ps.ContractError):pools.claim(self.service,p)
        with self.store.connection() as db:db.execute('DELETE FROM battle_instance_claims')
        self.assertTrue(pools.claim(self.service,p)['saved'])

    def test_unloaded_ammunition_counts_toward_capacity_and_pilots_can_be_claimed_separately(self):
        _,result=self.ending();self.store.save(result['settlement_id']);p=self.request()
        # 25 m³ fits an empty E1 but not its two returned rounds (0.2 m³).
        with patch.object(InventorySession,'_capacity',return_value=25_000_000),self.assertRaises(ps.ContractError):pools.claim(self.service,p)
        packet=pools.read(self.service);self.assertEqual(len(packet['pools'][0]['entries']),3)
        p['entry_ids']=[e['id'] for e in packet['pools'][0]['entries'] if e['kind']=='pilot']
        self.assertEqual(pools.claim(self.service,p)['pilots'],2)
        p=self.request();p['request_id']='claim.remaining'
        self.assertEqual(pools.claim(self.service,p)['aircraft'],1)
        self.assertFalse(pools.read(self.service)['pools'][0]['entries'])

    def test_stale_revision_and_modified_retry_fail_without_duplication(self):
        _,result=self.ending();self.store.save(result['settlement_id']);p=self.request();wrong={**p,'receiver_revision':0}
        with self.assertRaises(ps.ContractError):pools.claim(self.service,wrong)
        receipt=pools.claim(self.service,p)
        for wrong in ({**p,'entry_ids':p['entry_ids'][:1]},{**p,'request_id':'different'}):
            with self.assertRaises(ps.ContractError):pools.claim(self.service,wrong)
        self.assertEqual(pools.claim(self.service,p),receipt)

    def test_legacy_migration_is_explicit_atomic_and_retry_safe(self):
        # Emulate an AV4 saved result: salvage identities still belong to the source ship.
        with patch.object(salvage,'extract',return_value=[]):_,result=self.ending()
        self.assertNotIn('aviation_salvage',result)
        result['ships'][0]['after']['state']['aviation'].pop('salvage_origins',None)
        # Restage before committing to represent a historical record without position data.
        with self.store.connection() as db:
            payload,digest=self.store._encoded(result)
            db.execute('UPDATE results SET payload=?,digest=? WHERE id=?',(payload,digest,result['settlement_id']))
        self.store.save(result['settlement_id']);packet=pools.read(self.service)
        self.assertEqual(packet['legacy_count'],3);self.assertFalse(packet['pools'])
        original=pools.write_pool
        def fail(*args):original(*args);raise sqlite3.OperationalError('migration write fault')
        with patch.object(pools,'write_pool',side_effect=fail),self.assertRaises(ps.ContractError):pools.migrate(self.service,dict(request_id='migrate.once'))
        self.assertEqual(pools.read(self.service)['legacy_count'],3);self.assertFalse(pools.read(self.service)['pools'])
        receipt=pools.migrate(self.service,dict(request_id='migrate.once'));self.assertEqual(receipt['migrated'],3)
        self.assertEqual(pools.migrate(self.service,dict(request_id='migrate.once')),receipt)
        self.assertEqual(pools.migrate(self.service,dict(request_id='migrate.again'))['migrated'],0)
        packet=pools.read(self.service);self.assertEqual(packet['legacy_count'],0)
        self.assertTrue(all(e['origin'] is None for e in packet['pools'][0]['entries']))
        self.assertEqual(self.store.read(result['settlement_id'])['result'],result)
        self.assertEqual(pools.claim(self.service,self.request())['pilots'],2)

    def test_pool_rejects_malformed_assets_and_duplicate_identity(self):
        _,result=self.ending();pool=result['aviation_salvage']
        for bad in (None,[],42):
            invalid=deepcopy(pool);invalid['entries'][0]['asset']=bad
            with self.assertRaises(ps.ContractError):salvage.validate(invalid)
        invalid=deepcopy(result)
        invalid['ships'][0]['after']['state']['aviation']['manifest']['aircraft'].append(deepcopy(pool['entries'][0]['asset']))
        with self.assertRaises(ps.ContractError):salvage.validate_result(invalid)

    def test_enemy_assets_and_enemy_receivers_are_rejected(self):
        _,result=self.ending();self.store.save(result['settlement_id']);p=self.request()
        with self.assertRaises(ps.ContractError):pools.claim(self.service,{**p,'receiver_instance_id':'instance.flight.1'})
        with self.store.connection() as db:
            raw=db.execute('SELECT payload,digest FROM aviation_salvage_pools').fetchone();v=self.store._decode(*raw)
            v['player_side_id']='side.enemy';pools.write_pool(db,self.store,v)
        self.assertTrue(all(not e['claimable'] for e in pools.read(self.service)['pools'][0]['entries']))
        with self.assertRaises(ps.ContractError):pools.claim(self.service,p)

    def test_destroyed_airframe_not_recoverable_but_unique_ace_pilots_survive(self):
        _,result=self.ending(destroyed=True);entries=result['aviation_salvage']['entries']
        self.assertEqual(len(entries),2);self.assertTrue(all(e['kind']=='pilot' for e in entries))
        self.assertEqual(entries[0]['origin']['reason'],'aircraft_destroyed')
        self.assertEqual(result['ships'][0]['after']['state']['aviation']['manifest']['aircraft'][0]['condition'],'destroyed')
        self.store.save(result['settlement_id']);r=pools.claim(self.service,self.request());self.assertEqual(r['pilots'],2)

    def test_all_lost_pool_remains_after_source_ship_records_removed(self):
        _,result=self.ending(all_lost=True);self.store.save(result['settlement_id']);p=pools.read(self.service)
        self.assertEqual(len(p['pools'][0]['entries']),3);self.assertFalse(p['receivers'])
        self.store.create_ship(self.designs[2],'instance.rescue')
        with self.store.connection() as db:
            db.execute('DELETE FROM ships WHERE id IN (?,?)',('instance.flight.0','instance.flight.1'));layout=scene.read(db,self.store)
        layout['revision']+=1
        for s in layout['sides']:s.update(flagship_instance_id='instance.rescue' if s['id']=='player' else None,ships=[dict(instance_id='instance.rescue',x_m=0.,y_m=0.,heading_rad=0.)] if s['id']=='player' else [])
        scene.save(self.service,layout,1)
        r=pools.claim(self.service,self.request());self.assertEqual((r['aircraft'],r['pilots']),(1,2))

    def test_ground_airframe_crash_stays_damaged_after_ending_and_receipt(self):
        _,result=self.ending(all_lost=True,ground=True);pool=result['aviation_salvage'];salvage.validate(pool)
        plane=next(e for e in pool['entries'] if e['kind']=='aircraft')
        self.assertEqual(plane['asset']['condition'],'damaged');self.assertEqual(plane['origin']['reason'],'ship_loss')
        target=InventorySession(self.designs[2].resources,ps.fresh_instance(self.designs[2].resources,'rescue'))
        salvage.receive(target,pool,{e['id'] for e in pool['entries']})
        self.assertEqual(target._value['aviation']['manifest']['aircraft'][0]['condition'],'damaged')
        self.assertEqual(len(target._value['aviation']['manifest']['personnel']),2)

    def test_wounded_personnel_use_temporary_cargo_without_healing_or_buff_stacking(self):
        b,key=self.battle('e1');self.command(b,'launch',[key]);inv=b.inventory.inventories[0]
        recovery.salvage(inv,key);w=logistics.Work(inv);w.m['personnel'][0].update(health='wounded',modifiers={'speed':1.3});w.commit()
        entries=salvage.extract(inv,'source','side.player',None,{})
        pool=dict(entries=entries,sources=[dict(instance_id='source',catalog=inv._definition['aviation']['catalog'])])
        target=InventorySession(self.designs[1].resources,ps.fresh_instance(self.designs[1].resources,'rescue'))
        with patch.object(target,'_alive',return_value=False),patch.object(target,'_capacity',return_value=100_000_000),patch('backend.high_wilderness_sidecar.aviation_resources.quarters_available',return_value={}):salvage.receive(target,pool,{e['id'] for e in entries})
        people=target._value['aviation']['manifest']['personnel'];self.assertEqual(people[0]['health'],'wounded');self.assertEqual(people[0]['modifiers'],{'speed':1.3})
        self.assertTrue(all(p['housing']=='temporary_cargo' for p in people));self.assertEqual(target._value['aviation']['manifest']['aircraft'][0]['loadout'],{})
        from backend.high_wilderness_sidecar.aviation_manifest import supply_inputs
        self.assertEqual(supply_inputs(target._value['aviation']['manifest'])[0]['temporary_cargo'],2)

    def test_no_hangar_pilot_receipt_uses_shared_officer_beds_and_rejects_dead_people(self):
        _,result=self.ending();pool=result['aviation_salvage'];ids={e['id'] for e in pool['entries'] if e['kind']=='pilot'}
        target=InventorySession(self.designs[2].resources,ps.fresh_instance(self.designs[2].resources,'rescue'))
        hangars={f['module_id'] for f in target._definition['aviation']['facilities'] if f['kind']=='aircraft_hangar'}
        target._health={k:0. if k in hangars else v for k,v in target._health.items()}
        salvage.receive(target,pool,ids)
        people=target._value['aviation']['manifest']['personnel']
        self.assertTrue(all(p['housing']=='quarters' and p['module_id']=='aviation.officer_quarters' for p in people))
        target.snapshot()
        invalid=deepcopy(pool);next(e for e in invalid['entries'] if e['kind']=='pilot')['asset']['health']='dead'
        with self.assertRaises(ps.ContractError):salvage.validate(invalid)

    def test_catalog_mismatch_rejects_plane_but_allows_separate_pilot_rescue(self):
        _,result=self.ending();self.store.save(result['settlement_id']);p=self.request()
        with self.store.connection() as db:
            raw=db.execute('SELECT payload,digest FROM aviation_salvage_pools').fetchone();v=self.store._decode(*raw)
            v['sources'][0]['catalog']['aircraft'][0]['speed_mps']+=1;pools.write_pool(db,self.store,v)
        with self.assertRaises(ps.ContractError):pools.claim(self.service,p)
        packet=pools.read(self.service);p['entry_ids']=[e['id'] for e in packet['pools'][0]['entries'] if e['kind']=='pilot']
        self.assertEqual(pools.claim(self.service,p)['pilots'],2)

    def test_claimed_plane_can_prepare_launch_and_recover_in_second_battle(self):
        _,result=self.ending();self.store.save(result['settlement_id']);r=pools.claim(self.service,self.request())
        record=self.store.load_ship('instance.flight.0',r['receiver_revision']);inv=InventorySession(self.designs[0].resources,ps.parse_instance(record['state'],self.designs[0].resources))
        spec=inv._definition['aviation'];h=next(f['module_id'] for f in spec['facilities'] if f['kind']=='aircraft_hangar');c=next(f['module_id'] for f in spec['facilities'] if f['kind']=='aircraft_catapult');key=inv._value['aviation']['manifest']['aircraft'][0]['id']
        stock={'cargo:'+g['id']:10000 for g in inv._definition['goods']}
        logistics.prepare(inv,[dict(kind='prepare',aircraft_id=key,module_id=h,loadout={'p1':'self_defense','p2':'self_defense'}),dict(kind='load',aircraft_id=key,module_id=c)],stock)
        record['state']=inv.snapshot().to_dict()
        from backend.high_wilderness_sidecar import tactical_encounter as encounter
        enemy=bp.new_record(self.designs[1],'instance.next.enemy');rows=[];sides=[]
        for n,(d,saved) in enumerate(((self.designs[0],record),(self.designs[1],enemy))):
            side=dict(side_id='side.player' if n==0 else 'side.enemy',fleet_id='fleet.next.'+str(n),flagship_instance_id=saved['state']['instance_id'],ships=[])
            member=dict(instance_id=saved['state']['instance_id'],revision=saved['state']['revision'],deployment=dict(x_m=0.,y_m=30000.*n,heading_rad=0.));side['ships'].append(member);sides.append(side);rows.append((side,member,d,saved))
        req=dict(interface=encounter.INTERFACE,encounter_id='encounter.second',world_id='world.second',world_revision=0,player_side_id='side.player',sides=sides)
        b=encounter.build(req,rows,self.template,self.scenario)[0];self.command(b,'launch',[key]);b.step();self.assertEqual(b.aviation.flights[key]['modifiers']['speed'],1.2)
        b.withdraw();second=settlement.capture(b);settlement.validate_result(second)
        self.assertNotIn('aviation_salvage',second);self.assertEqual(len(second['ships'][0]['after']['state']['aviation']['manifest']['personnel']),2)


if __name__=='__main__':unittest.main()
