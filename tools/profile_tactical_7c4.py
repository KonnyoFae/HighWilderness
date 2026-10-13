"""7c.4a frozen publication/wire comparison; no production imports this tool."""
import argparse
import ast
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import textwrap
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_gunnery as guns, tactical_observation as observation
from backend.high_wilderness_sidecar import tactical_missiles as missiles, tactical_aviation as aviation, server
from backend.high_wilderness_sidecar import realtime_view
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write
from tools.tactical_7c_oracle import plain, difference
from backend.high_wilderness_sidecar.persistent_ship import canonical_sha256

REFERENCE_HEAD = '4cb15c04d40f9ed68f06c8fbca949b9c5c0c6986'
SPECS = ((guns, guns.GunneryBattle, ('view',), 'gunnery'),
         (observation, observation.ObservationRuntime, ('view',), 'observation'),
         (missiles, missiles.MissileRuntime, ('view',), 'missiles'),
         (aviation, aviation.AviationRuntime, ('view',), 'aviation'),
         (server, server.SidecarServer, ('execute', 'serve'), 'server'))
REFERENCES = {}
PUBLIC_ORACLE = None
WIRE_TIMING = False


def references(freeze=False):
    for module, owner, names, label in SPECS:
        filename = Path(module.__file__).name
        source = subprocess.check_output(['git', 'show', REFERENCE_HEAD+':backend/high_wilderness_sidecar/'+filename], cwd=ROOT).decode('utf-8')
        cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == owner.__name__)
        expected = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names}
        path = ROOT/f'tools/tactical_7c4_reference_{label}.py'
        if freeze:
            if path.exists(): raise ValueError('Reference already exists: '+str(path))
            lines = source.splitlines()
            path.write_text('\n\n\n'.join(textwrap.dedent('\n'.join(lines[n.lineno-1:n.end_lineno])) for n in expected.values())+'\n', encoding='utf-8', newline='\n')
        actual = {n.name: n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n, ast.FunctionDef)}
        assert {k:ast.dump(v) for k,v in expected.items()} == {k:ast.dump(v) for k,v in actual.items()}, label
        namespace = dict(module.__dict__)
        exec(compile(path.read_text(encoding='utf-8'),str(path),'exec'), namespace)
        REFERENCES[label] = namespace


class PublicOracle:
    def __init__(self, out):
        self.out = out; self.pending = []; self.tape = []

    def record(self, legacy, packet, service):
        b = service.gunnery
        epochs = {b.session.world.epoch:'<battle-epoch>', **{v.epoch:f'<inventory-epoch-{n}>' for n,v in enumerate(b.inventory.inventories)}}
        value = plain(packet, epochs)
        if legacy:
            self.pending.append(value)
        else:
            expected = self.pending.pop(0)
            if error := difference(expected, value):
                write(self.out/'public-mismatch.json', dict(read=len(self.tape), difference=error))
                raise AssertionError(error)
            self.tape.append(canonical_sha256(value))


def request():
    return dict(interface='gaotian.web-bridge/v1alpha1',kind='request',backend_instance_id='backend.7c.compare',
                request_id='req.1',session_id=None,expected_revision=None,method='tactical.realtime.read',params={})


@contextmanager
def implementation(legacy=False, counters=None):
    original_read = realtime_view.RealtimeViewService.read
    def read(self, *args, **kwargs):
        result = original_read(self,*args,**kwargs)
        if PUBLIC_ORACLE is not None: PUBLIC_ORACLE.record(legacy,result,self)
        if WIRE_TIMING:
            if legacy: server.encode_message(server.response_for(request(),result=result))
            else: server.realtime_response(request(),result,wire=True)
        return result
    with ExitStack() as stack:
        if legacy:
            for module,owner,names,label in SPECS:
                for name in names: stack.enter_context(patch.object(owner,name,REFERENCES[label][name]))
        if PUBLIC_ORACLE is not None or WIRE_TIMING:
            stack.enter_context(patch.object(realtime_view.RealtimeViewService,'read',read))
        yield


def main():
    global PUBLIC_ORACLE, WIRE_TIMING
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=('freeze','verify','measure'))
    parser.add_argument('source',type=Path,nargs='?');parser.add_argument('out',type=Path,nargs='?')
    parser.add_argument('--steps',type=int,default=900);parser.add_argument('--pairs',type=int,default=3)
    args=parser.parse_args();references(args.mode=='freeze')
    if args.mode=='freeze':return
    if args.source is None or args.out is None:parser.error('source and out are required')
    if args.pairs < 1:parser.error('pairs must be positive')
    if args.out.exists():raise ValueError('Choose a fresh output directory')
    args.out.mkdir(parents=True)
    provenance=fingerprint();provenance.update(reference_head=REFERENCE_HEAD,reference_methods_ast_verified=True,
        tools_7c={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*') if p.is_file()},
        read_timing_scope='public read plus response validation/encoding, without queues or physical transport')
    write(args.out/'source.json',provenance)
    with patch.object(baseline,'implementation',implementation):
        if args.mode=='verify':
            PUBLIC_ORACLE=PublicOracle(args.out)
            baseline.verify(args.source,args.out,args.steps)
            assert not PUBLIC_ORACLE.pending
            write(args.out/'publications.json',dict(matched_reads=len(PUBLIC_ORACLE.tape),sha256=canonical_sha256(PUBLIC_ORACLE.tape),tape=PUBLIC_ORACLE.tape))
        else:
            WIRE_TIMING=True
            baseline.measure(args.source,args.out,args.pairs)


if __name__=='__main__': main()
