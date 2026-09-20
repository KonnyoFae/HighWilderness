"""Pre-F inverse and cache-free control, used only by tests/profiling tools."""
from backend.high_wilderness_sidecar import tactical_ballistics as ballistics
from backend.high_wilderness_sidecar import tactical_targeting as targeting


def time_to_distance(profile, speed, distance):
    elapsed = 0.; limit = profile.lifetime_steps / 60
    while elapsed < limit and distance > 1e-6:
        dt = min(.1, limit-elapsed)
        k = ballistics.step_coefficient(profile, speed, dt)
        dx, after = ballistics.scalar(speed, dt, k)
        if dx >= distance:
            lo, hi = 0., dt
            for _ in range(18):
                mid = (lo+hi)/2
                if ballistics.scalar(speed, mid, k)[0] < distance: lo = mid
                else: hi = mid
            return elapsed+(lo+hi)/2
        elapsed += dt; distance -= dx; speed = after
    return elapsed if distance <= 1e-6 else None


class UncachedSolutions(targeting.StepSolutions):
    def solve(self, *args):
        self.requests += 1
        return targeting.solution(*args)

    def metrics(self):
        return dict(step=self.step, requests=self.requests, solves=self.requests,
                    cache_hits=0, negative_cache_hits=0, solution_age_steps=0)
