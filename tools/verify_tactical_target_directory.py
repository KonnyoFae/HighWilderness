"""P2a.1c: exact old public fields plus independently checked new metadata."""
import argparse
import ast
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_gunnery as guns, realtime_view
from backend.high_wilderness_sidecar.tactical_target_directory import key
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7c4 import PublicOracle
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write

REFERENCE='352f3e5f5f8bca499a6b77cb27332057f2bd3be3'


def run(source,out,steps):
    if out.exists():raise ValueError('Choose a fresh evidence directory')
    out.mkdir(parents=True)
    path=Path(guns.__file__).relative_to(ROOT).as_posix()
    raw=subprocess.check_output(['git','show',REFERENCE+':'+path],cwd=ROOT)
    owner=next(n for n in ast.parse(raw.decode('utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='GunneryBattle')
    method=next(n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name=='view')
    scope=dict(guns.__dict__);exec(compile(ast.Module(body=[method],type_ignores=[]),REFERENCE+':view','exec'),scope)
    write(out/'source.json',dict(fingerprint(),reference_head=REFERENCE,reference_method='GunneryBattle.view',
        reference_file_sha256=sha256(raw).hexdigest(),verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Only view differs between variants; both use the current P2a.1b simulation. New field checked separately.'))
    public=PublicOracle(out);metadata=[];original_read=realtime_view.RealtimeViewService.read

    @contextmanager
    def implementation(legacy=False,counters=None):
        def read(service,*args,**kwargs):
            packet=original_read(service,*args,**kwargs)
            if not legacy:
                b=service.gunnery;g=packet['view']['gunnery'];summary=g['target_directory']
                expected=[dict(key=key(r.category,r.handle.target_id),target_id=r.handle.target_id,name=r.name,category=r.category)
                          for r in b.target_roster.frame.query(exclude_side=b._sides[b._direct_index])]
                assert list(summary['entries'])==expected
                assert summary['epoch']==b.session.world.epoch
                assert set(summary)=={'interface','epoch','revision','entries','remembered'}
                active={r['key'] for r in expected}
                contacts={key(c['kind'],c['id']) for s in g['observation']['ships'] for c in s['contacts']}
                assert {r['key'] for r in summary['remembered']}==contacts-active
                assert all(set(r)=={'key','target_id','name','category'} for r in (*summary['entries'],*summary['remembered']))
                metadata.append(canonical_sha256(summary))
                packet={**packet,'view':{**packet['view'],'gunnery':{k:v for k,v in g.items() if k!='target_directory'}}}
            public.record(legacy,packet,service)
            return packet
        with ExitStack() as stack:
            if legacy:stack.enter_context(patch.object(guns.GunneryBattle,'view',scope['view']))
            stack.enter_context(patch.object(realtime_view.RealtimeViewService,'read',read))
            yield

    with patch.object(baseline,'implementation',implementation):baseline.verify(source,out,steps)
    assert not public.pending
    write(out/'publications.json',dict(matched_reads=len(public.tape),sha256=canonical_sha256(public.tape),tape=public.tape,
        directory_checks=len(metadata),directory_tape_sha256=canonical_sha256(metadata),directory_tape=metadata))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('out',type=Path)
    p.add_argument('--all',action='store_true');p.add_argument('--steps',type=int,default=900);a=p.parse_args()
    cases=sorted(d.parent for d in a.source.glob('*/fixture.json')) if a.all else [a.source]
    for case in cases:run(case,a.out/case.name if a.all else a.out,a.steps)
