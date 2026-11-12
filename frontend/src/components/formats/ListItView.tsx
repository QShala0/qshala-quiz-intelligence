import { Check, X } from 'lucide-react';
import { ListItPayload } from '../../lib/formatPayloads';

// List It: nine options on screen, six are correct (green tick), three are wrong (red cross).
export default function ListItView({ payload }: { payload: ListItPayload }) {
  const right = payload.options.filter((o) => o.correct === true).length;
  const wrong = payload.options.filter((o) => o.correct === false).length;
  return (
    <div className="space-y-2 mt-1" data-testid="listit-view">
      {payload.prompt && (
        <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">{payload.prompt}</div>
      )}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
        {payload.team ? <span className="rounded-full bg-indigo-50 border border-indigo-200 px-2 py-0.5 text-indigo-700">Team {payload.team}</span> : null}
        <span className="rounded-full bg-slate-100 border border-slate-200 px-2 py-0.5 text-slate-700">Pick up to {payload.max_picks}</span>
        {payload.scoring?.per_correct != null && (
          <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-emerald-700">+{payload.scoring.per_correct} each</span>
        )}
        {payload.timer_seconds ? <span className="rounded-full bg-slate-100 border border-slate-200 px-2 py-0.5 text-slate-700">{payload.timer_seconds}s</span> : null}
        <span className="text-slate-500 font-medium">{right} correct · {wrong} wrong</span>
      </div>
      <ul className="grid grid-cols-1 sm:grid-cols-3 gap-1.5">
        {payload.options.map((o, i) => (
          <li
            key={i}
            className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[12.5px] leading-snug ${
              o.correct === true
                ? 'border-emerald-300 bg-emerald-50 text-emerald-900'
                : o.correct === false
                ? 'border-rose-200 bg-rose-50/70 text-rose-900'
                : 'border-slate-200 bg-slate-50 text-slate-700'
            }`}
          >
            {o.correct === true ? (
              <Check className="h-4 w-4 shrink-0 text-emerald-600" aria-label="correct" />
            ) : o.correct === false ? (
              <X className="h-4 w-4 shrink-0 text-rose-600" aria-label="wrong" />
            ) : (
              <span className="h-4 w-4 shrink-0 rounded-full border border-slate-300" aria-label="unknown" />
            )}
            <span className="font-medium">{o.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
