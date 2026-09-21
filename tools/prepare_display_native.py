"""Copy the current production backend/resources into an isolated desktop root.

The shipped executable is unchanged. HIGH_WILDERNESS_REPO_ROOT selects this copy,
so its normal .local/tactical store cannot touch the user's persistent fleet.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tools.joint_combat_fixture import create

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(); target = args.out.resolve()
    if target.exists(): raise ValueError('Use a fresh isolated native test root')
    if not target.is_relative_to(ROOT/'.local'): raise ValueError('Test root must be under .local')
    target.mkdir(parents=True)
    sources = {}
    for pattern in ('*.py', '*.json', '*.md'):
        for source in ROOT.glob(pattern):
            shutil.copy2(source, target/source.name)
            sources[source.name] = sha256(source.read_bytes()).hexdigest()
    for folder in ('backend', 'contracts', '舰艇数据'):
        shutil.copytree(ROOT/folder, target/folder, ignore=shutil.ignore_patterns('__pycache__', '报告'))
    for source in (ROOT/'backend').rglob('*.py'):
        copied=target/source.relative_to(ROOT)
        assert copied.read_bytes()==source.read_bytes()
        sources[str(source.relative_to(ROOT))]=sha256(source.read_bytes()).hexdigest()
    create(target/'.local/tactical')
    # Keep both fleets outside normal automatic engagement while exercising a
    # deliberate player VLS salvo at an empty point through ordinary commands.
    # This lets the native lifecycle check run for 120 s with finite stock.
    from backend.high_wilderness_sidecar.server import SidecarServer
    from backend.high_wilderness_sidecar import tactical_test_scene as scene
    service=SidecarServer('backend.d5.native-setup',settlement_dir=target/'.local/tactical').preparation
    with service.store.connection() as db: value=scene.read(db,service.store)
    revision=value['revision'];value['revision']+=1;value['distance_m']=50000
    scene.save(service,value,revision)
    (target/'source-hashes.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(root=str(target),production_backend_files=sum(k.startswith('backend') for k in sources),
                         fixture='four finite-stock ships, two legal SCIC fleets, 50 km start distance; no combat patches'),ensure_ascii=False))


if __name__=='__main__': main()
