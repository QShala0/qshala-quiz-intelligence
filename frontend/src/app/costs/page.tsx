'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  ArrowLeft,
  Receipt,
  RefreshCw,
  Sparkles,
  Layers,
} from 'lucide-react';
import { api } from '../../lib/api';
import {
  CostsSummaryResponse,
  DocumentCostItem,
  CostRefreshStatusResponse,
} from '../../lib/types';

function formatInr(val?: number | null): string {
  const num = Number(val || 0);
  return `₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function CostsPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [summary, setSummary] = useState<CostsSummaryResponse | null>(null);
  const [documents, setDocuments] = useState<DocumentCostItem[]>([]);
  const [refreshStatus, setRefreshStatus] = useState<CostRefreshStatusResponse | null>(null);

  const [refreshKey, setRefreshKey] = useState(0);

  const handleManualRefresh = () => {
    setLoading(true);
    setRefreshKey((k) => k + 1);
  };

  useEffect(() => {
    let active = true;
    Promise.all([
      api.getCostsSummary(),
      api.getCostsDocuments(50),
      api.getCostsRefreshStatus(),
    ])
      .then(([sum, docs, refStatus]) => {
        if (active) {
          setSummary(sum);
          setDocuments(((docs?.items ?? docs?.documents ?? []) as DocumentCostItem[]));
          setRefreshStatus(refStatus);
          setError(null);
        }
      })
      .catch((err) => {
        if (active) {
          console.error('Failed to load cost details:', err);
          setError(err instanceof Error ? err.message : 'Unable to load operating cost ledger');
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

  const cur = summary?.current_month ?? summary?.periods?.current_month;
  const last = summary?.last_month ?? summary?.periods?.last_month;
  const allTime = summary?.all_time ?? summary?.periods?.all_time;

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-7xl mx-auto space-y-8">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-4 border-b border-slate-100">
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <Link
              href="/"
              className="inline-flex items-center gap-1 text-[12.5px] font-medium text-slate-500 hover:text-slate-800 transition-colors"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>Back to Dashboard</span>
            </Link>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-white">
              <Receipt className="h-4 w-4" />
            </div>
            <div>
              <h1 className="text-[26px] sm:text-[30px] font-bold tracking-tight text-slate-900 leading-tight">
                Operating Cost Ledger
              </h1>
              <p className="text-[13px] text-slate-500">
                Transparent attribution for Gemini AI, Cloud Run, Neon Postgres, and Cloudflare R2
              </p>
            </div>
          </div>
        </div>

        <button
          type="button"
          onClick={handleManualRefresh}
          disabled={loading}
          className="inline-flex items-center gap-2 self-start sm:self-auto rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-700 shadow-xs hover:bg-slate-50 transition-colors cursor-pointer disabled:opacity-50"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Error state */}
      {error && !loading && (
        <div className="rounded-xl border border-amber-200 bg-amber-50/90 p-4 text-[13.5px] text-amber-900">
          <p className="font-semibold">Unable to load costs ledger data:</p>
          <p className="mt-0.5 text-amber-800">{error}</p>
        </div>
      )}

      {/* 3 Executive Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50">
          <div className="text-[12px] font-semibold text-slate-500 uppercase tracking-wider">
            This Month (MTD)
          </div>
          <div className="text-[28px] font-bold tracking-tight text-slate-900 mt-2">
            {loading ? '-' : formatInr(cur?.headline_inr)}
          </div>
          <div className="mt-1 text-[12px] text-slate-400">
            {summary?.period ? `Period: ${summary.period}` : 'Current billing cycle'}
          </div>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50">
          <div className="text-[12px] font-semibold text-slate-500 uppercase tracking-wider">
            Last Month
          </div>
          <div className="text-[28px] font-bold tracking-tight text-slate-900 mt-2">
            {loading ? '-' : formatInr(last?.headline_inr)}
          </div>
          <div className="mt-1 text-[12px] text-slate-400">
            Preceding month ledger total
          </div>
        </div>

        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50">
          <div className="text-[12px] font-semibold text-slate-500 uppercase tracking-wider">
            All Time
          </div>
          <div className="text-[28px] font-bold tracking-tight text-slate-900 mt-2">
            {loading ? '-' : formatInr(allTime?.headline_inr)}
          </div>
          <div className="mt-1 text-[12px] text-slate-400">
            Cumulative total across all periods
          </div>
        </div>
      </div>

      {/* Grid: Gemini Stage Breakdown & Operational Details */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Gemini Stage Breakdown */}
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50 space-y-4">
          <div className="border-b border-slate-100 pb-3 flex items-center justify-between">
            <div>
              <h3 className="text-[15px] font-bold text-slate-900">Gemini AI Breakdown (MTD)</h3>
              <p className="text-[12px] text-slate-400">Tokens consumed by stage and operation</p>
            </div>
            <span className="font-bold text-[15px] text-slate-900">
              {formatInr(cur?.gemini_breakdown?.total)}
            </span>
          </div>

          <div className="space-y-3 text-[13px]">
            <div className="space-y-1.5">
              <div className="font-semibold text-slate-700 text-[12px] flex items-center gap-1.5">
                <Layers className="h-3.5 w-3.5 text-slate-500" />
                <span>Ingestion Pipeline</span>
              </div>
              <div className="pl-5 space-y-1 text-slate-600">
                <div className="flex justify-between">
                  <span>Vision (Slides OCR & Description)</span>
                  <span className="font-mono">{formatInr(cur?.gemini_breakdown?.ingestion?.vision)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Tagging (Questions & Cognitive Level)</span>
                  <span className="font-mono">{formatInr(cur?.gemini_breakdown?.ingestion?.tagging)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Embeddings (Dense Vector Index)</span>
                  <span className="font-mono">{formatInr(cur?.gemini_breakdown?.ingestion?.embedding)}</span>
                </div>
              </div>
            </div>

            <div className="space-y-1.5 pt-2 border-t border-slate-100">
              <div className="font-semibold text-slate-700 text-[12px] flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5 text-slate-500" />
                <span>Quiz Generation</span>
              </div>
              <div className="pl-5 space-y-1 text-slate-600">
                <div className="flex justify-between">
                  <span>Intent Engine (Slot Filling)</span>
                  <span className="font-mono">{formatInr(cur?.gemini_breakdown?.quiz?.intent)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Curator LLM (Candidate Selection)</span>
                  <span className="font-mono">{formatInr(cur?.gemini_breakdown?.quiz?.curator)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Interpreter LLM (Follow-up Chat)</span>
                  <span className="font-mono">{formatInr(cur?.gemini_breakdown?.quiz?.interpreter)}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Rate Card & Daily Refresh Status */}
        <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-sm shadow-slate-100/50 space-y-4">
          <div className="border-b border-slate-100 pb-3">
            <h3 className="text-[15px] font-bold text-slate-900">System Rate Card & FX Status</h3>
            <p className="text-[12px] text-slate-400">Active rates and automated daily refresh health</p>
          </div>

          <div className="space-y-3 text-[13px] text-slate-600">
            <div className="flex justify-between items-center py-1 border-b border-slate-50">
              <span className="font-medium">Active Rate Card</span>
              <span className="font-semibold text-slate-800">
                Version {refreshStatus?.active_rate_card_version ?? 1}
              </span>
            </div>

            <div className="flex justify-between items-center py-1 border-b border-slate-50">
              <span className="font-medium">FX Exchange Rate</span>
              <span className="font-mono font-medium text-slate-800">
                1 USD = ₹{refreshStatus?.fx_rate ? refreshStatus.fx_rate.toFixed(2) : '95.00'}
              </span>
            </div>

            <div className="flex justify-between items-center py-1 border-b border-slate-50">
              <span className="font-medium">FX Source & Status</span>
              <span className="text-slate-600">
                {refreshStatus?.fx_source || 'api.exchangerate-api.com'}{' '}
                {refreshStatus?.fx_stale && (
                  <span className="text-amber-600 font-semibold">(stale fallback)</span>
                )}
              </span>
            </div>

            <div className="flex justify-between items-center py-1 border-b border-slate-50">
              <span className="font-medium">Last Daily Refresh Run</span>
              <span className="text-slate-600">
                {refreshStatus?.last_refresh_at
                  ? new Date(refreshStatus.last_refresh_at).toLocaleString()
                  : 'Automated daily catchup active'}
              </span>
            </div>

            <div className="flex justify-between items-center py-1">
              <span className="font-medium">Cron Secret Protection</span>
              <span className="text-emerald-700 font-medium">Guarded by X-Cron-Secret</span>
            </div>
          </div>
        </div>
      </div>

      {/* Per-Deck Ingestion Costs Table */}
      <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
        <div className="p-5 border-b border-slate-100 flex items-center justify-between">
          <div>
            <h3 className="text-[16px] font-bold text-slate-900">Per-Deck Ingestion Costs</h3>
            <p className="text-[12.5px] text-slate-500">
              Granular attribution per uploaded slide deck across Vision, Tagging, Embeddings, Job Compute, and R2
            </p>
          </div>
          <span className="text-[12px] text-slate-400 font-medium">
            {documents.length} decks recorded
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-slate-50/80 border-b border-slate-100 text-[11.5px] font-semibold uppercase tracking-wider text-slate-500">
              <tr>
                <th className="py-3 px-4">Deck Filename</th>
                <th className="py-3 px-3 text-right">Questions</th>
                <th className="py-3 px-3 text-right">Vision</th>
                <th className="py-3 px-3 text-right">Tagging</th>
                <th className="py-3 px-3 text-right">Job Compute</th>
                <th className="py-3 px-3 text-right">R2 Storage</th>
                <th className="py-3 px-4 text-right">Total (₹)</th>
                <th className="py-3 px-4 text-right">₹ / Question</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {documents.length > 0 ? (
                documents.map((d) => (
                  <tr key={d.document_id} className="hover:bg-slate-50/60 transition-colors">
                    <td className="py-3 px-4 font-medium text-slate-900 max-w-[240px] truncate">
                      {d.filename || d.title || 'Deck'}
                      {d.has_estimated && (
                        <span className="ml-1.5 text-[10.5px] text-slate-400 font-normal">~est.</span>
                      )}
                    </td>
                    <td className="py-3 px-3 text-right text-slate-500">{d.question_count}</td>
                    <td className="py-3 px-3 text-right font-mono text-[12px]">{formatInr(d.vision_inr)}</td>
                    <td className="py-3 px-3 text-right font-mono text-[12px]">{formatInr(d.tagging_inr)}</td>
                    <td className="py-3 px-3 text-right font-mono text-[12px]">{formatInr(d.job_compute_inr)}</td>
                    <td className="py-3 px-3 text-right font-mono text-[12px]">{formatInr(d.r2_inr)}</td>
                    <td className="py-3 px-4 text-right font-bold text-slate-900 font-mono text-[12.5px]">
                      {formatInr(d.total_inr)}
                    </td>
                    <td className="py-3 px-4 text-right font-medium text-blue-600 font-mono text-[12px]">
                      {formatInr(d.inr_per_question ?? d.cost_per_question_inr)}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-slate-400">
                    No ingested decks found. Upload slide decks to begin tracking per-deck costs.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
