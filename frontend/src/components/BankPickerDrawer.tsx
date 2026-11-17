'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Plus, Search, X, AlertTriangle } from 'lucide-react';
import { api, isAbortError } from '../lib/api';
import { HistoricalQuestion, SourceCatalog } from '../lib/types';
import { FORMAT_FILTER_OPTIONS, formatStyle } from '../lib/formatPayloads';
import SourceMultiPicker from './SourceMultiPicker';
import Highlight from './Highlight';

const PAGE = 30;

// Side drawer on the quiz-review screen: search the question bank (exact words, highlighted) and add questions one
// click at a time without leaving the quiz. Stays open so several can be added in a row.
export default function BankPickerDrawer({
  open,
  onClose,
  existingIds,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  existingIds: Set<string>;
  onAdd: (q: HistoricalQuestion) => void;
}) {
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [format, setFormat] = useState('');
  const [deckIds, setDeckIds] = useState<string[]>([]);
  const [catalog, setCatalog] = useState<SourceCatalog | null>(null);
  const [items, setItems] = useState<HistoricalQuestion[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [addedNow, setAddedNow] = useState(0);
  const inFlight = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const params = useCallback((offset: number, signal: AbortSignal) => ({
    query: query.trim() || undefined,
    match: 'exact' as const,
    question_format: format || undefined,
    document_ids: deckIds.length > 0 ? deckIds : undefined,
    sort_by: 'created_at',
    sort_order: 'desc' as const,
    limit: PAGE,
    offset,
    signal,
  }), [query, format, deckIds]);

  // Search as you type (debounced), so there is no Search button to hunt for.
  useEffect(() => {
    if (!open) return;
    const handle = setTimeout(async () => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      setLoading(true);
      setError('');
      try {
        const res = await api.searchQuestionsPage(params(0, controller.signal));
        setItems(res.items);
        setTotal(res.total);
        setApplied(query.trim());
      } catch (e) {
        if (isAbortError(e)) return;
        setError(e instanceof Error ? e.message : 'Could not search the question bank.');
      } finally {
        if (inFlight.current === controller) setLoading(false);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [open, params, query]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    if (!catalog) api.getDocumentSources().then(setCatalog).catch(() => {});
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); inFlight.current?.abort(); };
  }, [open, catalog, onClose]);

  async function loadMore() {
    if (loadingMore) return;
    const controller = new AbortController();
    setLoadingMore(true);
    try {
      const res = await api.searchQuestionsPage(params(items.length, controller.signal));
      setItems((cur) => {
        const seen = new Set(cur.map((x) => x.id));
        return [...cur, ...res.items.filter((x) => !seen.has(x.id))];
      });
      setTotal(res.total);
    } catch (e) {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : 'Could not load more.');
    } finally {
      setLoadingMore(false);
    }
  }

  if (!open) return null;
  const hasMore = total !== null ? items.length < total : items.length > 0 && items.length % PAGE === 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Add questions from the question bank">
      <button type="button" aria-label="Close" onClick={onClose} className="absolute inset-0 bg-slate-900/30 cursor-default" />
      <div className="relative flex h-full w-full max-w-xl flex-col bg-white shadow-2xl border-l border-slate-200">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 className="text-[16px] font-semibold text-slate-900">Add from question bank</h2>
            <p className="text-[12px] text-slate-500">{addedNow > 0 ? `${addedNow} added to this quiz` : 'Search, then click Add. You can add several.'}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full p-1.5 text-slate-500 hover:bg-slate-100 cursor-pointer">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-2.5 border-b border-slate-100 px-5 py-3">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Type a word, or “a phrase in quotes”…"
              className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-[14px] text-slate-900 placeholder:text-slate-400 focus:outline-none"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SourceMultiPicker catalog={catalog} selectedIds={deckIds} onChange={setDeckIds} />
            <select
              value={format}
              onChange={(e) => setFormat(e.target.value)}
              aria-label="Format"
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] text-slate-700"
            >
              <option value="">All formats</option>
              {FORMAT_FILTER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
            <span className="ml-auto text-[12px] text-slate-500" aria-live="polite">
              {loading ? 'Searching…' : total !== null ? `${total} match${total === 1 ? '' : 'es'}` : ''}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3">
          {error && <p className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}
          {!loading && !error && items.length === 0 && (
            <p className="py-10 text-center text-[14px] text-slate-500">No question contains {applied ? <>“{applied}”</> : 'those filters'}. Try fewer words.</p>
          )}
          <ul className="space-y-2">
            {items.map((q) => {
              const added = existingIds.has(q.id);
              return (
                <li key={q.id} className={`rounded-xl border px-3.5 py-3 ${added ? 'border-emerald-200 bg-emerald-50/50' : 'border-slate-200 bg-white'}`}>
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[10.5px] font-semibold">
                        <span className={`rounded-full border px-2 py-0.5 ${formatStyle(q.question_format).chip}`}>{formatStyle(q.question_format).label}</span>
                        {q.format_needs_review && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-amber-800">
                            <AlertTriangle size={10} /> Needs review
                          </span>
                        )}
                      </div>
                      <p className="text-[13.5px] font-medium leading-snug text-slate-900"><Highlight text={q.question_text} query={applied} /></p>
                      <p className="mt-1 text-[12.5px] text-slate-600">
                        <span className="font-semibold text-emerald-700">Answer:</span> <Highlight text={q.answer} query={applied} />
                      </p>
                      <p className="mt-1 text-[11px] text-slate-400">{[q.topic, q.document_title].filter(Boolean).join(' · ')}</p>
                    </div>
                    <button
                      type="button"
                      disabled={added}
                      onClick={() => { onAdd(q); setAddedNow((n) => n + 1); }}
                      className={`inline-flex shrink-0 items-center gap-1 rounded-full px-3 py-1.5 text-[12.5px] font-semibold transition cursor-pointer ${
                        added ? 'bg-emerald-100 text-emerald-700 cursor-default' : 'bg-slate-900 text-white hover:bg-slate-700'
                      }`}
                    >
                      {added ? <><Check size={13} /> Added</> : <><Plus size={13} /> Add</>}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          {hasMore && (
            <button type="button" onClick={loadMore} disabled={loadingMore}
              className="mt-3 w-full rounded-lg border border-slate-200 py-2 text-[13px] font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 cursor-pointer">
              {loadingMore ? 'Loading…' : 'Load more'}
            </button>
          )}
        </div>

        <div className="border-t border-slate-200 px-5 py-3">
          <button type="button" onClick={onClose}
            className="w-full rounded-full bg-slate-900 py-2.5 text-[14px] font-medium text-white hover:bg-slate-700 cursor-pointer">
            Done{addedNow > 0 ? ` (${addedNow} added)` : ''}
          </button>
        </div>
      </div>
    </div>
  );
}
