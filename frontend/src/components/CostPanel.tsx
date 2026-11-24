'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import {
  ChevronDown,
  ChevronUp,
  Receipt,
  ArrowRight,
  RefreshCw,
  Clock,
} from 'lucide-react';
import { api } from '../lib/api';
import {
  CostsSimpleResponse,
  CostsSummaryResponse,
  DocumentCostItem,
  CostRefreshStatusResponse,
} from '../lib/types';

function formatInr(val?: number | null): string {
  const num = Number(val || 0);
  return `₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPeriod(period?: string | null): string {
  if (!period) return 'This Month';
  const parts = period.split('-');
  if (parts.length === 2) {
    const year = parseInt(parts[0], 10);
    const month = parseInt(parts[1], 10) - 1;
    const d = new Date(Date.UTC(year, month, 1));
    return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
  }
  return period;
}

export default function CostPanel() {
  const [simpleData, setSimpleData] = useState<CostsSimpleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Dropdown open/close state
  const [isExpanded, setIsExpanded] = useState(false);

  // Lazy loaded details
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [summaryData, setSummaryData] = useState<CostsSummaryResponse | null>(null);
  const [recentDecks, setRecentDecks] = useState<DocumentCostItem[]>([]);
  const [refreshStatus, setRefreshStatus] = useState<CostRefreshStatusResponse | null>(null);

  const [refreshKey, setRefreshKey] = useState(0);

  const handleManualRefresh = () => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  };

  const fetchDetails = useCallback(async () => {
    if (summaryData) return; // already loaded
    setDetailsLoading(true);
    try {
      const [sum, docs, refStatus] = await Promise.all([
        api.getCostsSummary().catch(() => null),
        api.getCostsDocuments(5).catch(() => ({ total: 0, items: [], documents: [] })),
        api.getCostsRefreshStatus().catch(() => null),
      ]);
      if (sum) setSummaryData(sum);
      const deckList = (docs?.items ?? docs?.documents ?? []) as DocumentCostItem[];
      if (deckList.length > 0) setRecentDecks(deckList);
      if (refStatus) setRefreshStatus(refStatus);
    } catch (err) {
      console.warn('Failed to load cost details:', err);
    } finally {
      setDetailsLoading(false);
    }
  }, [summaryData]);

  useEffect(() => {
    let active = true;
    api.getCostsSimple()
      .then((data) => {
        if (active) {
          setSimpleData(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (active) {
          console.error('Failed to load simple costs:', err);
          setError(err instanceof Error ? err.message : 'Unable to load operating costs');
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [refreshKey]);

  const handleToggle = () => {
    const next = !isExpanded;
    setIsExpanded(next);
    if (next && !summaryData) {
      void fetchDetails();
    }
  };

  // 6 specific lines as mandated
  const lines = simpleData?.lines && simpleData.lines.length === 5
    ? simpleData.lines
    : [
        { key: 'gemini', label: 'Gemini AI', inr: simpleData?.gemini ?? 0, approx: simpleData?.approx },
        { key: 'database', label: 'Neon Database', inr: simpleData?.database ?? 0, approx: false },
        { key: 'storage', label: 'Cloudflare R2 Storage', inr: simpleData?.storage ?? 0, approx: false },
        { key: 'compute', label: 'Cloud Run Compute', inr: simpleData?.compute ?? 0, approx: false },
        { key: 'other', label: 'Vercel & GCP Other', inr: simpleData?.other ?? 0, approx: false },
      ];

  const hasAnyEstimate = simpleData?.approx || lines.some((l) => l.approx);

  return (
    <div
      data-testid="cost-panel"
      className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50 text-slate-800 transition-all"
    >
      {/* Header */}
      <div className="border-b border-slate-100 pb-3 mb-3.5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-600">
            <Receipt className="h-3.5 w-3.5" />
          </div>
          <div>
            <h3 className="text-[14px] font-bold tracking-tight text-slate-900 leading-none">
              Operating Costs
            </h3>
            <span className="text-[11.5px] text-slate-400 font-normal">
              {formatPeriod(simpleData?.period)}
            </span>
          </div>
        </div>

        {hasAnyEstimate && (
          <span
            title="Some figures are model-based estimates awaiting month-end provider invoices"
            className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500 border border-slate-200/60"
          >
            <span>~ estimated</span>
          </span>
        )}
      </div>

      {/* Error state */}
      {error && !loading && (
        <div className="py-3 text-center text-[12px] text-slate-500">
          <p>Cost ledger currently unavailable.</p>
          <button
            type="button"
            onClick={handleManualRefresh}
            className="mt-1.5 inline-flex items-center gap-1 text-[11.5px] font-medium text-blue-600 hover:underline cursor-pointer"
          >
            <RefreshCw className="h-3 w-3" />
            <span>Retry</span>
          </button>
        </div>
      )}

      {/* Loading Skeleton */}
      {loading && !simpleData && (
        <div className="space-y-2 py-1">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="flex justify-between items-center py-0.5">
              <div className="h-3.5 w-24 bg-slate-100 rounded animate-pulse" />
              <div className="h-3.5 w-14 bg-slate-100 rounded animate-pulse" />
            </div>
          ))}
        </div>
      )}

      {/* 6 Clean Lines: Gemini, Database, Storage, Compute, Other, and Total */}
      {!loading && simpleData && (
        <div className="space-y-1.5 text-[12.5px]">
          {lines.map((line) => (
            <div key={line.key} className="flex items-center justify-between text-slate-600 py-0.5">
              <div className="flex items-center gap-1.5">
                <span>{line.label}</span>
                {line.approx && (
                  <span
                    className="text-[10.5px] font-medium text-slate-400"
                    title="Estimated from usage tokens & rate card v1"
                  >
                    ~
                  </span>
                )}
              </div>
              <span className="font-medium text-slate-800 tabular-nums">
                {formatInr(line.inr)}
              </span>
            </div>
          ))}

          {/* Line 6: Total (This Month) */}
          <div className="pt-2 mt-2 border-t border-slate-100 flex items-center justify-between text-[13px] font-semibold text-slate-900">
            <div className="flex items-center gap-1.5">
              <span>Total (This Month)</span>
              {hasAnyEstimate && (
                <span className="text-[10.5px] font-normal text-slate-400">~</span>
              )}
            </div>
            <span className="text-[14px] font-bold text-slate-900 tabular-nums">
              {formatInr(simpleData.total ?? simpleData.total_inr)}
            </span>
          </div>
        </div>
      )}

      {/* Toggle Details Dropdown */}
      <div className="mt-3.5 pt-2.5 border-t border-slate-100/80">
        <button
          type="button"
          onClick={handleToggle}
          data-testid="cost-panel-details-toggle"
          className="w-full flex items-center justify-between text-[12px] font-medium text-slate-500 hover:text-slate-800 transition-colors py-0.5 cursor-pointer select-none"
          aria-expanded={isExpanded}
        >
          <span className="flex items-center gap-1">
            <span>Details</span>
            {isExpanded ? (
              <ChevronUp className="h-3.5 w-3.5 text-slate-400" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
            )}
          </span>
          <span className="text-[11px] text-slate-400 font-normal">
            {isExpanded ? 'Hide' : 'Decks, stages & history'}
          </span>
        </button>

        {/* Collapsed Drop-down Drawer */}
        {isExpanded && (
          <div className="mt-3 pt-3 border-t border-slate-100 space-y-4 text-[12px] animate-fadeIn">
            {detailsLoading && !summaryData ? (
              <div className="py-4 text-center text-slate-400 text-[11.5px] animate-pulse">
                Loading cost details…
              </div>
            ) : (
              <>
                {/* 1. Comparison: Last Month & All Time */}
                {(() => {
                  const curMonth = summaryData?.current_month ?? summaryData?.periods?.current_month;
                  const lastMonth = summaryData?.last_month ?? summaryData?.periods?.last_month;
                  const allTime = summaryData?.all_time ?? summaryData?.periods?.all_time;
                  return (
                    <>
                      <div className="rounded-lg bg-slate-50/70 p-2.5 space-y-1.5 border border-slate-100">
                        <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                          Historical Spend
                        </div>
                        <div className="flex items-center justify-between text-slate-600">
                          <span>Last Month</span>
                          <span className="font-medium text-slate-800 tabular-nums">
                            {formatInr(lastMonth?.headline_inr)}
                          </span>
                        </div>
                        <div className="flex items-center justify-between text-slate-600">
                          <span>All Time</span>
                          <span className="font-medium text-slate-800 tabular-nums">
                            {formatInr(allTime?.headline_inr)}
                          </span>
                        </div>
                      </div>

                      {/* 2. Gemini Stage Breakdown */}
                      {curMonth?.gemini_breakdown && (
                        <div className="space-y-1.5">
                          <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                            Gemini Stage Breakdown (MTD)
                          </div>
                          <div className="space-y-1 text-slate-600">
                            <div className="flex justify-between items-center pl-1 text-[11.5px]">
                              <span>Ingestion Vision</span>
                              <span className="tabular-nums font-mono text-[11px]">
                                {formatInr(curMonth.gemini_breakdown.ingestion?.vision)}
                              </span>
                            </div>
                            <div className="flex justify-between items-center pl-1 text-[11.5px]">
                              <span>Ingestion Tagging</span>
                              <span className="tabular-nums font-mono text-[11px]">
                                {formatInr(curMonth.gemini_breakdown.ingestion?.tagging)}
                              </span>
                            </div>
                            <div className="flex justify-between items-center pl-1 text-[11.5px]">
                              <span>Search Embeddings</span>
                              <span className="tabular-nums font-mono text-[11px]">
                                {formatInr(curMonth.gemini_breakdown.ingestion?.embedding)}
                              </span>
                            </div>
                            <div className="flex justify-between items-center pl-1 text-[11.5px]">
                              <span>Quiz Generation (Curator/Intent)</span>
                              <span className="tabular-nums font-mono text-[11px]">
                                {formatInr(curMonth.gemini_breakdown.quiz?.total)}
                              </span>
                            </div>
                          </div>
                        </div>
                      )}
                    </>
                  );
                })()}

                {/* 3. Per-Deck Costs */}
                {recentDecks.length > 0 && (
                  <div className="space-y-1.5">
                    <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                      Recent Ingested Decks
                    </div>
                    <div className="divide-y divide-slate-100 rounded-lg border border-slate-100 overflow-hidden bg-white">
                      {recentDecks.slice(0, 3).map((deck) => (
                        <div key={deck.document_id} className="p-2 flex items-center justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate font-medium text-slate-800 text-[11.5px]">
                              {deck.filename || deck.title || 'Deck'}
                            </p>
                            <p className="text-[10.5px] text-slate-400">
                              {deck.question_count} Qs • {formatInr(deck.inr_per_question ?? deck.cost_per_question_inr)}/Q
                            </p>
                          </div>
                          <span className="font-semibold text-slate-800 tabular-nums text-[11.5px] shrink-0">
                            {formatInr(deck.total_inr)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* 4. Ledger Refresh Status & FX */}
                <div className="pt-2 border-t border-slate-100 text-[11px] text-slate-400 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      <span>Last Refresh:</span>
                    </span>
                    <span className="text-slate-600">
                      {refreshStatus?.last_refresh_at
                        ? new Date(refreshStatus.last_refresh_at).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                        : 'Today'}
                    </span>
                  </div>
                  {refreshStatus?.fx_rate && (
                    <div className="flex items-center justify-between">
                      <span>FX Rate:</span>
                      <span className="text-slate-600 font-mono">
                        $1 = ₹{refreshStatus.fx_rate.toFixed(2)}
                      </span>
                    </div>
                  )}
                  {refreshStatus?.active_rate_card_version && (
                    <div className="flex items-center justify-between">
                      <span>Rate Card:</span>
                      <span className="text-slate-600">v{refreshStatus.active_rate_card_version}</span>
                    </div>
                  )}
                </div>

                {/* 5. Link to full tables page */}
                <div className="pt-1 text-center">
                  <Link
                    href="/costs"
                    className="inline-flex items-center gap-1 text-[12px] font-semibold text-blue-600 hover:text-blue-700 transition-colors"
                  >
                    <span>View full cost tables & history</span>
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
