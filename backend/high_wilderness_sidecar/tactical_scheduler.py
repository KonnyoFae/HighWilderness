"""E3a single-owner 60 Hz scheduler and bounded, queryable input ledger.

Internal experiment API: no transport, background thread or UI. The host calls
pump frequently, services reads/acks between pumps and owns the session solely
through this wrapper. Time debt uses integer nanoseconds times 60, without dt
rounding or dropping steps. Explicit pause excludes subsequent wall-clock time.
"""
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from threading import get_ident
from time import monotonic_ns

from 高天荒野舰艇数据契约 import ContractError
from 高天荒野舰艇定向推进控制桥 import DirectionalPropulsionControlInput, directional_control
from .simplified_flight import SimplifiedFlightSession

INTERFACE = 'gaotian.tactical-scheduled-control/e3a-v1alpha1'
QUANTA = 1_000_000_000


def require(ok, message):
    if not ok:
        raise ContractError('tactical_scheduler.boundary', '$', message)


def count(value, minimum=0):
    return type(value) is int and value >= minimum


@dataclass(frozen=True)
class SchedulerLimits:
    future_steps: int = 6
    pending_inputs: int = 64
    retained_receipts: int = 256
    reliable_events: int = 128
    steps_per_pump: int = 4
    pump_budget_ns: int = 8_000_000
    overload_steps: int = 15

    def __post_init__(self):
        require(all(count(v, 1) for v in (self.future_steps, self.pending_inputs, self.retained_receipts,
            self.reliable_events, self.steps_per_pump, self.pump_budget_ns, self.overload_steps)), 'Invalid scheduler limit')
        require(self.retained_receipts >= self.pending_inputs, 'Receipt capacity must cover accepted requests')


@dataclass(frozen=True)
class ScheduledControl:
    epoch: str
    generation: int
    sequence: int
    ship_id: str
    target_step: int
    control: DirectionalPropulsionControlInput

    @classmethod
    def parse(cls, value):
        if type(value) is cls:
            require(type(value.control) is DirectionalPropulsionControlInput, 'Invalid scheduled control value')
            value = value.to_dict()
        require(type(value) is dict and set(value) == {'interface', 'epoch', 'generation', 'sequence',
            'ship_id', 'target_step', 'control'}, 'Unknown or missing scheduled control field')
        require(value['interface'] == INTERFACE, 'Unsupported scheduled control interface')
        require(type(value['epoch']) is str and type(value['ship_id']) is str and
            count(value['generation']) and count(value['sequence'], 1) and count(value['target_step']), 'Invalid input identity')
        return cls(value['epoch'], value['generation'], value['sequence'], value['ship_id'], value['target_step'],
            DirectionalPropulsionControlInput.parse(value['control']))

    def to_dict(self):
        return dict(interface=INTERFACE, epoch=self.epoch, generation=self.generation, sequence=self.sequence,
            ship_id=self.ship_id, target_step=self.target_step, control=self.control.to_dict())


@dataclass(frozen=True)
class InputReceipt:
    sequence: int
    target_step: int
    status: str  # accepted, executed, cancelled, failed
    accepted_at_step: int
    resolved_at_step: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class InputQuery:
    epoch: str
    sequence: int
    status: str  # receipt.status, expired (never reexecute), not_received
    high_water_sequence: int
    receipt: InputReceipt | None


@dataclass(frozen=True)
class ReliableEvent:
    epoch: str
    sequence: int
    result: object  # immutable StepResult; never retains a world history chain


@dataclass(frozen=True)
class DomainBatch:
    device_operations: tuple = ()
    resource_operations: tuple = ()
    exit_operations: tuple = ()
    events: tuple = ()
    authority_events: tuple = ()


@dataclass(frozen=True)
class SchedulerStatus:
    epoch: str
    fixed_step: int
    running: bool
    generation: int
    pause_reason: str | None
    debt_quanta: int
    pending_inputs: int
    retained_receipts: int
    reliable_events: int
    highest_input_sequence: int
    acknowledged_event_sequence: int


