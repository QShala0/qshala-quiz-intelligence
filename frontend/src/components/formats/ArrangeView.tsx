'use client';

import { useState } from 'react';
import { api } from '../../lib/api';
import { ArrangePayload } from '../../lib/formatPayloads';
import { HistoricalQuestion } from '../../lib/types';

interface Props {
  questionId: string;
  payload: ArrangePayload;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage: (url: string) => void;
  /** Called with the saved question after the order is entered by hand (or cleared). */
  onOrderSaved?: (q: HistoricalQuestion) => void;
  readOnly?: boolean;
}

const SOURCE: Record<string, { text: string; cls: string }> = {
  computed: { text: 'computed from the values', cls: 'bg-emerald-50 border-emerald-200 text-emerald-700' },
  human: { text: 'entered by hand', cls: 'bg-indigo-50 border-indigo-200 text-indigo-700' },
  none: { text: 'not recorded', cls: 'bg-rose-50 border-rose-200 text-rose-700' },
};

// Arrange the Order: four items carrying the labels the audience writes down (3-1-4-2). The deck never states the
// right order; it is computed from the reveal values when they parse, otherwise a person enters it here.
export default function ArrangeView({ questionId, payload, getImageSrc, handleImgError, setPreviewImage, onOrderSaved, readOnly }: Props) {
  const items = [...payload.items].sort((a, b) => a.label - b.label);
  const labels = items.map((i) => i.label);
  const byLabel = new Map(items.map((i) => [i.label, i]));
  const order = payload.correct_order || [];
  const source = SOURCE[payload.order_source] || SOURCE.none;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<number[]>(() => (order.length === labels.length ? order : labels.map(() => 0)));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const draftValid = draft.length === labels.length && new Set(draft).size === labels.length && draft.every((d) => labels.includes(d));

  const save = async (next: number[]) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.setArrangeOrder(questionId, next);
      onOrderSaved?.(updated);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the order');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-2 mt-1" data-testid="arrange-view">
      {payload.prompt && <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">{payload.prompt}</div>}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
        {payload.direction && (
          <span className="rounded-full bg-purple-50 border border-purple-200 px-2 py-0.5 text-purple-700">
            {payload.direction === 'desc' ? 'Descending: biggest first' : 'Ascending: smallest / earliest first'}
          </span>
        )}
        <span className="rounded-full bg-slate-100 border border-slate-200 px-2 py-0.5 text-slate-700">
          4 correct +{payload.scoring.all_correct} · 2 correct +{payload.scoring.two_correct} · 1 correct +{payload.scoring.one_correct}
        </span>
        <span className={`rounded-full border px-2 py-0.5 ${source.cls}`} data-testid="order-source">Order: {source.text}</span>
      </div>

      <ul className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {items.map((it) => (
          <li key={it.label} className="rounded-lg border border-purple-200 bg-purple-50/50 p-2 text-[12px]">
            <div className="flex items-center gap-2">
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-purple-700 text-[13px] font-extrabold text-white">{it.label}</span>
              <span className="font-semibold text-slate-900 leading-tight">{it.text}</span>
            </div>
            {it.image && (
              <button type="button" onClick={() => setPreviewImage(it.image as string)} className="mt-1.5 block w-full overflow-hidden rounded border border-purple-200 bg-white cursor-pointer">
                <img src={getImageSrc(it.image)} onError={() => handleImgError(it.image as string)} alt={it.text} className="h-14 w-full object-contain" />
              </button>
            )}
            {it.value && <div className="mt-1 font-mono text-[11px] text-slate-600">{it.value}</div>}
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <span className="font-bold text-purple-700">Correct order:</span>
        {order.length > 0 ? (
          <span className="font-semibold text-slate-900" data-testid="correct-order">
            {order.map((l) => `${l}`).join(' – ')}
            <span className="ml-2 font-normal text-slate-500">
              ({order.map((l) => byLabel.get(l)?.text ?? l).join(' → ')})
            </span>
          </span>
        ) : (
          <span className="font-semibold text-rose-600">not recorded</span>
        )}
        {!editing && !readOnly && (
          <button type="button" onClick={() => { setDraft(order.length === labels.length ? order : labels.map(() => 0)); setEditing(true); }}
            className="rounded border border-purple-300 bg-white px-2 py-0.5 text-[11.5px] font-semibold text-purple-700 hover:bg-purple-50 cursor-pointer">
            {order.length > 0 ? 'Edit order' : 'Enter order'}
          </button>
        )}
      </div>

      {editing && (
        <div className="rounded-lg border border-purple-300 bg-white p-2.5 space-y-2" data-testid="order-editor">
          <div className="text-[11.5px] text-slate-600">Pick the item label for each place, first to last.</div>
          <div className="flex flex-wrap gap-2">
            {draft.map((val, idx) => (
              <label key={idx} className="flex items-center gap-1 text-[12px] font-semibold text-slate-700">
                {idx + 1}.
                <select
                  value={val}
                  onChange={(e) => setDraft((d) => d.map((x, i) => (i === idx ? Number(e.target.value) : x)))}
                  className="rounded border border-slate-300 bg-white px-1.5 py-1 text-[12px] cursor-pointer"
                  aria-label={`Place ${idx + 1}`}
                >
                  <option value={0}>–</option>
                  {labels.map((l) => (
                    <option key={l} value={l}>{l} · {byLabel.get(l)?.text}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          {error && <div className="text-[11.5px] font-semibold text-rose-600">{error}</div>}
          <div className="flex items-center gap-2 text-[12px]">
            <button type="button" disabled={!draftValid || saving} onClick={() => save(draft)}
              className="rounded bg-purple-600 px-2.5 py-1 font-semibold text-white hover:bg-purple-700 disabled:opacity-40 cursor-pointer">
              {saving ? 'Saving…' : 'Save order'}
            </button>
            {order.length > 0 && (
              <button type="button" disabled={saving} onClick={() => save([])}
                className="rounded border border-rose-200 bg-white px-2 py-1 font-semibold text-rose-600 hover:bg-rose-50 cursor-pointer">
                Clear order
              </button>
            )}
            <button type="button" onClick={() => setEditing(false)} className="rounded px-2 py-1 font-semibold text-slate-500 hover:text-slate-800 cursor-pointer">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
