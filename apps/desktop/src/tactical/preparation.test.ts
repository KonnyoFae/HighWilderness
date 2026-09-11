import { describe, expect, it } from 'vitest';
import { changeDraft, quantity } from './preparation';
import type { PreparationDraft } from './preparation';
describe('preparation local edits',()=>{
  it('rejects fractional, missing, negative and unsafe resource quantities',()=>{
    for(const value of ['', '-1', '1.1', 'NaN', '9007199254740992'])expect(()=>quantity(value)).toThrow();
    expect(quantity('0')).toBe(0);expect(quantity('200')).toBe(200);
  });
  it('edits one ship without dropping the other ship or mutating saved state',()=>{
    const original={preparation_id:'prep.a',revision:3,supply_id:'supply.a',ships:[
      {instance_id:'ship.a',revision:0,magazines:[{module_id:'mag',quantity:10}],cargo:[],weapons:[]},
      {instance_id:'ship.b',revision:1,magazines:[],cargo:[],weapons:[]}]} satisfies PreparationDraft;
    const next=changeDraft(original,'ship.a',s=>{s.magazines[0].quantity=20;});
    expect(original.ships[0].magazines[0].quantity).toBe(10);expect(next.revision).toBe(4);
    expect(next.ships[1]).toEqual(original.ships[1]);
    expect(()=>changeDraft(original,'missing',()=>{})).toThrow();
  });
});
