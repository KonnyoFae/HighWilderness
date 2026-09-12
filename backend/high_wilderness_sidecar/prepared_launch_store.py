"""Durable entry identities and process lifetime lease, only at UI boundaries."""
import os
from . import persistent_ship as ps


class BattleLease:
    def __init__(self,directory):
        directory.mkdir(parents=True,exist_ok=True)
        self.file=(directory/'prepared-battle.lock').open('a+b')
        if self.file.tell()==0:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None

    def close(self):
        if self.file is not None:self.file.close();self.file=None


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS prepared_launches (id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, scene_id TEXT NOT NULL, status TEXT NOT NULL)')


def recover(store):
    """Only the holder of the OS lease may recover dead-process claims.

    A staged ending remains pending; unended battle rolls back to the saved
    entry state, including its already-paid preload. No live battle checkpoint
    is claimed. A second running backend cannot release the owner's claims.
    """
    lease=BattleLease(store.directory)
    if lease.file is None:return
    try:
        with store.connection() as db:
            setup(db)
            for key,scene in db.execute("SELECT id,scene_id FROM prepared_launches WHERE status='active'").fetchall():
                result=db.execute('SELECT committed FROM results WHERE id=?',('settlement.'+scene,)).fetchone()
                if result is None:
                    db.execute('DELETE FROM battle_instance_claims WHERE scene_id=?',(scene,))
                    status='interrupted'
                else:status='settled' if result[0] else 'pending'
                db.execute('UPDATE prepared_launches SET status=? WHERE id=?',(status,key))
    finally:lease.close()


def claim(store,launch_id,digest,scene_id,records,*,encounter=None,mapping=None):
    with store.connection() as db:
        setup(db)
        ps.need(db.execute('SELECT 1 FROM prepared_launches WHERE id=?',(launch_id,)).fetchone() is None,'$.launch_id','入战请求已经使用，请重新发起准备入战')
        store._unavailable(db,{r['state']['instance_id'] for r in records})
        for record in records:
            key=record['state']['instance_id']
            old=db.execute('SELECT payload,digest FROM ships WHERE id=?',(key,)).fetchone()
            ps.need(old is not None and store._decode(*old)==record,'$.revision','舰船已变化，请重新准备后入战')
            db.execute('INSERT INTO battle_instance_claims VALUES (?,?)',(key,scene_id))
        db.execute('INSERT INTO prepared_launches VALUES (?,?,?,?)',(launch_id,digest,scene_id,'active'))
        if encounter is not None:
            from .tactical_encounter import bind
            bind(db,store,encounter,scene_id,mapping)


def rollback_failed_attach(store,launch_id,scene_id):
    with store.connection() as db:
        ps.need(db.execute('SELECT 1 FROM results WHERE id=?',('settlement.'+scene_id,)).fetchone() is None,'$.scene_id','已生成结算，不能撤销认领')
        db.execute('DELETE FROM battle_instance_claims WHERE scene_id=?',(scene_id,))
        db.execute('DELETE FROM prepared_launches WHERE id=? AND scene_id=?',(launch_id,scene_id))
        from .tactical_encounter import setup
        setup(db)
        db.execute('DELETE FROM tactical_encounters WHERE scene_id=?',(scene_id,))
