"""Wall-time rate control only; never changes a simulation step or game input."""
from dataclasses import dataclass
from math import isfinite

RATE_SCALE = 1000


@dataclass(frozen=True)
class SpeedPolicy:
    mode: str = 'fixed'
    fixed_rate: float = 1.
    minimum_rate: float = .05
    utilization: float = .70
    window_ns: int = 200_000_000
    step_alpha: float = .15
    overhead_alpha: float = .30
    recovery_delay_ns: int = 1_500_000_000
    recovery_per_second: float = .06
    pressure_steps: float = 4.

    def __post_init__(self):
        if self.mode not in ('fixed','adaptive') or any(type(v) not in (int,float) or not isfinite(v) for v in
            (self.fixed_rate,self.minimum_rate,self.utilization,self.step_alpha,self.overhead_alpha,self.recovery_per_second,self.pressure_steps)):
            raise ValueError('Invalid speed policy')
        if not (0 < self.minimum_rate <= self.fixed_rate <= 1 and .1 <= self.utilization < 1 and
                0 < self.step_alpha <= 1 and 0 < self.overhead_alpha <= 1 and 0 < self.recovery_per_second <= 1 and self.pressure_steps > 0):
            raise ValueError('Invalid speed policy bounds')
        if any(type(v) is not int or v <= 0 for v in (self.window_ns,self.recovery_delay_ns)) or self.minimum_rate*RATE_SCALE < 1:
            raise ValueError('Invalid speed policy timing')


@dataclass(frozen=True)
class SpeedStatus:
    mode: str
    effective: float
    slowed: bool
    revision: int
    active_wall_s: float
    advanced_simulation_s: float
    actual_ratio: float
    step_mean_ms: float
    nonstep_utilization: float
    longest_step_ms: float


class SpeedController:
    def __init__(self, policy):
        if type(policy) is not SpeedPolicy:raise ValueError('Expected speed policy')
        self.policy=policy;self.rate=round(policy.fixed_rate*RATE_SCALE);self.revision=0
        self.wall_ns=0;self.steps=0;self.step_work_ns=0;self.longest_ns=0;self.step_mean_ns=0.
        self.overhead=0.;self.window_wall=0;self.window_work=0;self.last_drop=0
        # At most RATE_SCALE keys. Diagnostics can inspect exact active wall
        # time spent at each rate without keeping an unbounded sample history.
        self.rate_wall_ns={}

    def elapse(self, ns):
        self.wall_ns+=ns;self.rate_wall_ns[self.rate]=self.rate_wall_ns.get(self.rate,0)+ns

    def resume(self):
        # Retain the learned capacity and current rate, exclude paused work/time.
        self.window_wall=self.wall_ns;self.window_work=0;self.last_drop=self.wall_ns

    def record_step(self, ns, *, active):
        self.step_work_ns+=ns;self.longest_ns=max(self.longest_ns,ns)
        if not active:return
        self.steps+=1
        self.step_mean_ns=ns if not self.step_mean_ns else self.step_mean_ns+self.policy.step_alpha*(ns-self.step_mean_ns)

    def tune(self, debt_steps):
        p=self.policy
        if p.mode!='adaptive' or not self.step_mean_ns:return
        elapsed=self.wall_ns-self.window_wall
        ready=elapsed>=p.window_ns
        pressure=debt_steps>=p.pressure_steps
        if not ready and (not pressure or self.wall_ns-self.last_drop<50_000_000):return
        if ready:
            measured=min(1.,self.window_work/max(1,elapsed))
            self.overhead+=p.overhead_alpha*(measured-self.overhead)
            self.window_wall=self.wall_ns;self.window_work=0
        capacity=max(.02,p.utilization-self.overhead)*1e9/(60*self.step_mean_ns)
        # Leave room to repay already accrued debt. Never remove debt or change
        # the rate retroactively for work/time which has already elapsed.
        if pressure:capacity=min(capacity,self.rate/RATE_SCALE*.7)
        target=max(round(p.minimum_rate*RATE_SCALE),min(RATE_SCALE,int(capacity*RATE_SCALE)))
        rate=self.rate
        if target < rate*.92:
            rate=target;self.last_drop=self.wall_ns
        elif ready and target>rate and debt_steps<1.5 and self.wall_ns-self.last_drop>=p.recovery_delay_ns:
            rate=min(target,rate+max(1,round(p.recovery_per_second*elapsed/1e9*RATE_SCALE)))
        if rate!=self.rate:
            self.rate=rate;self.revision+=1

    def status(self):
        return SpeedStatus(self.policy.mode,self.rate/RATE_SCALE,self.policy.mode=='adaptive' and self.rate<RATE_SCALE,
            self.revision,self.wall_ns/1e9,self.steps/60,self.steps*1e9/(60*self.wall_ns) if self.wall_ns else 0.,
            self.step_mean_ns/1e6,self.overhead,self.longest_ns/1e6)
