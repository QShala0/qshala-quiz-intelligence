import React from 'react';

export type QCatPose = 'idle' | 'peek' | 'think' | 'happy' | 'sleep' | 'paw';

export interface QCatProps {
  pose?: QCatPose;
  size?: number | string;
  className?: string;
  alt?: string;
  lookingAtText?: boolean;
}

/**
 * QCat - The QShala Quiz Companion Mascot.
 * 
 * Original lightweight inline SVG (<6 KB) in authentic brand colours:
 * - Ink black body (#1E222D)
 * - Round white glasses (#FFFCF5)
 * - Sunny yellow necktie (#F8B42C)
 * 
 * Poses: 'idle' | 'peek' | 'think' | 'happy' | 'sleep' | 'paw'
 * CSS keyframes handle gentle breathing, periodic blinking (~5s), and tail swishes.
 * Respects prefers-reduced-motion, pointer-events: none, and causes zero layout shift.
 */
export default function QCat({
  pose = 'idle',
  size = 64,
  className = '',
  alt = 'QShala Cat mascot',
  lookingAtText = false,
}: QCatProps) {
  const pixelSize = typeof size === 'number' ? `${size}px` : size;

  return (
    <div
      className={`inline-block select-none pointer-events-none ${className}`}
      style={{ width: pixelSize, height: pixelSize }}
      role="img"
      aria-label={alt}
    >
      <svg
        viewBox="0 0 100 100"
        width="100%"
        height="100%"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="w-full h-full overflow-visible"
      >
        {/* Definitions for shared gradients / shadows */}
        <defs>
          <linearGradient id="qcat-tie-grad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#F8B42C" />
            <stop offset="100%" stopColor="#E29E1B" />
          </linearGradient>
          <linearGradient id="qcat-fur-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#252A38" />
            <stop offset="100%" stopColor="#1E222D" />
          </linearGradient>
        </defs>

        {/* ───────────── POSE: IDLE (Calm, seated companion) ───────────── */}
        {pose === 'idle' && (
          <g className="qcat-breathe origin-bottom">
            {/* Tail swishing gently */}
            <path
              d="M 68 82 C 78 80, 88 74, 88 62 C 88 52, 78 50, 76 56 C 75 60, 80 66, 80 70"
              stroke="#1E222D"
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
              className="qcat-tail origin-[68px_82px]"
            />

            {/* Cat Body */}
            <ellipse cx="50" cy="74" rx="22" ry="18" fill="url(#qcat-fur-grad)" />

            {/* Front Paws */}
            <circle cx="42" cy="88" r="5" fill="#1E222D" />
            <circle cx="58" cy="88" r="5" fill="#1E222D" />

            {/* Head */}
            <circle cx="50" cy="46" r="22" fill="url(#qcat-fur-grad)" />

            {/* Ears */}
            <path d="M 33 34 L 25 14 L 43 26 Z" fill="#1E222D" />
            <path d="M 32 30 L 27 17 L 39 25 Z" fill="#32384A" />
            <path d="M 67 34 L 75 14 L 57 26 Z" fill="#1E222D" />
            <path d="M 68 30 L 73 17 L 61 25 Z" fill="#32384A" />

            {/* Round White Glasses */}
            <circle cx="41" cy="45" r="9" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.12)" />
            <circle cx="59" cy="45" r="9" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.12)" />
            <path d="M 50 45 L 50 45" stroke="#FFFCF5" strokeWidth="2.4" strokeLinecap="round" />
            <path d="M 32 44 L 27 42" stroke="#FFFCF5" strokeWidth="2" strokeLinecap="round" />
            <path d="M 68 44 L 73 42" stroke="#FFFCF5" strokeWidth="2" strokeLinecap="round" />

            {/* Eyes (Blinking every ~5s) */}
            <g className="qcat-blink origin-[50px_45px]">
              <circle cx="41" cy={lookingAtText ? 47 : 45} r="3.2" fill="#FFFCF5" />
              <circle cx="41.8" cy={lookingAtText ? 47.6 : 45.4} r="2.2" fill="#1E222D" />
              <circle cx="40.8" cy={lookingAtText ? 46.2 : 44.2} r="0.9" fill="#FFFCF5" />

              <circle cx="59" cy={lookingAtText ? 47 : 45} r="3.2" fill="#FFFCF5" />
              <circle cx="59.8" cy={lookingAtText ? 47.6 : 45.4} r="2.2" fill="#1E222D" />
              <circle cx="58.8" cy={lookingAtText ? 46.2 : 44.2} r="0.9" fill="#FFFCF5" />
            </g>

            {/* Nose & Mouth */}
            <path d="M 48.5 53.5 Q 50 55 51.5 53.5" stroke="#E9DFC9" strokeWidth="1.2" strokeLinecap="round" fill="none" />

            {/* Whiskers */}
            <path d="M 31 51 L 18 49 M 31 54 L 17 56" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.75" />
            <path d="M 69 51 L 82 49 M 69 54 L 83 56" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.75" />

            {/* Sunny Yellow Tie */}
            <path d="M 47.5 58 L 52.5 58 L 51 62 L 49 62 Z" fill="#D99618" />
            <path d="M 49 62 L 51 62 L 53.5 75 L 50 79 L 46.5 75 Z" fill="url(#qcat-tie-grad)" />
          </g>
        )}

        {/* ───────────── POSE: PEEK (Peeking over prompt bar edge) ───────────── */}
        {pose === 'peek' && (
          <g className={lookingAtText ? 'translate-y-1 transition-transform duration-200' : 'qcat-breathe origin-bottom'}>
            {/* Tail peeking up from behind */}
            <path
              d="M 76 80 C 84 75, 89 65, 87 54 C 85 45, 77 44, 75 49 C 74 53, 79 58, 80 62"
              stroke="#1E222D"
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
              className="qcat-tail origin-[76px_80px]"
            />

            {/* Head peeking over edge */}
            <circle cx="50" cy="50" r="26" fill="url(#qcat-fur-grad)" />

            {/* Alert Perked Ears */}
            <path d="M 30 36 L 20 12 L 42 26 Z" fill="#1E222D" className={lookingAtText ? 'rotate-[-3deg] origin-[30px_36px]' : ''} />
            <path d="M 29 32 L 23 16 L 37 25 Z" fill="#32384A" />
            <path d="M 70 36 L 80 12 L 58 26 Z" fill="#1E222D" className={lookingAtText ? 'rotate-[3deg] origin-[70px_36px]' : ''} />
            <path d="M 71 32 L 77 16 L 63 25 Z" fill="#32384A" />

            {/* Round White Glasses */}
            <circle cx="40" cy="50" r="9.5" stroke="#FFFCF5" strokeWidth="2.5" fill="rgba(255, 252, 245, 0.12)" />
            <circle cx="60" cy="50" r="9.5" stroke="#FFFCF5" strokeWidth="2.5" fill="rgba(255, 252, 245, 0.12)" />
            <path d="M 49.5 50 L 50.5 50" stroke="#FFFCF5" strokeWidth="2.5" strokeLinecap="round" />
            <path d="M 30.5 49 L 24 47" stroke="#FFFCF5" strokeWidth="2" strokeLinecap="round" />
            <path d="M 69.5 49 L 76 47" stroke="#FFFCF5" strokeWidth="2" strokeLinecap="round" />

            {/* Eyes looking down at prompt */}
            <g className="qcat-blink origin-[50px_50px]">
              <circle cx="40" cy={lookingAtText ? 53 : 51} r="3.4" fill="#FFFCF5" />
              <circle cx="40" cy={lookingAtText ? 54 : 51.8} r="2.3" fill="#1E222D" />
              <circle cx="39.2" cy={lookingAtText ? 53.2 : 50.8} r="1" fill="#FFFCF5" />

              <circle cx="60" cy={lookingAtText ? 53 : 51} r="3.4" fill="#FFFCF5" />
              <circle cx="60" cy={lookingAtText ? 54 : 51.8} r="2.3" fill="#1E222D" />
              <circle cx="59.2" cy={lookingAtText ? 53.2 : 50.8} r="1" fill="#FFFCF5" />
            </g>

            {/* Nose & Whiskers */}
            <path d="M 48.5 59 Q 50 60.5 51.5 59" stroke="#E9DFC9" strokeWidth="1.2" strokeLinecap="round" fill="none" />
            <path d="M 29 55 L 16 53 M 29 58 L 15 60" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.75" />
            <path d="M 71 55 L 84 53 M 71 58 L 85 60" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.75" />

            {/* Yellow Tie hanging over */}
            <path d="M 47.5 64 L 52.5 64 L 51 68 L 49 68 Z" fill="#D99618" />
            <path d="M 49 68 L 51 68 L 53 79 L 50 83 L 47 79 Z" fill="url(#qcat-tie-grad)" />

            {/* Two Paws clutching the bottom edge (resting on border) */}
            <g>
              <ellipse cx="32" cy="78" rx="6.5" ry="5" fill="#1E222D" stroke="#FFFCF5" strokeWidth="1.5" />
              <line x1="30" y1="76" x2="30" y2="79" stroke="#32384A" strokeWidth="1" />
              <line x1="34" y1="76" x2="34" y2="79" stroke="#32384A" strokeWidth="1" />

              <ellipse cx="68" cy="78" rx="6.5" ry="5" fill="#1E222D" stroke="#FFFCF5" strokeWidth="1.5" />
              <line x1="66" y1="76" x2="66" y2="79" stroke="#32384A" strokeWidth="1" />
              <line x1="70" y1="76" x2="70" y2="79" stroke="#32384A" strokeWidth="1" />
            </g>
          </g>
        )}

        {/* ───────────── POSE: THINK (Generating, contemplating quiz) ───────────── */}
        {pose === 'think' && (
          <g>
            {/* Floating curiosity question marks drifting up */}
            <g className="qcat-question">
              <text x="68" y="24" fill="#F8B42C" fontSize="16" fontWeight="bold" fontFamily="sans-serif">?</text>
            </g>
            <g className="qcat-question" style={{ animationDelay: '1.2s' }}>
              <text x="78" y="34" fill="#F8B42C" fontSize="12" fontWeight="bold" fontFamily="sans-serif">?</text>
            </g>

            {/* Head tilted in deep thought */}
            <g className="origin-[50px_60px] rotate-[-5deg]">
              {/* Body */}
              <ellipse cx="48" cy="76" rx="20" ry="16" fill="url(#qcat-fur-grad)" />

              {/* Head */}
              <circle cx="48" cy="48" r="22" fill="url(#qcat-fur-grad)" />

              {/* Ears */}
              <path d="M 31 36 L 22 16 L 41 28 Z" fill="#1E222D" />
              <path d="M 65 36 L 75 16 L 55 28 Z" fill="#1E222D" />

              {/* Round White Glasses */}
              <circle cx="39" cy="47" r="8.5" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.12)" />
              <circle cx="57" cy="47" r="8.5" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.12)" />
              <path d="M 47.5 47 L 48.5 47" stroke="#FFFCF5" strokeWidth="2.4" />

              {/* Eyes looking upward toward the thought bubble */}
              <circle cx="41" cy="44.5" r="3.2" fill="#FFFCF5" />
              <circle cx="42" cy="44" r="2.2" fill="#1E222D" />
              <circle cx="42.5" cy="43.2" r="0.8" fill="#FFFCF5" />

              <circle cx="59" cy="44.5" r="3.2" fill="#FFFCF5" />
              <circle cx="60" cy="44" r="2.2" fill="#1E222D" />
              <circle cx="60.5" cy="43.2" r="0.8" fill="#FFFCF5" />

              {/* Whiskers */}
              <path d="M 29 52 L 16 50 M 29 55 L 15 57" stroke="#FFFCF5" strokeWidth="1.2" opacity="0.75" />
              <path d="M 67 52 L 80 50 M 67 55 L 81 57" stroke="#FFFCF5" strokeWidth="1.2" opacity="0.75" />

              {/* Yellow Tie */}
              <path d="M 46 60 L 51 60 L 49.5 64 L 47.5 64 Z" fill="#D99618" />
              <path d="M 47.5 64 L 49.5 64 L 51.5 76 L 48.5 80 L 45.5 76 Z" fill="url(#qcat-tie-grad)" />

              {/* Paw raised to chin / cheek */}
              <ellipse cx="61" cy="56" rx="5" ry="6" fill="#1E222D" stroke="#FFFCF5" strokeWidth="1.2" />
            </g>

            {/* Left Paw grounded */}
            <circle cx="36" cy="88" r="5" fill="#1E222D" />
          </g>
        )}

        {/* ───────────── POSE: HAPPY (Draft ready, celebratory hop) ───────────── */}
        {pose === 'happy' && (
          <g className="qcat-happy origin-bottom">
            {/* Wagging Tail */}
            <path
              d="M 68 80 C 78 76, 88 68, 86 54 C 84 46, 76 46, 76 52 C 76 56, 80 62, 80 66"
              stroke="#1E222D"
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
              className="qcat-wag origin-[68px_80px]"
            />

            {/* Body */}
            <ellipse cx="50" cy="72" rx="21" ry="17" fill="url(#qcat-fur-grad)" />

            {/* Head */}
            <circle cx="50" cy="44" r="22" fill="url(#qcat-fur-grad)" />

            {/* Ears perked slightly wide in joy */}
            <path d="M 32 32 L 21 13 L 42 24 Z" fill="#1E222D" />
            <path d="M 31 28 L 24 16 L 38 23 Z" fill="#F8B42C" opacity="0.6" />
            <path d="M 68 32 L 79 13 L 58 24 Z" fill="#1E222D" />
            <path d="M 69 28 L 76 16 L 62 23 Z" fill="#F8B42C" opacity="0.6" />

            {/* Round White Glasses */}
            <circle cx="41" cy="43" r="9" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.15)" />
            <circle cx="59" cy="43" r="9" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.15)" />
            <path d="M 50 43 L 50 43" stroke="#FFFCF5" strokeWidth="2.4" strokeLinecap="round" />

            {/* Joyful Eyes (^ ^ arcs) */}
            <path d="M 36.5 44 Q 41 39 45.5 44" stroke="#FFFCF5" strokeWidth="2.6" strokeLinecap="round" fill="none" />
            <path d="M 54.5 44 Q 59 39 63.5 44" stroke="#FFFCF5" strokeWidth="2.6" strokeLinecap="round" fill="none" />

            {/* Sweet Joyful Smile (:3) */}
            <path d="M 46.5 50.5 Q 48.5 52.5 50 51 Q 51.5 52.5 53.5 50.5" stroke="#FFFCF5" strokeWidth="1.4" strokeLinecap="round" fill="none" />

            {/* Whiskers */}
            <path d="M 31 49 L 18 47 M 31 52 L 17 54" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.8" />
            <path d="M 69 49 L 82 47 M 69 52 L 83 54" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.8" />

            {/* Fluttering Yellow Tie */}
            <path d="M 47.5 55 L 52.5 55 L 51 59 L 49 59 Z" fill="#D99618" />
            <path d="M 49 59 L 51 59 L 55 72 L 51 76 L 47 72 Z" fill="url(#qcat-tie-grad)" />

            {/* Joyful Paws */}
            <circle cx="38" cy="84" r="5" fill="#1E222D" />
            <circle cx="62" cy="84" r="5" fill="#1E222D" />

            {/* Subtle sunny sparkles */}
            <path d="M 22 28 L 24 24 L 26 28 L 30 30 L 26 32 L 24 36 L 22 32 L 18 30 Z" fill="#F8B42C" opacity="0.8" />
            <path d="M 76 26 L 77.5 23 L 79 26 L 82 27.5 L 79 29 L 77.5 32 L 76 29 L 73 27.5 Z" fill="#F8B42C" opacity="0.8" />
          </g>
        )}

        {/* ───────────── POSE: SLEEP (Cozy curled up ball on error/idle) ───────────── */}
        {pose === 'sleep' && (
          <g className="qcat-sleep origin-center">
            {/* Curled Body Ball */}
            <ellipse cx="50" cy="58" rx="28" ry="22" fill="url(#qcat-fur-grad)" />

            {/* Curled Tail wrapped tightly around front */}
            <path
              d="M 74 64 C 76 74, 64 78, 50 78 C 34 78, 22 72, 24 62 C 25 56, 30 54, 34 56"
              stroke="#1E222D"
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
            />

            {/* Cozy Flat Head */}
            <ellipse cx="44" cy="50" rx="18" ry="16" fill="url(#qcat-fur-grad)" />

            {/* Relaxed Flat Ears */}
            <path d="M 30 42 L 20 28 L 36 34 Z" fill="#1E222D" />
            <path d="M 58 42 L 68 28 L 52 34 Z" fill="#1E222D" />

            {/* Round Glasses resting on sleepy face */}
            <circle cx="38" cy="51" r="7.5" stroke="#FFFCF5" strokeWidth="2" fill="rgba(255, 252, 245, 0.12)" />
            <circle cx="53" cy="51" r="7.5" stroke="#FFFCF5" strokeWidth="2" fill="rgba(255, 252, 245, 0.12)" />
            <path d="M 45.5 51 L 45.5 51" stroke="#FFFCF5" strokeWidth="2" />

            {/* Sleeping Arcs (u u) */}
            <path d="M 34.5 50 Q 38 54 41.5 50" stroke="#FFFCF5" strokeWidth="2" strokeLinecap="round" fill="none" />
            <path d="M 49.5 50 Q 53 54 56.5 50" stroke="#FFFCF5" strokeWidth="2" strokeLinecap="round" fill="none" />

            {/* Small yellow tie tucked beneath chin */}
            <path d="M 42 60 L 46 60 L 47 67 L 44 70 L 41 67 Z" fill="url(#qcat-tie-grad)" />

            {/* Soft Zzz */}
            <text x="68" y="38" fill="#D8CBB0" fontSize="10" fontWeight="bold" fontFamily="sans-serif" opacity="0.7">z</text>
            <text x="75" y="30" fill="#D8CBB0" fontSize="13" fontWeight="bold" fontFamily="sans-serif" opacity="0.9">Z</text>
          </g>
        )}

        {/* ───────────── POSE: PAW (Friendly wave / nudge) ───────────── */}
        {pose === 'paw' && (
          <g className="qcat-breathe origin-bottom">
            {/* Seated Body */}
            <ellipse cx="50" cy="74" rx="22" ry="18" fill="url(#qcat-fur-grad)" />

            {/* Tail */}
            <path
              d="M 68 82 C 78 80, 88 74, 88 62 C 88 52, 78 50, 76 56"
              stroke="#1E222D"
              strokeWidth="5"
              strokeLinecap="round"
              fill="none"
              className="qcat-tail origin-[68px_82px]"
            />

            {/* Head */}
            <circle cx="50" cy="46" r="22" fill="url(#qcat-fur-grad)" />

            {/* Ears */}
            <path d="M 33 34 L 25 14 L 43 26 Z" fill="#1E222D" />
            <path d="M 67 34 L 75 14 L 57 26 Z" fill="#1E222D" />

            {/* Round White Glasses */}
            <circle cx="41" cy="45" r="9" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.12)" />
            <circle cx="59" cy="45" r="9" stroke="#FFFCF5" strokeWidth="2.4" fill="rgba(255, 252, 245, 0.12)" />
            <path d="M 50 45 L 50 45" stroke="#FFFCF5" strokeWidth="2.4" strokeLinecap="round" />

            {/* Eyes */}
            <circle cx="41" cy="45" r="3.2" fill="#FFFCF5" />
            <circle cx="41.8" cy="45.4" r="2.2" fill="#1E222D" />
            <circle cx="40.8" cy="44.2" r="0.9" fill="#FFFCF5" />

            <circle cx="59" cy="45" r="3.2" fill="#FFFCF5" />
            <circle cx="59.8" cy="45.4" r="2.2" fill="#1E222D" />
            <circle cx="58.8" cy="44.2" r="0.9" fill="#FFFCF5" />

            {/* Nose & Whiskers */}
            <path d="M 48.5 53.5 Q 50 55 51.5 53.5" stroke="#E9DFC9" strokeWidth="1.2" strokeLinecap="round" fill="none" />
            <path d="M 31 51 L 18 49 M 31 54 L 17 56" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.75" />
            <path d="M 69 51 L 82 49 M 69 54 L 83 56" stroke="#FFFCF5" strokeWidth="1.2" strokeLinecap="round" opacity="0.75" />

            {/* Yellow Tie */}
            <path d="M 47.5 58 L 52.5 58 L 51 62 L 49 62 Z" fill="#D99618" />
            <path d="M 49 62 L 51 62 L 53.5 75 L 50 79 L 46.5 75 Z" fill="url(#qcat-tie-grad)" />

            {/* Left Paw on ground */}
            <circle cx="40" cy="88" r="5" fill="#1E222D" />

            {/* Right Paw Raised & Waving */}
            <g className="qcat-wave origin-[66px_66px]">
              <path d="M 64 68 C 68 62, 74 54, 76 46" stroke="#1E222D" strokeWidth="6" strokeLinecap="round" />
              <ellipse cx="76" cy="44" rx="5" ry="4" fill="#1E222D" stroke="#FFFCF5" strokeWidth="1.2" />
              <circle cx="76" cy="44" r="1.5" fill="#F8B42C" />
            </g>
          </g>
        )}
      </svg>
    </div>
  );
}

/**
 * Reusable tiny paw print icon for progress bars, rating, or buttons
 */
export function PawIcon({ className = 'w-4 h-4', fill = 'currentColor' }: { className?: string; fill?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill={fill} xmlns="http://www.w3.org/2000/svg">
      <path d="M12 13c-2.2 0-4 1.8-4 4 0 1.7 1.3 3 3 3 .6 0 1-.4 1-1 0-.6.4-1 1-1s1 .4 1 1c0 .6.4 1 1 1 1.7 0 3-1.3 3-3 0-2.2-1.8-4-4-4z" />
      <circle cx="7.5" cy="10.5" r="1.8" />
      <circle cx="10.5" cy="7.5" r="1.8" />
      <circle cx="13.5" cy="7.5" r="1.8" />
      <circle cx="16.5" cy="10.5" r="1.8" />
    </svg>
  );
}
