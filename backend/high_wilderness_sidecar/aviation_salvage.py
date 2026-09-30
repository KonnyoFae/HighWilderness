"""AV5 independent aviation salvage boundary. Entries own actual identities."""
from . import persistent_ship as ps,aviation_manifest as manifest,aviation_logistics as logistics

INTERFACE='gaotian.aviation-salvage/av5-v1'


def origin(world,position,layer,reason):
    return dict(fixed_step=world.fixed_step,position_m=list(position),height_layer=layer,reason=reason)


def validate_origin(value):
    if value is None:return  # Historical records have no trustworthy position.
    ps.obj(value,'fixed_step position_m height_layer reason','$.salvage.origin')
    ps.integer(value['fixed_step'],'$.fixed_step')
    ps.need(value['height_layer'] in ('upper','cloud','rain'),'$.height_layer','未知打捞层位')
    ps.need(value['reason'] in ('aircraft_destroyed','no_capacity','ship_loss','facility_loss','battle_end'),'$.reason','未知打捞原因')
    ps.need(type(value['position_m']) is list and len(value['position_m'])==2,'$.position_m','需要打捞坐标')
    for x in value['position_m']:ps.number(x,'$.position_m',-ps.MAX_INT)


def stamp(work,where,ids):
    if where is None:return
    for row in [a for a in work.m['aircraft'] if a['location']=='salvage']+[p for p in work.m['personnel'] if p['housing']=='salvage']:
        if row['id'] not in ids:continue
        work.s.setdefault('salvage_origins',{}).setdefault(row['id'],ps.clone(where))


def stamp_new(b,world,inventories):
    for old,inv,ship in zip(b.inventory.inventories,inventories,world.ships):
        if 'aviation' not in inv._value:continue
        previous=old._value['aviation']['manifest'];current=inv._value['aviation']['manifest']
        salved=lambda m:{r['id'] for r in (*m['aircraft'],*m['personnel']) if r.get('location')=='salvage' or r.get('housing')=='salvage'}
        ids=salved(current)-salved(previous)
        if ids:
            work=logistics.Work(inv)
            stamp(work,origin(world,ship.motion.position_world_m.to_list(),ship.motion.height_layer,'ship_loss' if ship.wreck else 'facility_loss'),ids)
            work.commit()


def extract(inv,source,side,fallback,previous):
    """Move recoverable rows out of a private ending inventory; dead rows stay."""
    if 'aviation' not in inv._value:return []
    work=logistics.Work(inv);entries=[]
    historical={r['id'] for field in ('aircraft','personnel') for r in previous.get('aviation',{}).get('manifest',{}).get(field,[])
        if r.get('location')=='salvage' or r.get('housing')=='salvage'}
    for field,kind,location in (('aircraft','aircraft','location'),('personnel','pilot','housing')):
        for row in list(work.m[field]):
            if row[location]!='salvage':continue
            where=work.s.get('salvage_origins',{}).get(row['id'],None if row['id'] in historical else fallback)
            entries.append(dict(id=row['id'],kind=kind,source_instance_id=source,side_id=side,origin=ps.clone(where),asset=ps.clone(row)))
            work.m[field].remove(row)
            for key in ('departure_tasks','departure_emissions','salvage_origins'):work.s.get(key,{}).pop(row['id'],None)
    work.commit();return entries


def make_pool(result,entries):
    sources=[dict(instance_id=r['after']['state']['instance_id'],side_id=r['side_id'],catalog=ps.clone(r['after']['resources']['aviation']['catalog']))
        for r in result['ships'] if any(e['source_instance_id']==r['after']['state']['instance_id'] for e in entries)]
    return dict(interface=INTERFACE,pool_id='salvage.'+result['scene_id'],scene_id=result['scene_id'],settlement_id=result['settlement_id'],
        player_side_id=result['player_side_id'],coordinate_space='tactical_scene',revision=0,sources=sources,entries=entries)


