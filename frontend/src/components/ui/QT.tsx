'use client';

import React, { useState, useEffect, useRef } from 'react';
import Image from 'next/image';

/** Every pose available in /public/qt (built by scripts/build_qt_assets.py): [width, height]. */
const POSES = {
  sit: [480, 374],
  'chase-blue': [480, 236],
  'chase-yellow': [480, 236],
  'chase-green': [480, 236],
  professor: [480, 343],
  'box-blue': [480, 320],
  'box-yellow': [480, 320],
  'box-green': [480, 320],
  curled: [480, 456],
  fishbowl: [480, 206],
  stretch: [480, 208],
  round: [480, 362],
  whiskers: [480, 328],
  sprawl: [480, 216],
  'wide-eyed': [480, 362],
  fluffy: [480, 342],
  alert: [480, 296],
  'yarn-mat': [480, 414],
  'yarn-green': [480, 414],
  'yarn-blue': [480, 414],
  'yarn-yellow': [480, 414],
  happy: [480, 296],
  sleepy: [480, 357],
  dizzy: [475, 480],
  'door-green': [315, 480],
  'door-yellow': [315, 480],
  'door-blue': [315, 480],
} as const;

export type QTPose = keyof typeof POSES;
export const QT_POSES = Object.keys(POSES) as QTPose[];

const ALT: Partial<Record<QTPose, string>> = {
  sit: 'QT the QShala cat sitting',
  round: 'QT the round cat with a question-mark tail',
  professor: 'Professor QT wearing round spectacles and a necktie',
  'box-yellow': 'QT sitting cozily inside a yellow box',
  'yarn-green': 'QT batting a green ball of yarn',
  'yarn-mat': 'QT batting yarn on a yellow mat',
  'chase-blue': 'QT pouncing playfully at a blue ball',
  fishbowl: 'QT gazing curiously into a fishbowl',
  sprawl: 'QT sprawled out flat and relaxed',
  stretch: 'QT stretching lazily',
  dizzy: 'QT looking dizzy with swirling eyes',
  happy: 'QT looking cheerful and excited',
  sleepy: 'QT curled up asleep',
  alert: 'QT standing attentive and alert',
};

const DEFAULT_QUIPS: string[] = [
  'Curiosity didn’t hurt this cat!',
  'Feed me a quiz prompt!',
  'Purr-fect question incoming...',
  'Ready when you are, quizmaster!',
  'I love a good trivia quest!',
  'Every great quiz starts with a spark!',
];

export type Motion = 'none' | 'bob' | 'breathe' | 'wiggle' | 'pounce' | 'squish';

export interface QTProps {
  pose: QTPose;
  /** Rendered height in px (width follows the pose’s aspect ratio). */
  height?: number;
  /** Decorative cats are hidden from screen readers (default). Pass `alt` to make one meaningful. */
  alt?: string;
  motion?: Motion;
  className?: string;
  priority?: boolean;
  /** When true, clicking triggers a playful squish recoil and displays a speech bubble */
  interactive?: boolean;
  /** Custom speech bubble text or list of random quips shown on interaction */
  speechBubble?: string | string[];
  /** Positioning of the speech bubble relative to the cat */
  bubblePosition?: 'top' | 'top-right' | 'top-left';
  onClick?: () => void;
}

const MOTION_CLASS: Record<Motion, string> = {
  none: '',
  bob: 'animate-bob',
  breathe: 'animate-breathe',
  wiggle: 'hover:animate-wiggle',
  pounce: 'animate-pounce',
  squish: 'animate-squish',
};

/** Paw icon for ratings, progress stages, and decorative touches */
export function PawIcon({ className = 'w-4 h-4', ...props }: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
      {...props}
    >
      <ellipse cx="12" cy="16.5" rx="4.5" ry="3.5" />
      <circle cx="7" cy="10" r="2.2" />
      <circle cx="10.5" cy="7.5" r="2.2" />
      <circle cx="14" cy="7.5" r="2.2" />
      <circle cx="17.5" cy="10" r="2.2" />
    </svg>
  );
}

