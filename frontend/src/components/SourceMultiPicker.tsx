'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, FolderOpen, Search, X } from 'lucide-react';
import { SourceCatalog, SourceDeckItem, SourceSeriesItem } from '../lib/types';

function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let diag = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const nextDiag = prev[j];
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + cost);
      diag = nextDiag;
    }
  }
  return prev[b.length];
}

function fuzzyIncludes(haystack: string, query: string): boolean {
  const h = haystack.toLowerCase();
  const q = query.trim().toLowerCase();
  if (!q) return true;
  if (h.includes(q)) return true;

  const qTokens = q.split(/[^a-z0-9]+/).filter(Boolean);
  const hTokens = h.split(/[^a-z0-9]+/).filter(Boolean);
  if (!qTokens.length) return true;

  return qTokens.every((qt) =>
    hTokens.some((ht) => {
      if (ht.includes(qt)) return true;
      if (qt.length < 4 || ht.length < 4) return false;
      const dist = levenshtein(qt, ht);
      const maxDist = qt.length >= 6 ? 2 : 1;
      if (dist <= maxDist) return true;
      return qt.length >= 6 && ht.length >= 6 && qt[0] === ht[0] && dist <= 3 && qt.slice(-2) === ht.slice(-2);
    })
  );
}

export function summarizeSelectedSources(selectedIds: string[], catalog: SourceCatalog | null): string[] {
  if (!selectedIds.length || !catalog) return [];
  const selSet = new Set(selectedIds);
  const covered = new Set<string>();
  const labels: string[] = [];

  for (const ser of catalog.series) {
    if (ser.document_ids.length > 0 && ser.document_ids.every((id) => selSet.has(id))) {
      labels.push(ser.deck_count > 1 ? `${ser.label} (${ser.deck_count} decks)` : ser.label);
      ser.document_ids.forEach((id) => covered.add(id));
    }
  }
  for (const deck of catalog.decks) {
    if (selSet.has(deck.id) && !covered.has(deck.id)) {
      labels.push(deck.title);
    }
  }
  return labels;
}

interface SourceMultiPickerProps {
  catalog: SourceCatalog | null;
  selectedIds: string[];
  onChange: (nextIds: string[]) => void;
  variant?: 'filter' | 'pill';
  placeholder?: string;
}

