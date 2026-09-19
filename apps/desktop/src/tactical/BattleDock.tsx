import { useId, useState } from 'react';
import type { ReactNode } from 'react';

/** Overlay visibility is deliberately independent of the viewport and its camera. */
export function BattleDock({ name, symbol, position, children, initiallyOpen = true, onOpenChange, open: controlledOpen }: {
  name: string; symbol: string; position: string; children: ReactNode;
  initiallyOpen?: boolean; onOpenChange?: (open: boolean) => void; open?: boolean;
}) {
  const [localOpen, setOpen] = useState(initiallyOpen), id = useId();
  const open = controlledOpen ?? localOpen;
  return <aside className={`battle-dock dock-${position}${open ? '' : ' is-collapsed'}`} aria-label={name}>
    <div className="battle-dock-body" id={id} inert={!open}>{children}</div>
    <button className="battle-dock-tab" aria-label={`${open ? '收起' : '展开'}${name}`} title={name}
      aria-expanded={open} aria-controls={id} onClick={() => { setOpen(!open); onOpenChange?.(!open); }}>
      <span aria-hidden="true">{symbol}</span>
    </button>
  </aside>;
}

export type BattlePages = { service: 'ship' | 'damage' | 'cargo'; weapon: 'weapons' | 'missiles';
  missile: 'launch' | 'flight' | 'stores'; launcher: string | null; sensors: boolean; ew: boolean };
export const DEFAULT_BATTLE_PAGES: BattlePages = { service: 'ship', weapon: 'weapons', missile: 'launch', launcher: null, sensors: false, ew: false };
