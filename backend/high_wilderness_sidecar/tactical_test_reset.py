"""Explicit all-test-state reset, serialized with the tactical worker.

Only tactical database rows are removed. Editor designs and recovery are not
part of this store. Receipts survive resets so a lost reply cannot erase a new run.
"""
from . import persistent_ship as ps
from .prepared_launch_store import BattleLease

CAPABILITY = 'tactical.reset_test_state'
TABLES = ('results', 'ships', 'battle_instance_claims', 'tactical_encounters',
          'preparation_designs', 'preparation_supplies', 'preparations',
          'prepared_launches', 'preparation_drafts', 'preparation_imports',
          'tactical_test_scene', 'tactical_test_supply_updates', 'preparation_actions')


def reset(server, params):
    ps.obj(params, 'reset_id scope', '$.params')
    ps.identifier(params['reset_id'], '$.reset_id')
    ps.need(params['scope'] == 'all_tactical_test_state', '$.scope', '需要明确清空全部战术测试数据')
    live = server.realtime
    owned = live._prepared_lease
    lease = owned or BattleLease(live.store.directory)
    ps.need(lease.file is not None, '$.reset', '另一个战术窗口仍在使用测试舰队，请先关闭那个窗口后重试清空')
    try:
        with live.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tactical_test_resets (id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)')
            previous = db.execute('SELECT payload,digest FROM tactical_test_resets WHERE id=?', (params['reset_id'],)).fetchone()
            if previous:
                return live.store._decode(*previous)
            # Prepare replacements before committing the deletion; neither
            # constructor starts a scene or modifies editor state.
            from .realtime_view import RealtimeViewService
            from .tactical import TacticalService
            fresh = RealtimeViewService(server.instance_id, live.root, clock=live.clock, settlement_dir=live.store.directory)
            fresh.preparation_store = server.preparation.store
            tactical = TacticalService(server.instance_id, server.tactical.root)
            present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {}
            for table in TABLES:
                if table in present:
                    counts[table] = db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    db.execute(f'DELETE FROM {table}')  # fixed internal allowlist, never caller names
            result = dict(reset_id=params['reset_id'], cleared=True, mode='editor', paused=True, removed=counts)
            payload, digest = live.store._encoded(result)
            db.execute('INSERT INTO tactical_test_resets VALUES (?,?,?)', (params['reset_id'], payload, digest))
        server.realtime, server.tactical = fresh, tactical
        live._prepared_lease = None
        lease.close()
        return result
    finally:
        # Failed resets keep the active owner's lease and in-memory scene intact.
        if owned is None:
            lease.close()
