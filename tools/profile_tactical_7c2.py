"""Incremental 7c.2 comparison; both variants retain 7c.1b observation reuse."""
import argparse
import ast
from contextlib import contextmanager, ExitStack
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import aviation_flight as flight, aviation_combat as combat
from backend.high_wilderness_sidecar import aviation_logistics as logistics, aviation_recovery as recovery
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write


REFERENCE_HEAD='c0554da2beb88aff9a50268080559e43ddaf0e94'
MODULES=(flight,combat,logistics)
REFERENCES={}
for module in MODULES:
    name=module.__name__.split('.')[-1]
    path=ROOT/f'tools/tactical_7c2_reference_{name}.py'
    namespace=dict(module.__dict__)
    exec(compile(path.read_text(encoding='utf-8'),str(path),'exec'),namespace)
    REFERENCES[name]=namespace


def validate_reference():
    for module in MODULES:
        name=module.__name__.split('.')[-1]
        path=ROOT/f'tools/tactical_7c2_reference_{name}.py'
        frozen={n.name:ast.dump(n) for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.FunctionDef)}
        source=subprocess.check_output(['git','show',REFERENCE_HEAD+':backend/high_wilderness_sidecar/'+name+'.py'],cwd=ROOT).decode('utf-8')
        original={n.name:ast.dump(n) for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name in frozen}
        assert frozen==original,'Frozen 7c.2 reference differs from c0554da: '+name


@contextmanager
def implementation(legacy=False,counters=None):
    methods=[(flight,'candidates'),(flight,'finish'),(combat,'apply_hits'),(logistics,'advance'),(logistics.Work,'unload')]
    selected=[]
    for owner,name in methods:
        module=logistics if owner is logistics.Work else owner
        fn=REFERENCES[module.__name__.split('.')[-1]][name] if legacy else getattr(owner,name)
        selected.append((owner,name,fn))
    def measured(label,fn):
        def call(*args,**kwargs):
            counters[label]+=1
            return fn(*args,**kwargs)
        return call
    with ExitStack() as stack:
        for owner,name,fn in selected:stack.enter_context(patch.object(owner,name,fn))
        if counters is not None:
            for owner,name in ((logistics.Work,'__init__'),(recovery,'receiving'),(recovery,'can_receive'),(logistics,'countdown')):
                stack.enter_context(patch.object(owner,name,measured('Work' if name=='__init__' else name,getattr(owner,name))))
            # Count outer copy requests, not recursive calls inside copy.py.
            for module in MODULES:
                name=module.__name__.split('.')[-1];fn=measured(name+'.deepcopy',module.deepcopy)
                stack.enter_context(patch.object(module,'deepcopy',fn))
                stack.enter_context(patch.dict(REFERENCES[name],deepcopy=fn))
        yield


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('verify','measure'))
    parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    parser.add_argument('--steps',type=int,default=900);parser.add_argument('--pairs',type=int,default=3)
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Choose a fresh output directory')
    if args.pairs<1:raise ValueError('At least one measurement pair is required')
    validate_reference();args.out.mkdir(parents=True)
    provenance=fingerprint()
    provenance.update(reference_head=REFERENCE_HEAD,reference_methods_ast_verified=True,
        comparison='c0554da aviation vs 7c.2; both retain 7c.1b observation and 7c.1a occlusion',
        tools_7c={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*.py')})
    write(args.out/'source.json',provenance)
    with patch.object(baseline,'implementation',implementation):
        if args.mode=='verify':baseline.verify(args.source,args.out,args.steps)
        else:baseline.measure(args.source,args.out,args.pairs)


if __name__=='__main__':main()
