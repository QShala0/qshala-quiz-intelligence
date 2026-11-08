'use client';

import { useState } from 'react';
import { ImageRevealPayload, ladderText } from '../../lib/formatPayloads';

interface Props {
  payload: ImageRevealPayload;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage?: (url: string) => void;
}

// Where each cover sits on the 2x2 picture.
const CELL: Record<string, string> = {
  'top-left': 'col-start-1 row-start-1',
  'top-right': 'col-start-2 row-start-1',
  'bottom-left': 'col-start-1 row-start-2',
  'bottom-right': 'col-start-2 row-start-2',
};

// Image Reveal: the full picture sits under four numbered covers. Stepping reveals them in the order the deck's
// animation takes them off; the points ladder (+20 / -15 ... +5 / 0) shows beside the stepper.
export default function ImageRevealView({ payload, getImageSrc, handleImgError, setPreviewImage }: Props) {
  const steps = payload.reveal_steps;
  const [shown, setShown] = useState(0);        // how many covers have been lifted
  const full = payload.full_image;

  return (
    <div className="space-y-2 mt-1" data-testid="reveal-view">
      {payload.prompt && <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">{payload.prompt}</div>}
      <div className="grid gap-3 sm:grid-cols-[minmax(0,320px)_1fr] items-start">
        <div className="relative aspect-square max-w-[320px] w-full overflow-hidden rounded-lg border border-slate-300 bg-slate-100">
          {full ? (
            <button type="button" className="absolute inset-0 cursor-zoom-in" onClick={() => setPreviewImage?.(full)} title="View full picture">
              <img
                src={getImageSrc(full)}
                onError={() => handleImgError(full)}
                alt="Reveal picture"
                className="h-full w-full object-cover"
              />
            </button>
          ) : (
            <div className="absolute inset-0 grid place-items-center text-[12px] text-slate-400">No picture stored</div>
          )}
          <div className="pointer-events-none absolute inset-0 grid grid-cols-2 grid-rows-2">
            {steps.map((s, i) => (
              <div
                key={s.step}
                className={`${CELL[s.position] || ''} grid place-items-center transition-opacity duration-300 ${
                  i < shown ? 'opacity-0' : 'opacity-100'
                } bg-slate-800/90 border border-slate-600 text-white`}
                data-testid={`cover-${s.step}`}
                data-lifted={i < shown ? 'true' : 'false'}
              >
                <div className="text-center leading-tight">
                  <div className="text-[22px] font-extrabold">{s.step}</div>
                  <div className="text-[11px] font-mono text-slate-200">{ladderText(s.correct, s.wrong)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-2 text-[12px]">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setShown((n) => Math.max(0, n - 1))}
              disabled={shown === 0}
              className="rounded border border-slate-200 bg-white px-2 py-1 font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40 cursor-pointer"
              aria-label="Cover back"
            >
              ◀ Back
            </button>
            <button
              type="button"
              onClick={() => setShown((n) => Math.min(steps.length, n + 1))}
              disabled={shown >= steps.length}
              className="rounded bg-sky-600 px-2.5 py-1 font-semibold text-white hover:bg-sky-700 disabled:opacity-40 cursor-pointer"
              aria-label="Lift next cover"
            >
              Lift cover {Math.min(shown + 1, steps.length)} ▶
            </button>
            <button type="button" onClick={() => setShown(0)} className="rounded px-2 py-1 font-semibold text-slate-500 hover:text-slate-800 cursor-pointer">
              Reset
            </button>
            <span className="text-slate-500">{shown}/{steps.length} lifted</span>
          </div>
          <ol className="space-y-1">
            {steps.map((s, i) => (
              <li key={s.step} className={`flex items-center gap-2 rounded border px-2 py-1 ${i < shown ? 'border-sky-300 bg-sky-50' : 'border-slate-200 bg-white'}`}>
                <span className="grid h-5 w-5 place-items-center rounded-full bg-slate-800 text-[11px] font-bold text-white">{s.step}</span>
                <span className="font-mono font-semibold text-slate-800">{ladderText(s.correct, s.wrong)}</span>
                <span className="text-slate-500">{s.position.replace('-', ' ')}</span>
              </li>
            ))}
          </ol>
          <div className="text-[12.5px]">
            <span className="font-bold text-sky-700">Answer:</span>{' '}
            <span className="font-semibold text-slate-900">{payload.answer}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
