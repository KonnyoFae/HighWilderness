# Frozen aa301d3 observation capabilities; P1a does not modify this file.
class _Inputs:
    """Scratch inputs for one plan/view call; never retained by the runtime.

    Even two plans at the same fixed step get fresh inputs. Target range and
    visibility are still checked individually against that call's world.
    """
    def __init__(self,runtime,world,available):
        self.runtime=runtime;self.world=world;self.available=available
        self.positions=tuple(tuple(s.motion.position_world_m.to_list()) for s in world.ships)
        self.controls={}

    def controllers(self,n,position):
        if n not in self.controls:
            b=self.runtime.battle
            self.controls[n]=tuple((mid,b._capabilities[n][mid]['maximum_lock_range_m'],
                int(b._capabilities[n][mid]['simultaneous_channels']*self.runtime.effect(self.world,n,mid,'fire_control.solution')))
                for mid in b._controllers[n] if not self.available[n][mid])
        distance=None if position is None else hypot(*(a-c for a,c in zip(position,self.positions[n])))
        controls=[];channels=0
        for mid,maximum,count in self.controls[n]:
            if distance is not None and distance>maximum:continue
            controls.append(mid);channels+=count
        return tuple(controls),channels


def controllers(self,n,world,available,position=None,*,inputs=None):
    if inputs is not None:return inputs.controllers(n,position)
    b=self.battle;controls=[];channels=0
    for mid in b._controllers[n]:
        if available[n][mid]:continue
        cap=b._capabilities[n][mid];eff=self.effect(world,n,mid,'fire_control.solution')
        if position is not None and hypot(*(a-c for a,c in zip(position,world.ships[n].motion.position_world_m.to_list())))>cap['maximum_lock_range_m']:continue
        controls.append(mid);channels+=int(cap['simultaneous_channels']*eff)
    return tuple(controls),channels

