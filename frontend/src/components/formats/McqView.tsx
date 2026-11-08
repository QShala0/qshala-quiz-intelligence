import { Check, Star } from 'lucide-react';
import { McqPayload } from '../../lib/formatPayloads';

// MCQ: the stem, the options (lettered A-D by position when the deck prints no letters) and the correct one marked.
export default function McqView({ payload }: { payload: McqPayload }) {
  return (
    <div className="space-y-2 mt-1" data-testid="mcq-view">
      <div className="flex items-start gap-2">
        <div className="flex-1 font-semibold text-[13.5px] text-slate-900 leading-snug">{payload.prompt}</div>
        {payload.star && (
          <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-amber-50 border border-amber-200 px-1.5 py-0.5 text-[10.5px] font-bold text-amber-700">
            <Star className="h-3 w-3 fill-amber-400 text-amber-500" />Star
          </span>
        )}
      </div>
      <ul className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        {payload.options.map((o) => {
          const right = o.letter === payload.correct_letter;
          return (
            <li key={o.letter} className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[12.5px] ${right ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-slate-200 bg-white text-slate-800'}`}>
              <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold ${right ? 'bg-emerald-600 text-white' : 'bg-slate-200 text-slate-700'}`}>{o.letter}</span>
              <span className="flex-1 font-medium">{o.text}</span>
              {right && <Check className="h-4 w-4 shrink-0 text-emerald-600" aria-label="correct answer" />}
            </li>
          );
        })}
      </ul>
      {payload.letters_inferred && <div className="text-[10.5px] text-slate-400">The deck prints no letters; A–D follow the order on the slide.</div>}
      {!payload.correct_letter && <div className="text-[11.5px] font-semibold text-rose-600">The correct option could not be worked out.</div>}
    </div>
  );
}
