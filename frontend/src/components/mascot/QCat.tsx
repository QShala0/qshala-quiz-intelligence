'use client';

import React from 'react';
import QT, { QTPose, PawIcon as UIPawIcon } from '../ui/QT';

export type QCatPose = 'idle' | 'peek' | 'think' | 'happy' | 'sleep' | 'paw';

export interface QCatProps {
  pose?: QCatPose;
  size?: number | string;
  className?: string;
  alt?: string;
  lookingAtText?: boolean;
}

export { UIPawIcon as PawIcon };

/**
 * QCat - Adapted to use the authentic QT brand poses.
 * Maps legacy pose names to the authentic, charming WebP illustrations:
 * - 'idle' / 'peek' -> 'round' (the round cat with question-mark tail)
 * - 'think' -> 'professor' (the scholarly cat with round spectacles and tie)
 * - 'happy' -> 'happy'
 * - 'sleep' -> 'sleepy'
 * - 'paw' -> 'chase-blue'
 */
export default function QCat({
  pose = 'idle',
  size = 64,
  className = '',
  alt,
  lookingAtText = false,
}: QCatProps) {
  const numericHeight = typeof size === 'number' ? size : parseInt(size, 10) || 64;

  let qtPose: QTPose = 'round';
  if (pose === 'think') qtPose = 'professor';
  else if (pose === 'sleep') qtPose = 'sleepy';
  else if (pose === 'paw') qtPose = 'chase-blue';
  else if (pose === 'happy') qtPose = 'happy';
  else if (pose === 'peek' || pose === 'idle') qtPose = 'round';

  return (
    <QT
      pose={qtPose}
      height={numericHeight}
      alt={alt}
      motion={pose === 'sleep' ? 'breathe' : lookingAtText ? 'wiggle' : 'breathe'}
      className={className}
      interactive={true}
    />
  );
}
