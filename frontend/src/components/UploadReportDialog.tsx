'use client';

import { useMemo } from 'react';
import {
  X,
  CheckCircle2,
  Copy,
  AlertCircle,
  HelpCircle,
  AlertTriangle,
  FileText,
} from 'lucide-react';
import { UploadDuplicateReport } from '../lib/types';

export interface UploadReportSection {
  deckTitle: string;
  report: UploadDuplicateReport;
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  sections: UploadReportSection[];
}

function SectionBody({ deckTitle, report }: { deckTitle: string; report: UploadDuplicateReport }) {
  const { reconciliation, already_in_library, repeated_in_this_deck, possible_duplicates_kept } = report;

  const repeatCounts = useMemo(() => {
    if (!already_in_library || already_in_library.length === 0) return [];
    const counts = new Map<string, number>();
    for (const a of already_in_library) {
      const raw = a.already_in_deck_title || 'Another presentation';
      const clean = raw.replace(/\.(?:pptx|ppt)$/i, '').trim() || raw;
      counts.set(clean, (counts.get(clean) || 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [already_in_library]);

  const cleanDeckName = deckTitle.replace(/\.(?:pptx|ppt)$/i, '').trim() || deckTitle;

  return (
    <div className="space-y-3.5">
      {/* Deck Source Header */}
      <div className="flex items-center gap-2.5 rounded-xl bg-slate-50/80 border border-slate-200/70 px-3 py-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600 border border-blue-100">
          <FileText className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold text-slate-900 truncate" title={deckTitle}>
            {cleanDeckName}
          </p>
          {deckTitle.match(/\.(?:pptx|ppt)$/i) && (
            <p className="text-[11px] text-slate-400 font-mono truncate">{deckTitle}</p>
          )}
        </div>
      </div>

      {/* Metrics Summary Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <div className="rounded-xl bg-slate-50 border border-slate-200/70 p-2.5 text-center">
          <span className="block text-[10.5px] font-bold uppercase tracking-wider text-slate-500">
            Found
          </span>
          <span className="text-[18px] font-bold text-slate-900">{reconciliation.extracted}</span>
          <span className="block text-[10.5px] text-slate-400">questions</span>
        </div>

        <div className="rounded-xl bg-emerald-50/70 border border-emerald-200/70 p-2.5 text-center">
          <span className="block text-[10.5px] font-bold uppercase tracking-wider text-emerald-700">
            Saved
          </span>
          <span className="text-[18px] font-bold text-emerald-700">{reconciliation.saved}</span>
          <span className="block text-[10.5px] text-emerald-600/80">to library</span>
        </div>

        {already_in_library.length > 0 ? (
          <div className="col-span-2 sm:col-span-1 rounded-xl bg-amber-50/70 border border-amber-200/70 p-2.5 text-center">
            <span className="block text-[10.5px] font-bold uppercase tracking-wider text-amber-700">
              Repeats
            </span>
            <span className="text-[18px] font-bold text-amber-700">{already_in_library.length}</span>
            <span className="block text-[10.5px] text-amber-600/80">skipped</span>
          </div>
        ) : (
          <div className="col-span-2 sm:col-span-1 rounded-xl bg-slate-50 border border-slate-200/70 p-2.5 text-center">
            <span className="block text-[10.5px] font-bold uppercase tracking-wider text-slate-500">
              Repeats
            </span>
            <span className="text-[18px] font-bold text-slate-400">0</span>
            <span className="block text-[10.5px] text-slate-400">all new</span>
          </div>
        )}
      </div>

      {/* Repeated from existing decks breakdown */}
      {already_in_library.length > 0 && (
        <div className="rounded-xl border border-amber-200/80 bg-amber-50/40 p-3 space-y-2">
          <div className="flex items-center gap-1.5 text-amber-900 font-semibold text-[12.5px]">
            <Copy className="h-3.5 w-3.5 text-amber-600 shrink-0" />
            <span>{already_in_library.length} already in library</span>
            <span className="text-amber-700/80 font-normal text-[11px]">(skipped)</span>
          </div>

          <div className="space-y-1.5">
            <p className="text-[11px] font-medium text-amber-800/90">
              Repeated from existing presentations:
            </p>
            <div className="flex flex-wrap gap-1.5 max-h-40 overflow-y-auto pr-0.5">
              {repeatCounts.map(([name, count]) => (
                <span
                  key={name}
                  className="inline-flex items-center gap-1 rounded-md bg-white border border-amber-200/80 px-2 py-1 text-[11.5px] font-medium text-amber-950 shadow-2xs"
                  title={`${name}: ${count} repeat${count > 1 ? 's' : ''}`}
                >
                  <span className="truncate max-w-[210px]">{name}</span>
                  {count > 1 && (
                    <span className="rounded bg-amber-100 text-amber-800 font-bold px-1.5 text-[10px]">
                      ×{count}
                    </span>
                  )}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Repeated inside this presentation */}
      {repeated_in_this_deck > 0 && (
        <div className="flex items-center gap-2 rounded-xl bg-slate-50 border border-slate-200/80 px-3 py-2 text-[12.5px] text-slate-700">
          <AlertCircle className="h-3.5 w-3.5 text-slate-500 shrink-0" />
          <span>
            <strong className="font-semibold text-slate-900">{repeated_in_this_deck}</strong> duplicate question{repeated_in_this_deck > 1 ? 's' : ''} repeated within this presentation.
          </span>
        </div>
      )}

      {/* Possible duplicates kept for review */}
      {possible_duplicates_kept.length > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-blue-200/80 bg-blue-50/60 px-3 py-2.5 text-[12.5px] text-blue-900">
          <HelpCircle className="h-4 w-4 text-blue-600 shrink-0" />
          <span>
            <strong className="font-semibold text-blue-950">{possible_duplicates_kept.length}</strong> possible duplicate{possible_duplicates_kept.length > 1 ? 's' : ''} kept for you to review.
          </span>
        </div>
      )}

      {/* All New Banner */}
      {already_in_library.length === 0 && repeated_in_this_deck === 0 && possible_duplicates_kept.length === 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 text-[12.5px] text-emerald-800">
          <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
          <span>No duplicates found — every question was brand new and saved!</span>
        </div>
      )}

      {/* Provenance warnings */}
      {report.provenance_warnings.length > 0 && (
        <div className="flex items-start gap-2 rounded-xl bg-rose-50 border border-rose-200/80 p-2.5 text-[12px] text-rose-800">
          <AlertTriangle className="h-3.5 w-3.5 text-rose-600 shrink-0 mt-0.5" />
          <span>
            {report.provenance_warnings.length} repeat record{report.provenance_warnings.length > 1 ? 's' : ''} could not be saved — the questions themselves are safely stored in your library.
          </span>
        </div>
      )}
    </div>
  );
}

export default function UploadReportDialog({ isOpen, onClose, sections }: Props) {
  if (!isOpen || sections.length === 0) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm animate-in fade-in"
      onClick={onClose}
    >
      <div
        className="relative flex flex-col w-full max-w-lg max-h-[90vh] rounded-2xl border border-slate-200/90 bg-white shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-3.5 bg-slate-50/60">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-50 text-blue-600 border border-blue-100">
              <CheckCircle2 className="h-4 w-4" />
            </div>
            <h3 className="text-[14.5px] font-bold text-slate-900">Upload Report</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors cursor-pointer"
            title="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Modal Sections */}
        <div className="flex-1 overflow-y-auto divide-y divide-slate-100">
          {sections.map((s, i) => (
            <div key={i} className="p-5">
              <SectionBody deckTitle={s.deckTitle} report={s.report} />
            </div>
          ))}
        </div>

        {/* Modal Footer */}
        <div className="shrink-0 border-t border-slate-100 px-5 py-3.5 bg-slate-50/40">
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-xl bg-slate-900 px-4 py-2.5 text-[13px] font-semibold text-white hover:bg-slate-800 transition-colors shadow-xs cursor-pointer"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
