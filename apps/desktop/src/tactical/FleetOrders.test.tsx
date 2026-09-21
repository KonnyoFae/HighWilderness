import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {FleetOrders} from './FleetOrders';
import type {TacticalView} from './model';
const render=(shipId='escort',kind='formation',departed=false)=>renderToStaticMarkup(<FleetOrders
  view={{snapshot:{navigation:{command_sequence:0,last:null,ships:[{ship_id:'escort',kind,status:'following',points:[]}],withdrawals:[],
    disengagement:{distance_m:30000,threshold_m:50000,waiting_ship_ids:['escort'],departed_ship_ids:departed?['escort']:[]}}}} as unknown as TacticalView}
  shipId={shipId} directId="flag" disabled={false} picking={false} onPick={()=>{}} onCommand={()=>{}}
  speed={100} onSpeed={()=>{}} heading={null} onHeading={()=>{}}/>);
describe('正式距离撤离界面',()=>{
  it('显示有效阈值与等待队员，仅随伴舰能脱队',()=>{
    expect(render()).toContain('脱离阈值 50 公里');expect(render()).toContain('等待 1 艘舰完成换层');
    expect(render()).toContain('单舰脱队撤离');expect(render('flag')).not.toContain('单舰脱队撤离');
  });
  it('撤离途中可取消，离场后按钮锁定',()=>{
    expect(render('escort','individual_withdrawal')).toContain('取消单舰撤离');
    const ended=render('escort','individual_withdrawal',true);
    expect(ended).toContain('已离场');expect(ended).toMatch(/disabled=""[^>]*>取消单舰撤离/);
  });
});
