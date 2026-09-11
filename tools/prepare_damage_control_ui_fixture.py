"""Synthetic wounded ship for D1d browser QA; never modifies player saves."""
import argparse
import json
from pathlib import Path
from backend.high_wilderness_sidecar.server import SidecarServer
from backend.high_wilderness_sidecar import battle_preparation as bp
from tools.test_battle_preparation import fixture


def prepare(directory):
    directory.mkdir(parents=True,exist_ok=False)
    server=SidecarServer('backend.e3bbrowser',settlement_dir=directory/'store')
    service=server.preparation
    doc,_,_=fixture(server.editor.index)
    file=directory/'outfit.json';file.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    grant=server.editor.store.bind(str(file.resolve()),'open',None,None)
    service.import_ship(dict(instance_id='instance.d1d.browser',source=dict(kind='file',value=grant['destination_handle'])))
    with service.store.connection() as db:
        record=service.store._decode(*db.execute('SELECT payload,digest FROM ships WHERE id=?',('instance.d1d.browser',)).fetchone())
        archive=service.store._decode(*db.execute('SELECT payload,digest FROM preparation_designs WHERE id=?',('instance.d1d.browser',)).fetchone())
        record['state']['hull_integrity_fraction']=.99
        for m in record['state']['modules']:
            if m['module_id']=='custom.cargo':m['durability_points']=50
        record['state']['fires']=[dict(module_id='custom.cargo',intensity_units=1000,remaining_steps=3600)]
        design=bp.restore_design(archive,server.editor.index)
        record=bp.validate_record(record,design)
        service.store._write_ship(db,record)
    (directory/'entry.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    prepare(parser.parse_args().out)
