"""Exact angular sectors and real scene launch rejection, without baseline rewrites."""
from copy import deepcopy
from dataclasses import replace
from math import cos, sin, radians
from types import SimpleNamespace as NS
import unittest

from 高天荒野舰艇水平射界 import blocked_regions, horizontal_fire_arc, ray_hits_polygon
from 高天荒野舰艇数据契约 import ContractError, HullBlueprintInput, canonical_sha256, load_material_registry, load_hull_coating_catalog
from 高天荒野舰艇无界面船壳编译器 import compile_hull
from 高天荒野舰艇无界面舾装编译器 import compile_outfit, build_derived_ship_snapshot
from 高天荒野舰艇阶段F三舰集成测试 import build_chain, STRUCTURE_CATALOG, ARMOR_CATALOG, COATING_CATALOG
from 高天荒野舰艇阶段I统一战术场景时间线测试 import scene_fixture, TIMING_CATALOG, PROJECTILE_CATALOG
from 高天荒野舰艇武器时间与射击队列 import load_weapon_timing_profile_catalog
from 高天荒野舰艇战术弹丸世界 import load_projectile_profile_catalog
from 高天荒野舰艇统一战术场景 import advance_tactical_scene_step
from 高天荒野舰艇编辑器领域层 import OutfitEditorDocument


def hull_with(polygons, level=1):
    return NS(normalized_blueprint=NS(decks=[NS(id="upper", level=level,
        regions=[NS(id=f"r{i}", vertices_m=tuple(p)) for i,p in enumerate(polygons)])]))


def occluded_chain():
    chain = build_chain("conventional_crewed")
    source = chain.hull.normalized_blueprint.to_dict()
    upper = deepcopy(source["decks"][1])
    upper.update(id="deck.2", level=2)
    for i, region in enumerate(upper["regions"]):
        x0, x1 = (-7.5,-2.5) if i == 0 else (2.5,7.5)
        region["id"] = f"deck.2.region.{i}"
        region["vertices_m"] = [[x0,12.5],[x1,12.5],[x1,17.5],[x0,17.5]]
    source["decks"].append(upper)
    registry = load_material_registry((STRUCTURE_CATALOG,ARMOR_CATALOG))
    hull = compile_hull(HullBlueprintInput.parse(source),registry)
    outfit = compile_outfit(chain.outfit.normalized_plan,hull,chain.module_catalog,load_hull_coating_catalog(COATING_CATALOG))
    return replace(chain,hull=hull,outfit=outfit,snapshot=build_derived_ship_snapshot(hull,outfit))


class HorizontalArcTests(unittest.TestCase):
    def test_forward_wrap_sector_and_clear_aft(self):
        hull = hull_with([[(-1,2),(1,2),(1,4),(-1,4)]])
        arc = horizontal_fire_arc(hull,(0,0),0)
        self.assertEqual(len(arc["blocked_intervals_deg"]),2)
        self.assertAlmostEqual(arc["blocked_intervals_deg"][0][1],26.5650511771)
        self.assertTrue(blocked_regions(hull,(0,0),0,(0,1)))
        self.assertFalse(blocked_regions(hull,(0,0),0,(0,-1)))
        self.assertFalse(blocked_regions(hull,(0,0),1,(0,1)))

    def test_tangent_collinear_and_origin_inside_block(self):
        polygon = ((-1,2),(1,2),(1,4),(-1,4))
        self.assertTrue(ray_hits_polygon((0,0),(1,2),polygon))
        self.assertTrue(ray_hits_polygon((1,0),(0,1),polygon))
        self.assertFalse(ray_hits_polygon((1,0),(0,-1),polygon))
        self.assertEqual(horizontal_fire_arc(hull_with([polygon]),(0,3),0)["blocked_intervals_deg"],[[0.0,360.0]])

    def test_narrow_and_concave_polygons_match_direction_queries(self):
        hull = hull_with([[(1,2000),(1.01,2000),(1.01,2001),(1,2001)],
            [(-4,2),(-2,2),(-2,4),(-3,3),(-4,4)]])
        arc = horizontal_fire_arc(hull,(0,0),0)
        self.assertTrue(any(b-a < .001 for a,b in arc["blocked_intervals_deg"]))
        for angle in [i+.123 for i in range(360)]:
            direction = sin(radians(angle)),cos(radians(angle))
            self.assertEqual(bool(blocked_regions(hull,(0,0),0,direction)),
                any(a <= angle <= b for a,b in arc["blocked_intervals_deg"]))

    def test_union_and_order_are_deterministic(self):
        polygons = [[(-1,2),(1,2),(1,4),(-1,4)],[(0,2),(3,2),(3,3),(0,3)]]
        self.assertEqual(horizontal_fire_arc(hull_with(polygons),(0,0),0),
                         horizontal_fire_arc(hull_with(list(reversed(polygons))),(0,0),0))
        self.assertEqual(horizontal_fire_arc(hull_with(polygons,level=0),(0,0),0)["blocked_intervals_deg"],[])

    def test_invalid_direction_is_rejected_even_without_obstacles(self):
        for direction in [(0,0),(float("nan"),1),(float("inf"),1),(True,1),()]:
            with self.assertRaises(ContractError):
                blocked_regions(hull_with([]),(0,0),0,direction)

    def test_real_scene_blocked_launch_does_not_commit_ammo_time_or_projectile(self):
        chain = occluded_chain()
        document = OutfitEditorDocument(chain.outfit.normalized_plan.to_dict(), chain.hull,
            chain.module_catalog, load_hull_coating_catalog(COATING_CATALOG))
        preview = document.weapon_control_preview(document.layout_preview())
        self.assertEqual(preview["interface"], "gaotian.weapon-control-view/v2alpha1")
        arc = preview["arcs"][0]
        self.assertTrue(any(a == 0 for a,b in arc["blocked_intervals_deg"]))
        self.assertTrue(any(a < 180 < b for a,b in arc["intervals_deg"]))
        timing = load_weapon_timing_profile_catalog(TIMING_CATALOG)
        projectiles = load_projectile_profile_catalog(PROJECTILE_CATALOG)
        registry = load_material_registry((STRUCTURE_CATALOG,ARMOR_CATALOG))
        bindings,state,directive = scene_fixture(chain,timing,projectiles)
        for heading in [0.0, 1.2]:
            oriented = replace(state, ships=tuple(replace(ship, motion_state=replace(ship.motion_state, heading_rad=heading))
                if ship.ship_id == directive.source_ship_id else ship for ship in state.ships))
            before = canonical_sha256(oriented)
            with self.assertRaisesRegex(ContractError,"weapon_arc.hull_blocked"):
                advance_tactical_scene_step(oriented,bindings,timing,projectiles,registry,launch_directives=(directive,))
            self.assertEqual(canonical_sha256(oriented),before)
        clear = advance_tactical_scene_step(state,bindings,timing,projectiles,registry,
            launch_directives=(replace(directive,launch_direction_local_xy=(0.0,-1.0)),))
        self.assertEqual(len(clear.spawned_projectiles),1)
        self.assertEqual(clear.resulting_scene.fixed_step_index,1)


if __name__ == "__main__":
    unittest.main()
