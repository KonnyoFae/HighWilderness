"""Compare 7c.1b to frozen c0554da observation methods, retaining 7c.1a in both.

Reuses the 18-section oracle, input fixtures, AB/BA timing and cache clearing.
Reference code executes only in this test tool's isolated namespace.
"""
import argparse
import ast
from contextlib import contextmanager, ExitStack
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_observation as obs, tactical_ew as ew
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write


REFERENCE_HEAD='c0554da2beb88aff9a50268080559e43ddaf0e94'
REFERENCE_PATH=ROOT/'tools/tactical_7c1b_reference.py'
METHODS=('visible','plan','controllers','sources','view')
reference_globals=dict(obs.__dict__,blocked_channels=ew.blocked_channels)
exec(compile(REFERENCE_PATH.read_text(encoding='utf-8'),str(REFERENCE_PATH),'exec'),reference_globals)


def validate_reference():
    frozen={n.name:ast.dump(n) for n in ast.parse(REFERENCE_PATH.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)}
    original={}
    for filename,classname,names in (('tactical_observation.py','ObservationRuntime',METHODS),
                                    ('tactical_ew.py','ElectronicWarfare',('sensor_blocker',))):
        source=subprocess.check_output(['git','show',REFERENCE_HEAD+':backend/high_wilderness_sidecar/'+filename],cwd=ROOT).decode('utf-8')
        cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name==classname)
        original.update({n.name:ast.dump(n) for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names})
    assert frozen==original,'Frozen 7c.1b reference differs from c0554da'


@contextmanager
def implementation(legacy=False,counters=None):
    # Bind before patching; nested callers continue to use the chosen class methods.
    methods={name:reference_globals[name] if legacy else getattr(obs.ObservationRuntime,name) for name in METHODS}
    blocker=reference_globals['sensor_blocker'] if legacy else ew.ElectronicWarfare.sensor_blocker
    effect=obs.ObservationRuntime.effect;hypot=obs.hypot
    def measured(name,fn):
        def call(*args,**kw):
            counters[name]+=1
            return fn(*args,**kw)
        return call
    with ExitStack() as stack:
        for name,fn in methods.items():
            stack.enter_context(patch.object(obs.ObservationRuntime,name,measured(name,fn) if counters is not None else fn))
        stack.enter_context(patch.object(ew.ElectronicWarfare,'sensor_blocker',blocker))
        if counters is not None:
            stack.enter_context(patch.object(obs.ObservationRuntime,'effect',measured('effect',effect)))
            wrapped=measured('observation_hypot',hypot)
            stack.enter_context(patch.object(obs,'hypot',wrapped))
            stack.enter_context(patch.dict(reference_globals,hypot=wrapped))
        yield


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('verify','measure','interleaved'))
    parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    parser.add_argument('--steps',type=int,default=900);parser.add_argument('--pairs',type=int,default=3)
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Choose a fresh output directory')
    if args.pairs<1:raise ValueError('At least one measurement pair is required')
    validate_reference();args.out.mkdir(parents=True)
    provenance=fingerprint()
    provenance.update(reference_head=REFERENCE_HEAD,reference_methods_ast_verified=True,
        comparison='c0554da observation vs 7c.1b; both keep 7c.1a occlusion',
        tools_7c={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*.py')})
    write(args.out/'source.json',provenance)
    with patch.object(baseline,'implementation',implementation):
        if args.mode=='verify':baseline.verify(args.source,args.out,args.steps)
        elif args.mode=='measure':baseline.measure(args.source,args.out,args.pairs)
        else:baseline.interleaved(args.source,args.out)


if __name__=='__main__':main()
