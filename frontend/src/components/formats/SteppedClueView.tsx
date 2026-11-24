'use client';

import { AlertTriangle } from 'lucide-react';
import { SteppedCluePayload, ladderText } from '../../lib/formatPayloads';

interface Props {
  payload: SteppedCluePayload;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage: (url: string) => void;
}

// 3 clue: one slide per clue, each worth fewer points than the one before. Every clue and the answer are shown at
// once (no reveal buttons): the card is for reading and curating a question, not for playing it.
export default function SteppedClueView({ payload, getImageSrc, handleImgError, setPreviewImage }: Props) {
  const clues = payload.clues;
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
        {clues.map((c) => {
          return (
            <li key={c.step} className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2.5 text-[13px]">
              {/* The points label sits above the clue text, so the text can use the whole width beside the picture. */}
              <div className="min-w-0 flex-1">
                <span className="mb-1 inline-block rounded bg-amber-200 px-2 py-0.5 font-mono text-[11px] font-bold text-amber-900">
                  Clue {c.step} · {ladderText(c.correct, c.wrong) || '—'}
                </span>
                <div className="leading-snug text-slate-800">
                  {c.text || <span className="text-slate-400">(no text)</span>}
                </div>
              </div>
              {c.images.slice(0, 2).map((img) => (
                <button key={img} type="button" onClick={() => setPreviewImage(img)} className="shrink-0 overflow-hidden rounded-md border border-amber-300 bg-white cursor-pointer">
                  <img loading="lazy" decoding="async" src={getImageSrc(img)} onError={() => handleImgError(img)} alt={`Clue ${c.step}`} className="h-14 w-20 object-contain" />
                </button>
              ))}
            </li>
          );
        })}
      </ol>
      <div className="text-[12.5px]" data-testid="stepped-answer">
        <span className="font-bold text-amber-800">Answer:</span> <span className="font-semibold text-slate-900">{payload.answer}</span>
      </div>
    </div>
  );
}