export default function SourceMultiPicker({
  catalog,
  selectedIds,
  onChange,
  variant = 'filter',
  placeholder,
}: SourceMultiPickerProps) {
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const seriesList = catalog?.series || [];
  const decksList = catalog?.decks || [];
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);

  const filteredSeries = useMemo(() => {
    if (!filterText.trim()) return seriesList;
    return seriesList.filter((s) => {
      if (fuzzyIncludes(s.label, filterText)) return true;
      return decksList.some((d) => d.series_key === s.key && fuzzyIncludes(`${d.title} ${d.filename}`, filterText));
    });
  }, [seriesList, decksList, filterText]);

  const filteredDecks = useMemo(() => {
    if (!filterText.trim()) return decksList;
    return decksList.filter((d) => fuzzyIncludes(`${d.title} ${d.filename} ${d.series}`, filterText));
  }, [decksList, filterText]);

  const buttonLabel = useMemo(() => {
    if (selectedIds.length === 0) {
      return placeholder || `All Sources (${decksList.length})`;
    }
    const summary = summarizeSelectedSources(selectedIds, catalog);
    if (summary.length === 1) return summary[0];
    if (summary.length > 1) return `${summary.length} sources (${selectedIds.length} decks)`;
    return `${selectedIds.length} source${selectedIds.length === 1 ? '' : 's'}`;
  }, [selectedIds, catalog, decksList.length, placeholder]);

  const toggleSeries = (ser: SourceSeriesItem) => {
    const allSelected = ser.document_ids.every((id) => selectedSet.has(id));
    const next = new Set(selectedSet);
    if (allSelected) {
      ser.document_ids.forEach((id) => next.delete(id));
    } else {
      ser.document_ids.forEach((id) => next.add(id));
    }
    onChange(Array.from(next));
  };

  const toggleDeck = (deck: SourceDeckItem) => {
    const next = new Set(selectedSet);
    if (next.has(deck.id)) next.delete(deck.id);
    else next.add(deck.id);
    onChange(Array.from(next));
  };

  const matchingFilteredIds = useMemo(() => filteredDecks.map((d) => d.id), [filteredDecks]);

  const selectAllMatching = () => {
    const next = new Set(selectedSet);
    matchingFilteredIds.forEach((id) => next.add(id));
    onChange(Array.from(next));
  };

  const triggerClass =
    variant === 'pill'
      ? `inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition cursor-pointer max-w-[260px] ${
          selectedIds.length > 0
            ? 'border-indigo-300 bg-indigo-50 text-indigo-800 hover:bg-indigo-100'
            : 'border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100 hover:text-slate-900'
        }`
      : `inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[13px] font-normal transition cursor-pointer max-w-[250px] ${
          selectedIds.length > 0
            ? 'border-blue-300 bg-blue-50 text-blue-800 hover:bg-blue-100'
            : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
        }`;

  return (
    <div ref={containerRef} className="relative inline-block">
      <div className="inline-flex items-center gap-1">
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          aria-expanded={open}
          aria-haspopup="listbox"
          className={triggerClass}
          title={selectedIds.length > 0 ? buttonLabel : 'Filter by presentation deck or source series'}
        >
          <FolderOpen className="h-3.5 w-3.5 shrink-0 opacity-75" />
          <span className="truncate">{buttonLabel}</span>
          <ChevronDown className={`h-3.5 w-3.5 shrink-0 opacity-60 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        {selectedIds.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            aria-label="Clear source filter"
            title="Clear selected sources"
            className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute left-0 z-50 mt-1.5 w-80 sm:w-96 rounded-xl border border-slate-200 bg-white shadow-xl ring-1 ring-slate-900/5 overflow-hidden">
          <div className="p-2.5 border-b border-slate-100 bg-slate-50/70 space-y-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
              <input
                type="text"
                value={filterText}
                onChange={(e) => setFilterText(e.target.value)}
                placeholder="Search source series or deck (e.g. Agastya)..."
                autoFocus
                className="w-full rounded-lg border border-slate-200 bg-white pl-8 pr-7 py-1.5 text-[12.5px] text-slate-800 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none"
              />
              {filterText && (
                <button
                  type="button"
                  onClick={() => setFilterText('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 cursor-pointer"
                  aria-label="Clear search"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <div className="flex items-center justify-between text-[11.5px] text-slate-500 px-0.5">
              <span>{selectedIds.length === 0 ? 'Searching all sources' : `${selectedIds.length} deck${selectedIds.length === 1 ? '' : 's'} selected`}</span>
              <div className="flex items-center gap-2.5">
                {filterText.trim() && matchingFilteredIds.length > 0 && (
                  <button
                    type="button"
                    onClick={selectAllMatching}
                    className="font-semibold text-blue-600 hover:underline cursor-pointer"
                  >
                    Select {matchingFilteredIds.length} matching
                  </button>
                )}
                {selectedIds.length > 0 && (
                  <button
                    type="button"
                    onClick={() => onChange([])}
                    className="font-medium text-slate-600 hover:text-slate-900 hover:underline cursor-pointer"
                  >
                    All sources
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="max-h-72 overflow-y-auto divide-y divide-slate-100">
            {decksList.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12.5px] text-slate-400">
                No uploaded decks found.
              </div>
            ) : (
              <>
                {/* Source Series Groups */}
                {filteredSeries.some((s) => s.deck_count > 1) && (
                  <div className="p-2">
                    <div className="px-2 py-1 text-[10.5px] font-bold uppercase tracking-wider text-slate-400">
                      Source Series (All Sets)
                    </div>
                    {filteredSeries
                      .filter((s) => s.deck_count > 1)
                      .map((ser) => {
                        const checkedCount = ser.document_ids.filter((id) => selectedSet.has(id)).length;
                        const allChecked = checkedCount === ser.document_ids.length;
                        const partial = checkedCount > 0 && !allChecked;
                        return (
                          <button
                            key={ser.key}
                            type="button"
                            onClick={() => toggleSeries(ser)}
                            className={`w-full flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[12.5px] transition cursor-pointer ${
                              allChecked
                                ? 'bg-blue-50 text-blue-900 font-medium'
                                : 'hover:bg-slate-50 text-slate-700'
                            }`}
                          >
                            <span
                              className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                                allChecked
                                  ? 'border-blue-600 bg-blue-600 text-white'
                                  : partial
                                  ? 'border-blue-500 bg-blue-100 text-blue-700'
                                  : 'border-slate-300 bg-white'
                              }`}
                            >
                              {allChecked && <Check className="h-3 w-3" />}
                              {partial && <span className="h-1.5 w-1.5 rounded-xs bg-blue-600" />}
                            </span>
                            <span className="flex-1 truncate font-medium">{ser.label}</span>
                            <span className="shrink-0 text-[11px] text-slate-400 font-mono">
                              {ser.deck_count}d · {ser.question_count}q
                            </span>
                          </button>
                        );
                      })}
                  </div>
                )}

                {/* Individual Decks */}
                <div className="p-2">
                  <div className="px-2 py-1 text-[10.5px] font-bold uppercase tracking-wider text-slate-400">
                    Individual Presentations / Decks
                  </div>
                  {filteredDecks.length === 0 ? (
                    <div className="px-2 py-3 text-center text-[12px] text-slate-400">
                      No matching decks for &ldquo;{filterText}&rdquo;
                    </div>
                  ) : (
                    filteredDecks.map((deck) => {
                      const checked = selectedSet.has(deck.id);
                      return (
                        <button
                          key={deck.id}
                          type="button"
                          onClick={() => toggleDeck(deck)}
                          className={`w-full flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-[12.5px] transition cursor-pointer ${
                            checked
                              ? 'bg-blue-50 text-blue-900 font-medium'
                              : 'hover:bg-slate-50 text-slate-700'
                          }`}
                        >
                          <span
                            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                              checked
                                ? 'border-blue-600 bg-blue-600 text-white'
                                : 'border-slate-300 bg-white'
                            }`}
                          >
                            {checked && <Check className="h-3 w-3" />}
                          </span>
                          <span className="flex-1 truncate" title={deck.title || deck.filename}>
                            {deck.title || deck.filename}
                          </span>
                          <span className="shrink-0 text-[11px] text-slate-400 font-mono">
                            {deck.question_count}q
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
