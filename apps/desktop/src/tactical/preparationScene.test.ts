import { describe, it, expect } from 'vitest';
import { addToScene, moduleTab } from './preparationScene';
import type { PreparationScene } from './preparationScene';
describe('preparation scene identity',()=>{
  it('keeps an import on its captured side and treats retries as the same ship',()=>{
    const base:PreparationScene={interface:'test',revision:0,distance_m:50000,preparation_id:null,
      sides:[{id:'enemy',flagship_instance_id:null,ships:[]},{id:'player',flagship_instance_id:null,ships:[]}]};
    const added=addToScene(base,'enemy','same');
    expect(added.sides[0].flagship_instance_id).toBe('same');expect(added.sides[1].ships).toEqual([]);
    expect(addToScene(added,'player','same')).toBe(added);expect(base.revision).toBe(0);
    expect(added.distance_m).toBe(50000);
  });
  it('routes actual categories to the matching operation page',()=>{
    expect(moduleTab('weapon')).toBe('guns');expect(moduleTab('lift_fuel_tank')).toBe('cargo');
    expect(moduleTab('damage_control')).toBe('damage');expect(moduleTab('fire_control')).toBe('devices');
  });
});
