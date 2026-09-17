import type { TacticalStatic } from './model';
import type { PreparationShip, Supply } from './preparation';
export type FleetSide = 'enemy' | 'player';
export type PreparationTab = 'guns' | 'missiles' | 'devices' | 'damage' | 'cargo';
export type FormationShip = {instance_id:string;x_m:number;y_m:number;heading_rad:number};
export type PreparationScene = {interface:string;revision:number;distance_m:number;preparation_id:string|null;
  sides:{id:FleetSide;flagship_instance_id:string|null;ships:FormationShip[]}[]};
export type ScenePacket = {scene:PreparationScene;geometry:TacticalStatic;
  ships:{instance_id:string;revision:number;state:PreparationShip['state'];lift_reserve:PreparationShip['lift_reserve']}[];
  supply:Supply;supply_defaults:{ammunition_resources:number;goods_quantity:number;fuel_units:number};
  limits:{max_ships:number;minimum_distance_m:number;maximum_distance_m:number}};
export const sideName = (side:FleetSide) => side === 'player' ? '我方' : '敌方';
export const preparationTabs: {id:PreparationTab;name:string}[] = [
  {id:'guns',name:'火炮'},{id:'missiles',name:'导弹'},{id:'devices',name:'设备'},{id:'damage',name:'损管'},{id:'cargo',name:'货舱'}];
export function moduleTab(category:string):PreparationTab {
  if (/missile|launcher/.test(category)) return 'missiles';
  if (category==='weapon') return 'guns';
  if (category==='damage_control') return 'damage';
  if (/cargo|magazine|fuel_tank/.test(category)) return 'cargo';
  return 'devices';
}
export function changeScene(scene:PreparationScene, edit:(copy:PreparationScene)=>void) {
  const copy=structuredClone(scene);edit(copy);copy.revision++;return copy;
}
export function addToScene(scene:PreparationScene, side:FleetSide, id:string) {
  if(scene.sides.some(s=>s.ships.some(m=>m.instance_id===id)))return scene;
  return changeScene(scene,next=>{
    const fleet=next.sides.find(s=>s.id===side)!;
    fleet.ships.push({instance_id:id,x_m:fleet.ships.length*150,y_m:0,heading_rad:side==='enemy'?Math.PI:0});
    fleet.flagship_instance_id??=id;
  });
}
