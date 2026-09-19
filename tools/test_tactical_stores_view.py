"""Inventory readout uses reservations and never exposes enemy stores."""
import unittest
from copy import deepcopy
from tools.test_tactical_ballistics import BallisticsTests, GUN


class StoresViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BallisticsTests.setUpClass()

    def test_reservations_are_visible_without_mutating_inventory_or_showing_enemy(self):
        b = BallisticsTests().battle(75)
        inv = b.inventory.inventories[b._direct_index]
        loaded = next(w for w in inv._value['weapons'] if w['module_id'] == GUN)
        inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='discharge', target=GUN,
                    quantity=loaded['ready_rounds'], cooldown_steps=0)
        inv.command(epoch=inv.epoch, sequence=inv.sequence+1, kind='start_reload', target=GUN,
                    recipe_id='recipe.3a.75mm.ordinary')
        before = deepcopy(inv._value)
        view = b.view()['stores']
        self.assertEqual([s['ship_id'] for s in view], [b.session._direct])
        self.assertGreater(sum(m['reserved'] for m in view[0]['magazines']), 0)
        self.assertEqual(view[0]['used_volume_cm3'], inv.summary()['used_volume_cm3'])
        self.assertEqual(before, inv._value)
        self.assertEqual(view, b.view()['stores'])


if __name__ == '__main__':
    unittest.main()
