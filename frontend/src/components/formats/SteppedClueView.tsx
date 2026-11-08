'use client';

import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { SteppedCluePayload, ladderText } from '../../lib/formatPayloads';

interface Props {
  payload: SteppedCluePayload;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage: (url: string) => void;
}

// Stepped clue: one slide per clue, each worth fewer points than the one before. Reveal clue by clue.
export default function SteppedClueView({ payload, getImageSrc, handleImgError, setPreviewImage }: Props) {
  const clues = payload.clues;
  const [shown, setShown] = useState(1);
  const [showAnswer, setShowAnswer] = useState(false);
  return (
    <div className="space-y-2 mt-1" data-testid="stepped-view">
      {!payload.scoring_ladder_ok && (
        <div className="flex items-center gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-[11.5px] font-semibold text-amber-800">
          <AlertTriangle className="h-3.5 w-3.5" />
          The points or numbering on these slides differ from the round&apos;s ladder. Check the deck.
        </div>
      )}
      {payload.points_missing && (
        <div className="text-[11px] text-slate-500">Clue slides carry no points; the round&apos;s ladder is used.</div>
      )}
      <ol className="space-y-1.5">
        {clues.map((c, i) => {
          const open = i < shown;
          return (
            <li key={c.step} className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 text-[12.5px] ${open ? 'border-amber-200 bg-amber-50/70' : 'border-dashed border-slate-300 bg-slate-50'}`}>
              <span className="shrink-0 rounded bg-amber-200 px-2 py-0.5 font-mono text-[11px] font-bold text-amber-900">
                Clue {c.step} · {ladderText(c.correct, c.wrong) || '—'}
              </span>
              <div className="min-w-0 flex-1 leading-snug text-slate-800">
                {open ? c.text || <span className="text-slate-400">(no text)</span> : <span className="text-slate-400">Hidden until revealed</span>}
              </div>
              {open && c.images.slice(0, 2).map((img) => (
                <button key={img} type="button" onClick={() => setPreviewImage(img)} className="shrink-0 overflow-hidden rounded-md border border-amber-300 bg-white cursor-pointer">
                  <img src={getImageSrc(img)} onError={() => handleImgError(img)} alt={`Clue ${c.step}`} className="h-10 w-14 object-contain" />
                </button>
              ))}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <button
          type="button"
          onClick={() => setShown((n) => Math.min(clues.length, n + 1))}
          disabled={shown >= clues.length}
          className="rounded bg-amber-600 px-2.5 py-1 font-semibold text-white hover:bg-amber-700 disabled:opacity-40 cursor-pointer"
        >
          Reveal clue {Math.min(shown + 1, clues.length)}
        </button>
        <button type="button" onClick={() => { setShown(1); setShowAnswer(false); }} className="rounded px-2 py-1 font-semibold text-slate-500 hover:text-slate-800 cursor-pointer">
          Reset
        </button>
        <button type="button" onClick={() => setShowAnswer((v) => !v)} className="rounded border border-slate-200 bg-white px-2 py-1 font-semibold text-slate-700 hover:bg-slate-50 cursor-pointer">
          {showAnswer ? 'Hide answer' : 'Show answer'}
        </button>
        {showAnswer && <span><span className="font-bold text-amber-800">Answer:</span> <span className="font-semibold text-slate-900">{payload.answer}</span></span>}
      </div>
    </div>
  );
}
