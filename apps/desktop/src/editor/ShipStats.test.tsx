import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ShipStats } from "./ShipStats";
import type { ModuleOption, SessionSnapshot } from "./model";

const option={prototype:{id:"device",version:1,power:{generation_kw:0,active_load_kw:80}}} as unknown as ModuleOption;
const session={resource:{kind:"OutfitPlan"},draft:{modules:[{id:"one",prototype:{id:"device",version:1}},{id:"two",prototype:{id:"device",version:1}}]},
  preview:{valid:false,diagnostics:[{code:"outfit.insufficient_lift",message:"升力不足",path:"$.modules",severity:"error"}],model:{}},
  last_valid_preview:{model:{derived:{generation_kw:1000,design_mass_kg:500,lift:{lift_margin_n:100}}}},last_valid_revision:2,revision:3,dirty:true} as unknown as SessionSnapshot;
describe("current ship feedback",()=>{
  it("shows current power deficit even when only a previous valid mass preview exists",()=>{
    const html=renderToStaticMarkup(<ShipStats session={session} options={[option]}/>);
    expect(html).toContain("缺 160 kW");
    expect(html).toContain("最近合法结果（修订 2）");
    expect(html).toContain("升力不足");
    expect(html).not.toContain("1,000 kW");
  });
  it("does not present an unknown module as a zero-consumption device",()=>{
    const html=renderToStaticMarkup(<ShipStats session={session} options={[]}/>);
    expect(html).toContain("模块信息不全");
    expect(html).not.toContain("缺 160 kW");
  });
  it("uses the last valid lift reading with its revision when a draft is invalid", () => {
    const draft = structuredClone(session);
    Object.assign(draft.last_valid_preview!.model.derived!, { lift_reserve: {
      dry_mass_kg: 500, available_force_n: 5883.99, margin_n: 980.665, reserve_fraction: .2,
    } });
    const html = renderToStaticMarkup(<ShipStats session={draft} options={[option]} />);
    expect(html).toContain('+20.0%');
    expect(html).toContain('最近合法结果（修订 2）');
  });
});
