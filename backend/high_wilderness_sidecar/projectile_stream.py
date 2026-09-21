"""Disposable display publications. Cursors never acknowledge game actions."""
from collections import deque
import json

from .tactical_presentation import display_path, HISTORY_STEPS, PATH_STEPS

INTERFACE = 'gaotian.projectile-stream/d4-v1'
MAX_PUBLICATIONS = 32
MAX_HISTORY_BYTES = 2 * 1024 * 1024
MAX_DELTA_BYTES = 384 * 1024
MAX_TERMINALS = 4096
DEFINITION_KEYS = ('id', 'ship_id', 'kind', 'maximum_durability', 'born_step', 'origin_m', 'expires_step', 'missile_identity')
SAMPLE_KEYS = ('trajectory','shell_samples','missile_samples','missile_states')


def definition(flight):
    return {key: flight[key] for key in DEFINITION_KEYS if key in flight}


def terminal(flight):
    return {k:v for k,v in flight.items() if k not in SAMPLE_KEYS}


class ProjectileStream:
    def __init__(self):
        self.sequence = 0
        self.frames = deque()
        self.retained_bytes = 0
        self.step = None
        self.known = set()
        self.ended = set()
        self.flights = ()
        self.finished = ()
        self.dropped = 0
        self.terminals = deque()
        self.terminal_bytes = 0
        self.terminal_dropped = 0
        self.terminal_ids = set()
        self.closed = False

    def record(self, step, completed):
        if self.closed:return
        for p in completed:
            if p['id'] in self.terminal_ids:continue
            size = len(json.dumps(p,ensure_ascii=False).encode('utf-8'))
            self.terminals.append((p,size)); self.terminal_bytes += size
            self.terminal_ids.add(p['id'])
        while self.terminals and (self.terminals[0][0]['end_step']<step-HISTORY_STEPS
                or len(self.terminals)>MAX_TERMINALS or self.terminal_bytes>MAX_HISTORY_BYTES):
            p,size=self.terminals.popleft(); self.terminal_bytes-=size
            self.terminal_ids.discard(p['id'])
            if p['end_step']>=step-HISTORY_STEPS:self.terminal_dropped+=1

    def publish(self, view, history):
        step = view['fixed_step']
        # FlightHistory replaces each dictionary after commit; these references
        # remain an immutable snapshot even while later simulation steps run.
        # Seed a negotiated stream once; subsequent completed flights arrive
        # after each commit. Never resurrect budget-evicted events from a backup.
        if self.sequence==0:self.record(step,history.finished_flights())
        self.record(step, ())
        closing = bool(view.get('gunnery',{}).get('ending')) and not self.closed
        if closing:
            self.closed = True
            self.frames.clear(); self.terminals.clear(); self.terminal_ids.clear()
            self.retained_bytes = self.terminal_bytes = 0
            self.known.clear(); self.ended.clear()
        finished = tuple(p for p,_ in self.terminals)
        flights = () if self.closed else history.active_flights() + finished
        starts = [definition(p) for p in flights if p['id'] not in self.known]
        ends = [terminal(p) for p in finished if p['id'] not in self.ended]
        paths, shells, missiles = [], [], []
        for p in flights:
            if 'missile_samples' in p:
                fresh = p['id'] not in self.known or self.step is None
                samples = [v for v in p['missile_samples'] if fresh or v[0]>self.step]
                states = [v for v in p['missile_states'] if fresh or v['step']>self.step]
                if samples or states:
                    missiles.append([p['id'],samples,states])
                continue
            points = p.get('shell_samples', p.get('trajectory', ()))
            recent = points if self.step is None else [v for v in points if v[0] >= self.step]
            if recent and (p['id'] not in self.known or recent[-1][0] > self.step):
                if 'shell_samples' in p:
                    # D2 consumers already retain the previous endpoint and its
                    # incoming span mode. Do not transmit that anchor twice.
                    if p['id'] in self.known:
                        recent = [v for v in recent if v[0] > self.step]
                    shells.append([p['id'], recent])
                else:
                    paths.append([p['id'], display_path(recent)])
        self.sequence += 1
        frame = dict(sequence=self.sequence, step=step, starts=starts, paths=paths, shells=shells, missiles=missiles, ends=ends,
            gap=closing or self.step is not None and step-self.step > PATH_STEPS)
        size = len(json.dumps(frame, ensure_ascii=False).encode('utf-8'))
        self.frames.append((frame, size)); self.retained_bytes += size
        while self.frames and (len(self.frames)>MAX_PUBLICATIONS or self.retained_bytes>MAX_HISTORY_BYTES
                or step-self.frames[0][0]['step']>HISTORY_STEPS):
            _, old_size = self.frames.popleft(); self.retained_bytes -= old_size
        self.step, self.flights, self.finished = step, flights, finished
        self.known = {p['id'] for p in flights}
        self.ended = {p['id'] for p in finished}
        self.dropped = self.terminal_dropped

    def read(self, after):
        frames = [p for p,_ in self.frames if after is not None and p['sequence'] > after]
        rebase = (after is not None and (after > self.sequence or
            after < (self.frames[0][0]['sequence']-1 if self.frames else self.sequence) or
            any(p['gap'] for p in frames)))
        reset = after is None or rebase
        def packet(reset):
            return dict(interface=INTERFACE, sequence=self.sequence, base_sequence=None if reset else after,
                step=self.step, reset=reset, rebase=rebase, dropped_projectiles=self.dropped,
                starts=[definition(p) for p in self.flights] if reset else [v for p in frames for v in p['starts']],
                paths=[[p['id'], display_path(p['trajectory'])] for p in self.flights if 'trajectory' in p] if reset else [v for p in frames for v in p['paths']],
                shells=[[p['id'], p['shell_samples']] for p in self.flights if 'shell_samples' in p] if reset else [v for p in frames for v in p['shells']],
                missiles=[[p['id'],p['missile_samples'],p['missile_states']] for p in self.flights if 'missile_samples' in p] if reset else [v for p in frames for v in p['missiles']],
                ends=[terminal(p) for p in self.finished] if reset else [v for p in frames for v in p['ends']])
        result = packet(reset)
        if not reset and len(json.dumps(result,ensure_ascii=False).encode('utf-8')) > MAX_DELTA_BYTES:
            rebase = True
            result = packet(True)
        return result


def compact_projectile(value):
    # Current missile panel details remain available; display states are separate.
    return {k:v for k,v in value.items() if k not in (*DEFINITION_KEYS[1:], *SAMPLE_KEYS)}