/**
 * QT, the QShala companion cat.
 * Authentic brand artworks rendered with zero layout shift (CLS = 0)
 * and accessible, interactive animations.
 */
export default function QT({
  pose,
  height = 96,
  alt,
  motion = 'none',
  className = '',
  priority = false,
  interactive = false,
  speechBubble,
  bubblePosition = 'top',
  onClick,
}: QTProps) {
  const [w, h] = POSES[pose] || [480, 362];
  const width = Math.round((height * w) / h);
  const label = alt ?? ALT[pose];

  const [isSquishing, setIsSquishing] = useState(false);
  const [activeBubble, setActiveBubble] = useState<string | null>(null);
  const bubbleTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (bubbleTimeoutRef.current) clearTimeout(bubbleTimeoutRef.current);
    };
  }, []);

  const handleClick = (e: React.MouseEvent) => {
    if (interactive || speechBubble || onClick) {
      // Trigger squish animation
      setIsSquishing(true);
      setTimeout(() => setIsSquishing(false), 380);

      // Determine speech bubble text
      let nextBubbleText: string | null = null;
      if (speechBubble) {
        if (Array.isArray(speechBubble)) {
          nextBubbleText = speechBubble[Math.floor(Math.random() * speechBubble.length)];
        } else {
          nextBubbleText = speechBubble;
        }
      } else if (interactive) {
        nextBubbleText = DEFAULT_QUIPS[Math.floor(Math.random() * DEFAULT_QUIPS.length)];
      }

      if (nextBubbleText) {
        setActiveBubble(nextBubbleText);
        if (bubbleTimeoutRef.current) clearTimeout(bubbleTimeoutRef.current);
        bubbleTimeoutRef.current = setTimeout(() => {
          setActiveBubble(null);
        }, 3400);
      }

      onClick?.();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((interactive || onClick) && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      handleClick(e as unknown as React.MouseEvent);
    }
  };

  const isClickable = interactive || !!onClick || !!speechBubble;

  const bubblePositionClass =
    bubblePosition === 'top-left'
      ? 'bottom-full right-0 mb-2'
      : bubblePosition === 'top-right'
      ? 'bottom-full left-0 mb-2'
      : 'bottom-full left-1/2 -translate-x-1/2 mb-2';

  return (
    <div
      className={`relative inline-flex items-center justify-center shrink-0 ${
        isClickable ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 rounded-lg' : ''
      }`}
      style={{ width, height }}
      onClick={isClickable ? handleClick : undefined}
      onKeyDown={isClickable ? handleKeyDown : undefined}
      tabIndex={isClickable ? 0 : undefined}
      role={isClickable ? 'button' : undefined}
      aria-label={isClickable ? `${label ?? 'QT companion'}. Click for fun.` : undefined}
    >
      {/* Speech / Thought Bubble */}
      {activeBubble && (
        <div
          role="status"
          aria-live="polite"
          className={`absolute ${bubblePositionClass} z-30 pointer-events-none select-none animate-bubble-pop whitespace-nowrap`}
        >
          <div className="relative rounded-2xl border-2 border-slate-900 bg-amber-100 px-3 py-1.5 text-[12px] font-bold text-slate-900 shadow-sticker-sm">
            <span>{activeBubble}</span>
            {/* Bubble Tail */}
            <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 h-2.5 w-2.5 rotate-45 border-b-2 border-r-2 border-slate-900 bg-amber-100" />
          </div>
        </div>
      )}

      <Image
        src={`/qt/${pose}.webp`}
        width={width}
        height={height}
        alt={alt === '' || !label ? '' : label}
        aria-hidden={alt === '' || !label ? true : undefined}
        priority={priority}
        unoptimized
        draggable={false}
        className={`select-none object-contain transition-transform duration-200 ${
          isSquishing ? 'animate-squish' : MOTION_CLASS[motion]
        } ${isClickable ? 'hover:scale-105 active:scale-95' : ''} ${className}`}
        style={{ width, height }}
      />
    </div>
  );
}
