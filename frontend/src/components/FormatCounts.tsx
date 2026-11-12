'use client';

import { formatStyle } from '../lib/formatPayloads';

export interface VaultCounts {
  counts: Record<string, number>;
  total: number;
  slots: Record<string, number>;
  included_slots: string[];
}

interface Props {
  data: VaultCounts | null;
  includeReserve: boolean;
  includeExample: boolean;
  onToggleReserve: (v: boolean) => void;
  onToggleExample: (v: boolean) => void;
  picked: string;
  onPick: (format: string) => void;
}

// What the quiz can draw from, by format. Live questions only by default: hidden spare slides (RESERVE) and the
// example slides (EXAMPLE) are left out unless ticked. Clicking a format asks for that format only.
export default function FormatCounts({ data, includeReserve, includeExample, onToggleReserve, onToggleExample, picked, onPick }: Props) {
  if (!data || data.total === 0) return null;
  const entries = Object.entries(data.counts);
  return (
    <div className="mt-4 text-[13px] text-slate-600" data-testid="format-counts">
      <div className="flex flex-wrap items-center justify-center gap-1.5">
        <span className="text-slate-500">{data.total} questions to draw from:</span>
        {entries.map(([fmt, n]) => {
          const on = picked === fmt;
          return (
            <button
              key={fmt}
              type="button"
              onClick={() => onPick(on ? '' : fmt)}
              aria-pressed={on}
              className={`rounded-full border px-2.5 py-0.5 text-[12px] font-semibold transition ${
                on ? 'border-slate-900 bg-slate-900 text-white' : `${formatStyle(fmt).chip} hover:brightness-95`
              }`}
              title={on ? 'Click to allow every format again' : `Only ${formatStyle(fmt).label} questions`}
            >
              {formatStyle(fmt).label} <span className="font-mono">{n}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-center gap-4 text-[12px]">
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={includeReserve} onChange={(e) => onToggleReserve(e.target.checked)} />
          Include reserve spares ({data.slots.RESERVE ?? 0})
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={includeExample} onChange={(e) => onToggleExample(e.target.checked)} />
          Include example slides ({data.slots.EXAMPLE ?? 0})
        </label>
      </div>
    </div>
  );
}