def validate(pool):
    from . import aviation_catalog
    ps.obj(pool,'interface pool_id scene_id settlement_id player_side_id coordinate_space revision sources entries','$.salvage')
    ps.need(pool['interface']==INTERFACE and pool['coordinate_space']=='tactical_scene','$.salvage','未知航空打捞版本或坐标空间')
    for key in ('pool_id','scene_id','settlement_id','player_side_id'):ps.identifier(pool[key],'$.'+key)
    ps.integer(pool['revision'],'$.revision')
    ps.need(pool['pool_id']=='salvage.'+pool['scene_id'] and pool['settlement_id']=='settlement.'+pool['scene_id'],'$.salvage','池与战局身份不一致')
    sources=ps.rows(pool['sources'],'instance_id','$.sources')
    for s in sources.values():
        ps.obj(s,'instance_id side_id catalog','$.source');ps.identifier(s['side_id'],'$.side_id');aviation_catalog.validate(s['catalog'])
    for e in ps.rows(pool['entries'],'id','$.entries').values():
        ps.obj(e,'id kind source_instance_id side_id origin asset','$.entry');validate_origin(e['origin'])
        ps.identifier(e['source_instance_id'],'$.source_instance_id')
        s=sources.get(e['source_instance_id']);a=e['asset']
        ps.need(s is not None and e['side_id']==s['side_id'] and e['kind'] in ('aircraft','pilot') and type(a) is dict and a.get('id')==e['id'],'$.entry','打捞来源或身份无效')
        m=manifest.empty(s['catalog']);m['aircraft' if e['kind']=='aircraft' else 'personnel']=[a]
        manifest.validate(m,s['catalog'])
        ps.need(a.get('location' if e['kind']=='aircraft' else 'housing')=='salvage','$.entry','只有可打捞资产才能入池')
    return pool


def validate_result(result):
    pool=validate(result['aviation_salvage']);ids=set()
    ps.need(pool['revision']==0 and pool['scene_id']==result['scene_id'] and pool['player_side_id']==result['player_side_id'],'$.salvage','初始池与战果不一致')
    for source in pool['sources']:
        row=next((r for r in result['ships'] if r['after']['state']['instance_id']==source['instance_id']),None)
        ps.need(row is not None and source['side_id']==row['side_id'] and source['catalog']==row['after']['resources'].get('aviation',{}).get('catalog'),'$.source','打捞目录或阵营不匹配')
    for row in result['ships']:
        m=row['after']['state'].get('aviation',{}).get('manifest',{})
        for a in m.get('aircraft',[]):
            for v in (a,*a['crew']):
                ps.need(v['id'] not in ids,'$.salvage','跨舰航空身份重复');ids.add(v['id'])
        for p in m.get('personnel',[]):
            ps.need(p['id'] not in ids,'$.salvage','跨舰人员身份重复');ids.add(p['id'])
    for e in pool['entries']:
        ps.need(e['id'] not in ids,'$.salvage','资产不能同时在舰和池中');ids.add(e['id'])
        if e['origin']:ps.need(e['origin']['fixed_step']<=result['fixed_step'],'$.origin','打捞时间晚于战果')


def receive(inv,pool,ids):
    from .aviation_recovery import receive_people
    work=logistics.Work(inv);sources={s['instance_id']:s for s in pool['sources']}
    own={x['id'] for x in work.m['personnel']}|{x['id'] for a in work.m['aircraft'] for x in (a,*a['crew'])}
    for e in pool['entries']:
        if e['id'] not in ids:continue
        ps.need(e['id'] not in own,'$.entry_ids','接收舰已有相同身份，拒绝复制')
        a=ps.clone(e['asset'])
        if e['kind']=='pilot':receive_people(work,[{k:a[k] for k in ('id','health','modifiers')}])
        else:
            from 高天荒野舰艇数据契约 import canonical_sha256
            ps.need(work.m['catalog_sha256']==canonical_sha256(sources[e['source_instance_id']]['catalog']),'$.catalog','飞机目录不兼容，需要显式迁移')
            work.unload(a);a.update(location='cargo',ship_id=work.ship,home_ship_id=work.ship,module_id=None)
            work.m['aircraft'].append(a)
    work.capacity_check();work.commit()