class TacticalScheduler:
    def __init__(self, session, *, clock=monotonic_ns, limits=SchedulerLimits(), domain_inputs=None, project=None, stepper=None, stop_when=None):
        require(type(session) is SimplifiedFlightSession and type(limits) is SchedulerLimits, 'Invalid scheduler resource')
        require(session._owner == get_ident() and not session._executing, 'Session must belong to idle current owner')
        require(all(s.command is not None for s in session.world.ships), 'Scheduler requires command lifecycle domain')
        require(callable(clock) and (domain_inputs is None or callable(domain_inputs)) and
            (project is None or callable(project)), 'Invalid scheduler callback')
        self._session, self._committed = session, session.world
        self._owner, self._busy = get_ident(), False
        self._clock, self._limits = clock, limits
        self._clock_failed = False
        self._domains, self._project = domain_inputs, project
        require(stepper is None or callable(stepper), 'Invalid integrated stepper')
        self._stop_when = stop_when
        self._stepper = session.step if stepper is None else stepper
        self._last_ns = clock()
        require(count(self._last_ns), 'Clock must return nonnegative integer nanoseconds')
        self._running, self._reason, self._generation = False, 'initial', 0
        self._debt, self._highest, self._neutral_pending = 0, 0, True
        self._pending, self._records = {}, OrderedDict()
        self._events, self._event_sequence, self._event_ack, self._event_read = deque(), 0, 0, 0

    def _guard(self):
        require(get_ident() == self._owner and not self._busy, 'Single non-reentrant scheduler owner required')
        require(self._session.world is self._committed, 'Session was advanced outside scheduler')

    @property
    def limits(self):
        return self._limits

    @property
    def world(self):
        self._guard()
        return self._committed

    @property
    def status(self):
        self._guard()
        return SchedulerStatus(self._committed.epoch, self._committed.fixed_step, self._running,
            self._generation, self._reason, self._debt, len(self._pending), len(self._records), len(self._events),
            self._highest, self._event_ack)

    def _sample(self):
        now = self._clock()
        if not count(now) or now < self._last_ns:
            self._clock_failed = True
            self._pause('clock_error')
            require(False, 'Monotonic clock moved backward or returned invalid time')
        if self._running:
            self._debt += (now-self._last_ns)*60
        self._last_ns = now
        return now

    def _allowed(self):
        return any(s.ship_id == self._session._direct and s.authority_allowed and s.motion.hull_integrity_fraction > 0
            for s in self._committed.ships)

    def _resolve(self, sequence, status, reason=None):
        request, receipt = self._records[sequence]
        self._records[sequence] = (request, replace(receipt, status=status,
            resolved_at_step=self._committed.fixed_step, reason=reason))

    def _invalidate(self, reason):
        for sequence in sorted(self._pending.values()):
            self._resolve(sequence, 'cancelled', reason)
        self._pending.clear()
        self._generation += 1
        self._neutral_pending = True

    def _pause(self, reason):
        reason = 'clock_error' if self._clock_failed else reason
        self._running, self._reason = False, reason
        self._invalidate(reason)

    def pause(self, reason='manual'):
        self._guard()
        require(reason in ('manual', 'mode_exit', 'disconnected') or reason == 'battle_finished'
            and self._stop_when is not None and self._stop_when(), 'Invalid public pause reason')
        self._sample()
        if self._running or self._pending or reason != self._reason:
            self._pause(reason)
        return self.status

    def resume(self):
        self._guard()
        require(self._stop_when is None or not self._stop_when(), 'Battle has ended')
        require(not self._clock_failed, 'Clock fault requires a new scheduler')
        self._sample()
        require(self._debt <= self.limits.overload_steps*QUANTA, 'Recover paused debt before resume')
        require(len(self._events) < self.limits.reliable_events, 'Acknowledge reliable events before resume')
        self._running, self._reason = True, None
        return self.status

    def submit(self, value):
        self._guard()
        request = ScheduledControl.parse(value)
        require(request.epoch == self._committed.epoch, 'Foreign scene identity')
        if request.sequence in self._records:
            old, receipt = self._records[request.sequence]
            require(request == old, 'Conflicting input duplicate')
            return receipt
        require(request.sequence > self._highest, 'Input receipt expired; never reexecute')
        require(request.sequence == self._highest+1, 'Skipped input sequence')
        require(self._running and request.generation == self._generation, 'Paused or obsolete input generation')
        require(request.ship_id == self._session._direct and self._allowed(), 'Direct control denied')
        require(self._committed.fixed_step <= request.target_step <= self._committed.fixed_step+self.limits.future_steps,
            'Input outside future boundary window')
        require(request.target_step not in self._pending, 'Boundary already has an accepted control')
        require(len(self._pending) < self.limits.pending_inputs, 'Input queue full')
        require(len(self._records) < self.limits.retained_receipts, 'Acknowledge terminal input receipts before submitting')
        receipt = InputReceipt(request.sequence, request.target_step, 'accepted', self._committed.fixed_step)
        self._records[request.sequence] = (request, receipt)
        self._pending[request.target_step] = request.sequence
        self._highest = request.sequence
        return receipt

    def query(self, epoch, sequence):
        self._guard()
        require(epoch == self._committed.epoch and count(sequence, 1), 'Invalid input query')
        receipt = self._records[sequence][1] if sequence in self._records else None
        return InputQuery(epoch, sequence, receipt.status if receipt else 'expired' if sequence <= self._highest else 'not_received',
            self._highest, receipt)

    def acknowledge_input(self, epoch, sequence):
        self._guard()
        result = self.query(epoch, sequence)
        require(result.status not in ('accepted', 'not_received'), 'Only terminal input receipts can be acknowledged')
        self._records.pop(sequence, None)

    def read_events(self, epoch, *, after_sequence, limit=None):
        self._guard()
        limit = min(64, self.limits.reliable_events) if limit is None else limit
        require(epoch == self._committed.epoch and count(after_sequence) and
            self._event_ack <= after_sequence <= self._event_read and count(limit, 1) and
            limit <= self.limits.reliable_events, 'Invalid or expired reliable event cursor')
        result = tuple(e for e in self._events if e.sequence > after_sequence)[:limit]
        if result:
            self._event_read = max(self._event_read, result[-1].sequence)
        return result

    def acknowledge_events(self, epoch, through_sequence):
        self._guard()
        require(epoch == self._committed.epoch and count(through_sequence) and
            self._event_ack <= through_sequence <= self._event_read, 'Cannot acknowledge unread events')
        while self._events and self._events[0].sequence <= through_sequence:
            self._events.popleft()
        self._event_ack = through_sequence

    def _step(self):
        # Reserve one reliable envelope before touching the authority. Terminal
        # request storage is already reserved at acceptance, even if all cancel.
        if len(self._events) >= self.limits.reliable_events:
            self._pause('output_backpressure')
            return False
        n = self._committed.fixed_step
        sequence = self._pending.get(n)
        allowed = self._allowed()
        control = self._records[sequence][0].control if sequence is not None else \
            directional_control() if self._neutral_pending and allowed else None
        try:
            batch = DomainBatch() if self._domains is None else self._domains(self._committed)
            require(type(batch) is DomainBatch and all(type(v) is tuple for v in (batch.device_operations,
                batch.resource_operations, batch.exit_operations, batch.events, batch.authority_events)), 'Invalid internal domain batch')
            result = self._stepper(control, device_operations=batch.device_operations,
                resource_operations=batch.resource_operations, exit_operations=batch.exit_operations,
                events=batch.events, authority_events=batch.authority_events, project=self._project)
        except Exception:
            if sequence is not None:
                self._resolve(sequence, 'failed', 'step_failed')
                del self._pending[n]
            self._pause('step_failed')
            raise
        self._committed = self._session.world
        self._debt -= QUANTA
        self._neutral_pending = False
        if sequence is not None:
            self._resolve(sequence, 'executed')
            del self._pending[n]
        if result.events:
            self._event_sequence += 1
            self._events.append(ReliableEvent(self._committed.epoch, self._event_sequence, result))
        if allowed and not self._allowed():
            self._invalidate('authority_lost')
        if self._stop_when is not None and self._stop_when():
            self._pause('battle_finished')
        return True

    def pump(self):
        """Run bounded due steps; host must return to message/IO handling afterward."""
        return self._pump(recover=False)

    def recover_debt(self):
        """Explicit paused catch-up of retained debt, with cancelled/neutral controls.

        Does not accrue paused wall time or run steps beyond existing debt. A
        deeply stalled host may instead explicitly discard/recreate its scene.
        """
        return self._pump(recover=True)

    def _pump(self, *, recover):
        self._guard()
        require(not recover or not self._running, 'Debt recovery requires pause')
        require(not self._clock_failed, 'Clock fault requires a new scheduler')
        if self._stop_when is not None and self._stop_when():
            return 0
        start = self._sample()
        if not recover and not self._running:
            return 0
        self._busy = True
        completed = 0
        try:
            while completed < self.limits.steps_per_pump:
                now = self._sample()
                if not recover and self._debt > self.limits.overload_steps*QUANTA:
                    self._pause('overload')
                    break
                if self._debt < QUANTA or now-start >= self.limits.pump_budget_ns:
                    break
                if not self._step():
                    break
                completed += 1
                if self._stop_when is not None and self._stop_when():
                    break
                # Include compute time, even after the final permitted step.
                self._sample()
            if not recover and self._running and self._debt > self.limits.overload_steps*QUANTA:
                self._pause('overload')
            return completed
        finally:
            self._busy = False
