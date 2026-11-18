'use client';

import { X } from 'lucide-react';
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

function describeAlreadyInLibrary(already: UploadDuplicateReport['already_in_library']): string {
  if (already.length === 0) return '';
  const counts = new Map<string, number>();
  for (const a of already) {
    const key = a.already_in_deck_title || 'another deck';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const parts = Array.from(counts.entries()).map(([deck, n]) => (n > 1 ? `${deck} (${n})` : deck));
  return `repeated from: ${parts.join(', ')}`;
}

function SectionBody({ report }: { report: UploadDuplicateReport }) {
  const { reconciliation, already_in_library, repeated_in_this_deck, possible_duplicates_kept } = report;
  const alreadyDesc = describeAlreadyInLibrary(already_in_library);

  return (
    <div className="space-y-1.5 text-[13.5px] text-slate-700 leading-relaxed">
      <p>
        <span className="font-semibold text-slate-900">{reconciliation.extracted}</span> questions found,{' '}
        <span className="font-semibold text-slate-900">{reconciliation.saved}</span> saved.
      </p>
      {already_in_library.length > 0 && (
        <p>
          <span className="font-semibold text-amber-700">{already_in_library.length}</span> already in your
          library{alreadyDesc ? ` (${alreadyDesc})` : ''}.
        </p>
      )}
      {repeated_in_this_deck > 0 && (
        <p>
          <span className="font-semibold text-slate-700">{repeated_in_this_deck}</span> repeated inside this
          PPT.
        </p>
      )}
      {possible_duplicates_kept.length > 0 && (
        <p>
          <span className="font-semibold text-blue-700">{possible_duplicates_kept.length}</span> possible
          duplicate{possible_duplicates_kept.length > 1 ? 's' : ''} kept for you to review.
        </p>
      )}
      {report.provenance_warnings.length > 0 && (
        <p className="text-rose-600">
          {report.provenance_warnings.length} repeat record{report.provenance_warnings.length > 1 ? 's' : ''}{' '}
          could not be saved — the questions themselves are fine, just not flagged as repeats.
        </p>
      )}
      {already_in_library.length === 0 && repeated_in_this_deck === 0 && possible_duplicates_kept.length === 0 && (
        <p className="text-slate-500">No duplicates found — every question was new.</p>
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
        className="relative w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <h3 className="text-[15px] font-bold text-slate-900">Upload report</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors cursor-pointer"
            title="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="divide-y divide-slate-100">
          {sections.map((s, i) => (
            <div key={i} className="px-5 py-4">
              <p className="mb-1.5 text-[13.5px] font-semibold text-slate-900 truncate" title={s.deckTitle}>
                From &ldquo;{s.deckTitle}&rdquo;
              </p>
              <SectionBody report={s.report} />
            </div>
          ))}
        </div>

        <div className="border-t border-slate-100 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
          >
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
