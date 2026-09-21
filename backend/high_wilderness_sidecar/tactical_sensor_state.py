"""Shared entry/live equipment availability; observation never invents power or crew."""


class SensorState:
    def _availability(self, world):
        key = tuple((s.devices.revision, s.resources.revision) for s in world.ships)
        if key == self._availability_key:
            return key, self._available
        result = []
        for index, ship in enumerate(world.ships):
            available = {}
            powered, allocations = set(ship.resources.power.powered_instance_ids), dict(ship.resources.allocations)
            def check(k):
                if k in available:
                    return available[k]
                m, n = self._modules[index][k], self._indices[index][k]
                functions = {'weapon': ('weapon.aim', 'weapon.fire'), 'sensor': ('sensor.search',),
                             'fire_control': ('fire_control.solution',), 'damage_control': ('damage_control.firefighting',)}.get(m.prototype.category, ())
                automatic = m.prototype.automation.level == 'full' or bool(functions) and all(
                    f in m.prototype.automation.automated_functions for f in functions)
                if ship.devices.modules[n].durability_points <= 1e-8:
                    reason = 'destroyed'
                elif ship.resources.modes[n] != 'active':
                    reason = 'mode_disabled'
                elif m.host_instance_id and check(m.host_instance_id):
                    reason = 'host_unavailable'
                elif m.prototype.power.active_load_kw > 0 and k not in powered:
                    reason = 'power_unavailable'
                elif not automatic and (any(dict(allocations.get(k, ())).get(r.crew_type, 0) < r.minimum_operating for r in m.prototype.crew)
                    or functions and any(self.crew_efficiency(world,index,k,f)<=1e-8 for f in functions)):
                    reason = 'crew_unavailable'
                else:
                    reason = None
                available[k] = reason
                return reason
            for k in self._modules[index]:
                check(k)
            result.append(available)
        return key, tuple(result)

    def crew_efficiency(self,world,index,module_id,function):
        return self.session._resource_kernels[index].crew_efficiency(world.ships[index].resources,module_id,function)
