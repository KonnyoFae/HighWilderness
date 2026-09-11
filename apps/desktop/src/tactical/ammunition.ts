export const ammunitionName = (id: string | null | undefined) => {
  if (!id) return '未装弹';
  if (id === 'recipe.h5d.incendiary' || id === 'projectile.h5d.incendiary') return '燃烧弹';
  if (id === 'recipe.x1a.special_armor_piercing' || id === 'projectile.s1.armor_piercing') return '穿甲弹';
  if (id === 'recipe.x1a.ordinary' || id === 'recipe.p2a.ordinary' || id === 'projectile.p2a.ordinary') return '普通弹';
  return id;
};
