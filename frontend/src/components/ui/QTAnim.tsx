'use client';

import Image from 'next/image';
import { useSyncExternalStore } from 'react';
import QT, { type QTPose } from './QT';

/**
 * Official QT animations (Brand Elements / QT Animations), converted to animated WebP in /public/qt/anim.
 * Use each one only in its own context:
 *  - lets-get-started: first run / nothing here yet, inviting the first action
 *  - magnify:          searching or finding questions in the bank
 *  - celebrate:        something finished well (quiz ready, export done)
 *  - correct-answer:   an answer reveal / correct moment
 *  - megaphone:        announcements and batch notices
 * One QT per view. Never place over dense data.
 */
export type QTAnimName = 'celebrate' | 'correct-answer' | 'lets-get-started' | 'magnify' | 'megaphone';

const SIZES: Record<QTAnimName, [number, number]> = {
  celebrate: [220, 220],
  'correct-answer': [360, 203],
  'lets-get-started': [360, 203],
  magnify: [360, 360],
  megaphone: [360, 360],
};

const QUERY = '(prefers-reduced-motion: reduce)';

function subscribe(cb: () => void) {
  const mq = window.matchMedia(QUERY);
  mq.addEventListener('change', cb);
  return () => mq.removeEventListener('change', cb);
}
const getSnapshot = () => window.matchMedia(QUERY).matches;
// Server and first paint assume reduced motion, so users who asked for it never see a moving frame.
const getServerSnapshot = () => true;

export function usePrefersReducedMotion() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export default function QTAnim({
  anim,
  fallbackPose,
  height = 96,
  alt,
  className = '',
}: {
  anim: QTAnimName;
  /** Static pose shown when the user prefers reduced motion. */
  fallbackPose: QTPose;
  height?: number;
  alt?: string;
  className?: string;
}) {
  const reduced = usePrefersReducedMotion();
  if (reduced) return <QT pose={fallbackPose} height={height} alt={alt} className={className} />;

  const [w, h] = SIZES[anim];
  const width = Math.round((height * w) / h);
  return (
    <Image
      src={`/qt/anim/${anim}.webp`}
      width={width}
      height={height}
      alt={alt ?? ''}
      aria-hidden={alt ? undefined : true}
      unoptimized
      draggable={false}
      className={`select-none object-contain shrink-0 rounded-2xl ${className}`}
      style={{ width, height }}
    />
  );
}
