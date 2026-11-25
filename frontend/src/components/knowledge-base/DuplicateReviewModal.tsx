import { useState } from 'react';
import { AlertTriangle, X, CheckCircle, Copy, Check } from 'lucide-react';
import { HistoricalQuestion } from '../../lib/types';

// The side-by-side duplicate-candidate review popup (QShala priority plan P3-1). Extracted
// unchanged from knowledge-base/page.tsx's "Duplicate Review & Resolution Modal" section. Takes
// the candidate question as a prop and calls back up for both actions -- the actual resolve
// logic (including what happens to selectedDuplicateForReview on success vs error) stays owned
// by the page/hook, not this component.
export interface DuplicateReviewModalProps {
  question: HistoricalQuestion;
  resolvingId: string | null;
  onClose: () => void;
  onResolve: (id: string, action: 'DISMISS_UNIQUE' | 'CONFIRM_DUPLICATE', keep?: 'EXISTING' | 'CANDIDATE') => void;
}

export default function DuplicateReviewModal({
  question: selectedDuplicateForReview,
  resolvingId,
  onClose,
  onResolve,
}: DuplicateReviewModalProps) {
  const [keepTarget, setKeepTarget] = useState<'EXISTING' | 'CANDIDATE'>('EXISTING');

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-xs p-4">
      <div className="w-full max-w-3xl rounded-xl bg-white shadow-xl border border-slate-200 overflow-hidden flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4 bg-amber-50/50">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-600" />
            <h3 className="text-[16px] font-bold text-slate-900">
              Duplicate Candidate Review ({Math.round((selectedDuplicateForReview.duplicate_similarity || 0.92) * 100)}% Match)
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 p-1 rounded-md"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="p-6 space-y-4 overflow-y-auto">
          <p className="text-[13px] text-slate-600 leading-relaxed">
            This newly ingested question has a cosine similarity score of{' '}
            <span className="font-bold text-amber-700">
              {(selectedDuplicateForReview.duplicate_similarity || 0.92).toFixed(3)}
            </span>{' '}
            against an existing question in your Knowledge Base.
          </p>

          <div className="rounded-lg bg-slate-50 border border-slate-200/80 px-3.5 py-2 text-[12px] text-slate-600 flex items-center justify-between">
            <span>Choose which version to keep as primary in the Knowledge Base:</span>
            <span className="font-semibold text-slate-800">
              Keeping: {keepTarget === 'CANDIDATE' ? 'Newly Ingested Candidate' : 'Existing Knowledge Base Match'}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Candidate (New) Question */}
            <div
              onClick={() => setKeepTarget('CANDIDATE')}
              className={`rounded-xl border-2 p-4 space-y-3 cursor-pointer transition-all relative ${
                keepTarget === 'CANDIDATE'
                  ? 'border-amber-500 bg-amber-50/30 ring-2 ring-amber-500/20 shadow-sm'
                  : 'border-slate-200 bg-white hover:border-amber-300'
              }`}
            >
              <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
                <div className="flex items-center gap-2">
                  <div
                    className={`h-4 w-4 rounded-full border flex items-center justify-center transition-colors ${
                      keepTarget === 'CANDIDATE'
                        ? 'border-amber-600 bg-amber-600 text-white'
                        : 'border-slate-300 bg-white'
                    }`}
                  >
                    {keepTarget === 'CANDIDATE' && <Check className="h-2.5 w-2.5 stroke-[3]" />}
                  </div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-amber-800">
                    Newly Ingested Candidate
                  </span>
                </div>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                  keepTarget === 'CANDIDATE'
                    ? 'bg-amber-600 text-white'
                    : 'bg-slate-100 text-slate-500'
                }`}>
                  {keepTarget === 'CANDIDATE' ? 'Keep This' : 'Click to Keep'}
                </span>
              </div>

              <div>
                <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Question</span>
                <p className="text-[13.5px] font-medium text-slate-900 mt-0.5">
                  {selectedDuplicateForReview.question_text}
                </p>
              </div>

              <div>
                <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Answer</span>
                <p className="text-[13.5px] font-bold text-slate-800 mt-0.5">
                  {selectedDuplicateForReview.answer}
                </p>
              </div>

              <div className="text-[11px] text-slate-500 pt-1 border-t border-slate-100 flex items-center justify-between">
                <span>Source: {selectedDuplicateForReview.document_title || 'Current Deck'}</span>
              </div>

              {selectedDuplicateForReview.audio_transcript && (
                <div className="rounded bg-white/80 p-2 border border-amber-100 text-[11.5px]">
                  <span className="font-bold text-slate-700">Audio transcript:</span>{' '}
                  {selectedDuplicateForReview.audio_transcript}
                </div>
              )}

              {selectedDuplicateForReview.video_transcript && (
                <div className="rounded bg-white/80 p-2 border border-amber-100 text-[11.5px]">
                  <span className="font-bold text-slate-700">Video transcript:</span>{' '}
                  {selectedDuplicateForReview.video_transcript}
                </div>
              )}
            </div>

            {/* Matched Original Question */}
            <div
              onClick={() => setKeepTarget('EXISTING')}
              className={`rounded-xl border-2 p-4 space-y-3 cursor-pointer transition-all relative ${
                keepTarget === 'EXISTING'
                  ? 'border-blue-500 bg-blue-50/30 ring-2 ring-blue-500/20 shadow-sm'
                  : 'border-slate-200 bg-white hover:border-blue-300'
              }`}
            >
              <div className="flex items-center justify-between border-b border-slate-100 pb-2.5">
                <div className="flex items-center gap-2">
                  <div
                    className={`h-4 w-4 rounded-full border flex items-center justify-center transition-colors ${
                      keepTarget === 'EXISTING'
                        ? 'border-blue-600 bg-blue-600 text-white'
                        : 'border-slate-300 bg-white'
                    }`}
                  >
                    {keepTarget === 'EXISTING' && <Check className="h-2.5 w-2.5 stroke-[3]" />}
                  </div>
                  <span className="text-[11px] font-bold uppercase tracking-wider text-blue-900">
                    Existing Knowledge Base Match
                  </span>
                </div>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                  keepTarget === 'EXISTING'
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-100 text-slate-500'
                }`}>
                  {keepTarget === 'EXISTING' ? 'Keep This (Default)' : 'Click to Keep'}
                </span>
              </div>

              <div>
                <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Question</span>
                <p className="text-[13.5px] font-medium text-slate-900 mt-0.5">
                  {selectedDuplicateForReview.duplicate_of_text ? (
                    selectedDuplicateForReview.duplicate_of_text
                  ) : (
                    <span className="italic text-slate-400 font-normal">
                      [Visual or media-only slide with no question text extracted]
                    </span>
                  )}
                </p>
              </div>

              <div>
                <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Answer</span>
                <p className="text-[13.5px] font-bold text-slate-800 mt-0.5">
                  {selectedDuplicateForReview.duplicate_of_answer ? (
                    selectedDuplicateForReview.duplicate_of_answer
                  ) : (
                    <span className="italic text-slate-400 font-normal">
                      [No answer recorded]
                    </span>
                  )}
                </p>
              </div>

              <div className="text-[11px] text-slate-500 pt-1 border-t border-slate-100 flex items-center justify-between font-mono">
                <span>Match ID: {selectedDuplicateForReview.duplicate_of_id?.slice(0, 8) || 'Match'}</span>
                <span>Historical Tournament Match</span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-slate-100 bg-slate-50 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-slate-300 px-4 py-2 text-[13px] font-medium text-slate-700 hover:bg-white transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={resolvingId === selectedDuplicateForReview.id}
            onClick={() => onResolve(selectedDuplicateForReview.id, 'DISMISS_UNIQUE')}
            className="rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2 text-[13px] font-semibold text-emerald-800 hover:bg-emerald-100 transition-colors cursor-pointer flex items-center gap-1.5"
          >
            <CheckCircle className="h-4 w-4 text-emerald-600" />
            <span>Dismiss as Unique</span>
          </button>
          <button
            type="button"
            disabled={resolvingId === selectedDuplicateForReview.id}
            onClick={() => onResolve(selectedDuplicateForReview.id, 'CONFIRM_DUPLICATE', keepTarget)}
            className="rounded-lg bg-blue-600 hover:bg-blue-700 px-4 py-2 text-[13px] font-semibold text-white transition-colors cursor-pointer flex items-center gap-1.5 shadow-sm"
          >
            <Copy className="h-4 w-4" />
            <span>
              Confirm Duplicate (Keep {keepTarget === 'CANDIDATE' ? 'Candidate' : 'Existing'})
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
