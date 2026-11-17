'use client';

import React, { useState, useEffect, useRef } from 'react';
import Image from 'next/image';

interface YarnPlayCatProps {
  /** Total rendered height in px (aspect ratio: 480 / 414). Default is 76px. */
  height?: number;
  /** When true (e.g. user focusing or typing into prompt), switches to energetic batting mode */
  active?: boolean;
  /** When true, clicking triggers squish recoil and shows thought bubbles */
  interactive?: boolean;
  /** Custom speech bubble text or list of random quips */
  speechBubble?: string | string[];
  bubblePosition?: 'top' | 'top-right' | 'top-left';
  className?: string;
  onClick?: () => void;
  alt?: string;
}

const DEFAULT_QUIPS = [
  'Batting around some quiz ideas!',
  'Swat! That was a high bounce!',
  'Feed me a quiz prompt!',
  'Curiosity unlocked!',
  'Ready when you are, quizmaster!',
  'Purr-fect question incoming...',
];

/**
 * Animator-grade interactive Cat playing with green yarn ball.
 * Layered architecture:
 * 1. Cat on yellow mat (swats paws up as the ball approaches)
 * 2. Green yarn ball & curling string (arcs, bounces, and spins through the air)
 *
 * Strict zero layout shift (CLS = 0) and accessible.
 */
export default function YarnPlayCat({
  height = 76,
  active = false,
  interactive = true,
  speechBubble = DEFAULT_QUIPS,
  bubblePosition = 'top',
  className = '',
  onClick,
  alt = 'QT the cat playing with a green yarn ball on a yellow mat',
}: YarnPlayCatProps) {
  const w = 480;
  const h = 414;
  const width = Math.round((height * w) / h);

  const [isSquishing, setIsSquishing] = useState(false);
  const [activeBubble, setActiveBubble] = useState<string | null>(null);
  const bubbleTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    return () => {
      if (bubbleTimeoutRef.current) clearTimeout(bubbleTimeoutRef.current);
    };
  }, []);

  const handleClick = (e: React.MouseEvent) => {
    if (interactive || onClick) {
      setIsSquishing(true);
      setTimeout(() => setIsSquishing(false), 380);

      let nextBubbleText: string | null = null;
      if (Array.isArray(speechBubble)) {
        nextBubbleText = speechBubble[Math.floor(Math.random() * speechBubble.length)];
      } else if (speechBubble) {
        nextBubbleText = speechBubble;
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

  const bubblePositionClass =
    bubblePosition === 'top-left'
      ? 'bottom-full right-0 mb-3'
      : bubblePosition === 'top-right'
      ? 'bottom-full left-0 mb-3'
      : 'bottom-full left-1/2 -translate-x-1/2 mb-3';

  return (
    <div
      className={`relative inline-block select-none shrink-0 ${
        interactive ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 rounded-lg' : ''
      } ${className}`}
      style={{ width, height }}
      onClick={interactive ? handleClick : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
      tabIndex={interactive ? 0 : undefined}
      role={interactive ? 'button' : 'img'}
      aria-label={alt}
    >
      {/* Speech / Thought Bubble */}
      {activeBubble && (
        <div
          role="status"
          aria-live="polite"
          className={`absolute ${bubblePositionClass} z-30 pointer-events-none select-none animate-bubble-pop whitespace-nowrap`}
        >
          <div className="relative rounded-2xl border-2 border-slate-900 bg-amber-100 px-3.5 py-1.5 text-[12px] font-bold text-slate-900 shadow-sticker-sm">
            <span>{activeBubble}</span>
            {/* Bubble Tail */}
            <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 h-2.5 w-2.5 rotate-45 border-b-2 border-r-2 border-slate-900 bg-amber-100" />
          </div>
        </div>
      )}

      {/* Layer 1: Cat on the Yellow Mat (swats paws up) */}
      <div
        className={`absolute inset-0 transition-transform duration-200 ${
          isSquishing ? 'animate-squish' : active ? 'animate-cat-swat-fast' : 'animate-cat-swat'
        }`}
        style={{
          top: '25.12%',
          left: 0,
          width: '100%',
          height: '74.88%',
        }}
      >
        <Image
          src="/qt/yarn-body.webp"
          alt=""
          aria-hidden="true"
          width={480}
          height={310}
          unoptimized
          draggable={false}
          className="w-full h-full object-contain select-none pointer-events-none"
        />
      </div>

      {/* Layer 2: Green Yarn Ball & Trailing String (arcs, bounces and floats) */}
      <div
        className={`absolute pointer-events-none transition-transform duration-200 ${
          isSquishing ? 'animate-squish' : active ? 'animate-yarn-ball-fast' : 'animate-yarn-ball'
        }`}
        style={{
          top: 0,
          left: '59.79%',
          width: '40.21%',
          height: '24.4%',
        }}
      >
        <Image
          src="/qt/yarn-ball.webp"
          alt=""
          aria-hidden="true"
          width={193}
          height={101}
          unoptimized
          draggable={false}
          className="w-full h-full object-contain select-none pointer-events-none"
        />
      </div>
    </div>
  );
}
