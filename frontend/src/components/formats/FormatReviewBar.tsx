import { AlertTriangle, CheckCircle } from 'lucide-react';
import { HistoricalQuestion } from '../../lib/types';
import { confidenceTone, extractionOf, formatStyle, playModeLabel } from '../../lib/formatPayloads';

interface Props {
  q: HistoricalQuestion;
  busy: boolean;
  onConfirm: (id: string) => void;
}

// Shown in the Needs Review tab for a question whose FORMAT reading was flagged at ingestion: which format was read,
// how sure the reader was, how the Arrange order was obtained, and why it was flagged.
export default function FormatReviewBar({ q, busy, onConfirm }: Props) {
  const ex = extractionOf(q.format_payload);
  if (!q.format_needs_review && !ex?.needs_review) return null;
  const style = formatStyle(q.question_format);
  const payload = q.format_payload as { order_source?: string } | null;
  const pct = ex ? Math.round(ex.confidence * 100) : null;
  return (
    <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50/60 px-3 py-2 text-[12px]" data-testid="format-review-bar">
      <div className="flex flex-wrap items-center gap-1.5">
        <AlertTriangle className="h-3.5 w-3.5 text-rose-600" />
        <span className="font-semibold text-rose-800">Format needs a look</span>
        <span className={`rounded-full border px-2 py-0.5 text-[10.5px] font-bold ${style.chip}`} data-testid="review-format">{style.label}</span>
        {pct !== null && (
          <span className={`rounded-full border px-2 py-0.5 text-[10.5px] font-bold ${confidenceTone(ex!.confidence)}`} data-testid="review-confidence">
            {pct}% sure
          </span>
        )}
        {payload?.order_source && (
          <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10.5px] font-bold text-slate-700" data-testid="review-order-source">
            order: {payload.order_source}
          </span>
        )}
        {q.slot && q.slot !== 'LIVE' && (
          <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10.5px] font-bold text-slate-600">{q.slot.toLowerCase()}</span>
        )}
        {playModeLabel(q.play_mode, (q.format_payload as { team?: number | null } | null)?.team) && (
          <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10.5px] font-bold text-slate-600">
            {playModeLabel(q.play_mode, (q.format_payload as { team?: number | null } | null)?.team)}
          </span>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => onConfirm(q.id)}
          className="ml-auto inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white hover:bg-emerald-700 disabled:opacity-50 cursor-pointer"
          title="The reading is right as it stands"
        >
          <CheckCircle className="h-3 w-3" />
          Confirm reading
        </button>
      </div>
      {ex && ex.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-[11.5px] text-rose-900/80">
          {ex.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}
