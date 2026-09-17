import {describe, it, expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {missileSettlement, settlementShipLabel, type SettlementShip} from './settlement';
import {SettlementPanel} from './SettlementPanel';

const unit={serial:1,model_id:'gtw.missile.5c.small.rocket.active_radar',warhead_id:'blast',source:'raw' as const};
const state={instance_id:'instance.enemy.custom',revision:1,hull_integrity_fraction:1,service:{status:'available',reasons:[]},
  modules:[],magazines:[],weapons:[],cargo:[],missiles:{launchers:[{module_id:'vls',model_id:unit.model_id,warhead_id:'blast',auto_fire:false,
    ready:[unit],job:null,load_remaining:0,unload_remaining:0}],magazines:[]}};
const ship:SettlementShip={side_id:'side.enemy',ship_name:'自建敌方旗舰',before:{ship_id:'custom.enemy',state,armor:[]},
  after:{ship_id:'custom.enemy',state:{...state,revision:2,missiles:{launchers:[{...state.missiles.launchers[0],ready:[],
    job:{kind:'load_raw',unit:{...unit,serial:2},remaining_work_steps:60,total_work_steps:120}}],magazines:[]}},armor:[]},
  module_names:{vls:'垂发'},capacity_before:{capacity_cm3:100,used_volume_cm3:0,over_capacity:false},capacity_after:{capacity_cm3:100,used_volume_cm3:0,over_capacity:false},
  changes:[{resource:`missile:vls:ready:${unit.model_id}:blast`,reason:'missile_fired',delta:-1},
    {resource:`missile:vls:load_raw:${unit.model_id}:blast`,reason:'missile_logistics',delta:1}]};

describe('多舰战后连续性',()=>{
  it('使用保存的阵营标识，不把自建敌舰当作友舰；旧记录不猜测阵营',()=>{
    expect(settlementShipLabel(ship,'side.player')).toBe('敌方 · 自建敌方旗舰');
    expect(settlementShipLabel({...ship,side_id:'side.player'},'side.player')).toBe('我方 · 自建敌方旗舰');
    expect(settlementShipLabel({...ship,side_id:undefined,ship_name:undefined})).toBe('舰艇 · custom.enemy');
  });
  it('发射与库存转移分开统计，未完成作业保持一枚实际所有权',()=>{
    expect(missileSettlement(ship)).toEqual({before:{ready:1,stored:0,working:0},after:{ready:0,stored:0,working:1},fired:1});
    const html=renderToStaticMarkup(<SettlementPanel current={{saved:false,result:{settlement_id:'settlement.test',reason:'withdrawal',fixed_step:120,ships:[ship],player_side_id:'side.player'}}}
      library={null} busy={false} canDeploy={false} onSave={()=>{}} onInspect={()=>{}} onRefresh={()=>{}} onDeploy={()=>{}} onPrepare={()=>{}}/>);
    expect(html).toContain('敌方 · 自建敌方旗舰');expect(html).toContain('本场已发射 1 枚');
    expect(html).toContain('导弹未完成作业保留进度');expect(html).toContain('垂发转向中的弹');
    expect(html).toContain('disabled="">管理战后库存与下一场准备');
  });
});
