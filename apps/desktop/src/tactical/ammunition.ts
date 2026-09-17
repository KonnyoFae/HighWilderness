export const ammunitionName = (id: string | null | undefined) => {
  if(id?.startsWith('recipe.ew.'))return id.includes('.chaff.')?'箔条弹':id.includes('.thermal.')?'热能烟雾弹':'主动诱饵';
  if (!id) return '未装弹';
  const missile=/^gtw\.missile\.5c\.(small|medium)\.(rocket|turbojet)\.(active_radar|infrared)\.(blast|incendiary)$/.exec(id);
  if(missile)return `${missile[1]==='small'?'小型':'中型'}${missile[2]==='rocket'?'火箭':'涡喷'}${missile[3]==='infrared'?'红外':'雷达'}导弹（${missile[4]==='blast'?'高爆':'燃烧'}）`;
  const caliber = /^(?:recipe|projectile)\.3a\.(30|50|75|120)mm\.(ordinary|armor_piercing|incendiary)$/.exec(id);
  if (caliber) return `${caliber[1]} 毫米${({ordinary:'普通弹',armor_piercing:'穿甲弹',incendiary:'燃烧弹'} as Record<string,string>)[caliber[2]]}`;
  if (id === 'recipe.h5d.incendiary' || id === 'projectile.h5d.incendiary') return '燃烧弹';
  if (id === 'recipe.x1a.special_armor_piercing' || id === 'projectile.s1.armor_piercing') return '穿甲弹';
  if (id === 'recipe.x1a.ordinary' || id === 'recipe.p2a.ordinary' || id === 'projectile.p2a.ordinary') return '普通弹';
  return id;
};
