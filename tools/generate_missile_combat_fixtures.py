"""5e selectable outfits with real launcher and observation equipment."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]


def build():
    folder=ROOT/'舰艇数据/舾装方案夹具'
    for key,name,launcher,sensor in (
        ('rocket','火箭雷达导弹交战测试舰','gtw.module.launcher.5c.small.rocket.active_radar','radar'),
        ('turbojet','涡喷红外导弹交战测试舰','gtw.module.launcher.5c.small.turbojet.infrared','infrared'),
        ('vls','VLS导弹交战测试舰','gtw.module.launcher.5c.vls','radar')):
        outfit=json.loads((folder/'炮塔式导弹后勤测试舰.v1.json').read_text(encoding='utf-8'))
        outfit.update(id='gtw.outfit.5e.'+key,version=1,name=name)
        for m in outfit['modules']:
            if m['id']=='weapon_upper_port':
                m['prototype']=dict(id=launcher,version=1)
                if key=='vls':m['placement']['deck_id']='deck.0'
            if m['id']=='sensor_upper_starboard':m['prototype']=dict(id='gtw.module.sensor.5d.'+sensor,version=1)
            if m['id']=='fire_control':m['prototype']=dict(id='gtw.module.command_computer.5d',version=1)
        outfit['modules'].append(dict(id='datalink.0',prototype=dict(id='gtw.module.datalink.5d',version=1),
            placement=dict(kind='hosted',host_instance_id='fire_control')))
        (folder/f'{name}.v1.json').write_text(json.dumps(outfit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':build()
