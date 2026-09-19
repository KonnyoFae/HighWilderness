import { describe, expect, it } from 'vitest';
import { changeHelm, helmControl, NEUTRAL_HELM, readHelm } from './FlagshipHelm';

describe('independent flagship orders', () => {
  it('changing throttle preserves turn and changing turn preserves throttle through the real control contract', () => {
    let order = changeHelm(NEUTRAL_HELM, { notch: 'half' });
    order = changeHelm(order, { turn: -1 });
    order = changeHelm(order, { notch: 'full' });
    expect(readHelm(helmControl(order))).toEqual({ draft: { notch: 'full', direction: 'forward', brake: false }, turn: -1, turnPercent: 50, stabilize: false });
    order = changeHelm(order, { turn: 0 });
    expect(readHelm(helmControl(order)).draft.notch).toBe('full');
    order = changeHelm(changeHelm(order, { turn: 1 }), { notch: 'stop' });
    expect(readHelm(helmControl(order)).turn).toBe(1);
  });
  it('brake obeys the existing no-yaw contract and release does not resume an old maneuver', () => {
    const moving = changeHelm(changeHelm(NEUTRAL_HELM, { notch: 'full' }), { turn: 1 });
    const braking = changeHelm(moving, { brake: true });
    expect(helmControl(braking).automatic_brake).toBe(true);
    expect(helmControl(braking).channel_commands.filter(c => c.command_channel.startsWith('yaw.')).every(c => c.target_output_percent === 0)).toBe(true);
    expect(readHelm(helmControl(changeHelm(braking, { brake: false })))).toEqual({ ...NEUTRAL_HELM, stabilize: true });
  });
  it('reclicking a turn notch requests automatic zero-rate stabilization; other direction or power changes the turn', () => {
    const half = changeHelm(NEUTRAL_HELM, { turn: -1, power: 50 });
    const full = changeHelm(half, { turn: -1, power: 100 });
    expect(full).toMatchObject({ turn: -1, turnPercent: 100, stabilize: false });
    const stop = changeHelm(full, { turn: -1, power: 100 });
    expect(helmControl(stop)).toMatchObject({ interface: 'gaotian.tactical-propulsion-control/v3alpha1', automatic_yaw_brake: true });
    expect(helmControl(stop).channel_commands.filter(c => c.command_channel.startsWith('yaw.')).every(c => c.target_output_percent === 0)).toBe(true);
    expect(changeHelm(stop, { turn: 1, power: 50 })).toMatchObject({ turn: 1, turnPercent: 50, stabilize: false });
  });
  it('retains a chosen reverse direction while stopped without inventing reverse thrust', () => {
    const idle = changeHelm(NEUTRAL_HELM, { direction: 'reverse' });
    const confirmed = readHelm(helmControl(idle), 'reverse');
    expect(confirmed.draft).toMatchObject({ direction: 'reverse', notch: 'stop' });
    const go = helmControl(changeHelm(confirmed, { notch: 'half' }));
    expect(go.channel_commands.find(c => c.command_channel === 'translation.reverse')?.commanded_notch).toBe('half');
    expect(go.channel_commands.find(c => c.command_channel === 'translation.forward')?.commanded_notch).toBe('stop');
  });
});
