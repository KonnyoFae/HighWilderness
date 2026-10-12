"""7c.3 exact state comparison and incremental AB/BA/AB timing."""
import argparse
import ast
from contextlib import contextmanager, ExitStack
from hashlib import sha256
from pathlib import Path
import subprocess
from unittest.mock import patch

from backend.high_wilderness_sidecar import tactical_missile_defense as defense, tactical_damage as damage, missile_guidance as guidance
from tools import profile_tactical_7c as baseline
from tools.profile_tactical_7b import fingerprint
from tools.tactical_7b_fixture import ROOT, write


REFERENCE_HEAD='3a4dc2c24db47af18f408ab6c6b0e29dccafe928'
SPECS=((defense,defense,'prepare_all','missile'),(damage,damage.DamageKernel,'contacts_on_path','damage'))
REFERENCES={}
for module,owner,name,label in SPECS:
    path=ROOT/f'tools/tactical_7c3_reference_{label}.py';namespace=dict(module.__dict__)
    exec(compile(path.read_text(encoding='utf-8'),str(path),'exec'),namespace)
    REFERENCES[label]=namespace


def validate_reference():
    for module,owner,name,label in SPECS:
        frozen=ast.parse((ROOT/f'tools/tactical_7c3_reference_{label}.py').read_text(encoding='utf-8'))
        source=subprocess.check_output(['git','show',REFERENCE_HEAD+':backend/high_wilderness_sidecar/'+module.__name__.split('.')[-1]+'.py'],cwd=ROOT).decode('utf-8')
        expected=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name==name)
        actual=next(n for n in frozen.body if isinstance(n,ast.FunctionDef) and n.name==name)
        assert ast.dump(actual)==ast.dump(expected),'Frozen 7c.3 reference differs from 3a4dc2c: '+name


@contextmanager
def implementation(legacy=False,counters=None):
    selected=[(owner,name,REFERENCES[label][name] if legacy else getattr(owner,name)) for module,owner,name,label in SPECS]
    def measured(label,fn):
        def call(*args,**kwargs):
            counters[label]+=1
            return fn(*args,**kwargs)
        return call
    with ExitStack() as stack:
        for owner,name,fn in selected:stack.enter_context(patch.object(owner,name,measured(name,fn) if counters is not None else fn))
        if counters is not None:
            for module,name,reference in ((defense,'commitments','missile'),(damage,'segment','damage'),
                                          (damage,'_segment_aabb_entry_fraction','damage')):
                fn=measured(name,getattr(module,name));stack.enter_context(patch.object(module,name,fn))
                stack.enter_context(patch.dict(REFERENCES[reference],{name:fn}))
            for name in ('contact','blocked_channels'):
                stack.enter_context(patch.object(guidance,name,measured('guidance.'+name,getattr(guidance,name))))
        yield


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=('verify','measure'))
    parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    parser.add_argument('--steps',type=int,default=900);parser.add_argument('--pairs',type=int,default=3)
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Choose a fresh output directory')
    if args.pairs<1:raise ValueError('At least one measurement pair is required')
    validate_reference();args.out.mkdir(parents=True)
    provenance=fingerprint()
    provenance.update(reference_head=REFERENCE_HEAD,reference_methods_ast_verified=True,
        comparison='3a4dc2c vs 7c.3; both retain 7c.1a, 7c.1b and 7c.2',
        tools_7c={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tools').glob('*7c*.py')})
    write(args.out/'source.json',provenance)
    with patch.object(baseline,'implementation',implementation):
        if args.mode=='verify':baseline.verify(args.source,args.out,args.steps)
        else:baseline.measure(args.source,args.out,args.pairs)


if __name__=='__main__':main()
