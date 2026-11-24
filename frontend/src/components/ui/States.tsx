'use client';

import { useEffect, useState, type ReactNode } from 'react';
import QT, { type QTPose } from './QT';
import QTAnim, { type QTAnimName } from './QTAnim';
import Button from './Button';

/** Friendly "nothing here yet" panel. Pick a pose that matches the moment (box = empty, fishbowl = no results). */
export function EmptyState({
  pose = 'box-yellow',
  anim,
  title,
  hint,
  action,
  className = '',
}: {
  pose?: QTPose;
  /** Optional official animation (e.g. 'lets-get-started' for first-run); `pose` is the reduced-motion fallback. */
  anim?: QTAnimName;
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-col items-center gap-3 px-6 py-12 text-center ${className}`}>
      {anim ? <QTAnim anim={anim} fallbackPose={pose} height={110} /> : <QT pose={pose} height={110} motion="breathe" />}
      <h3 className="text-lg font-extrabold">{title}</h3>
      {hint && <p className="max-w-sm text-sm text-ink/70">{hint}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** Loading panel with rotating, on-brand lines. Pass your own `lines` to describe the real work. */
export function LoadingState({
  pose = 'chase-blue',
  anim,
  lines = ['QT is on the case…', 'Sniffing out answers…', 'Herding the questions…', 'Nearly there…'],
  className = '',
}: {
  pose?: QTPose;
  /** Optional official animation (e.g. 'magnify' while searching the bank); `pose` is the reduced-motion fallback. */
  anim?: QTAnimName;
  lines?: string[];
  className?: string;
}) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (lines.length < 2) return;
    const id = window.setInterval(() => setI((n) => (n + 1) % lines.length), 2400);
    return () => window.clearInterval(id);
  }, [lines.length]);

  return (
    <div className={`flex flex-col items-center gap-3 px-6 py-12 text-center ${className}`} role="status" aria-live="polite">
      {anim ? <QTAnim anim={anim} fallbackPose={pose} height={90} /> : <QT pose={pose} height={90} motion="bob" />}
      <p className="text-sm font-semibold text-ink/80">{lines[i]}</p>
    </div>
  );
}

/** Plain-language error with a retry. Never blames the user; always says what to do next. */
export function ErrorState({
  title = 'Oops, QT knocked something over',
  message,
  onRetry,
  className = '',
}: {
  title?: string;
  message?: ReactNode;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div className={`flex flex-col items-center gap-3 px-6 py-12 text-center ${className}`} role="alert">
      <QT pose="dizzy" height={110} />
      <h3 className="text-lg font-extrabold">{title}</h3>
      {message && <p className="max-w-md text-sm text-ink/70">{message}</p>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
