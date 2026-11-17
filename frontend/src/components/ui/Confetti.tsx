'use client';

import { useMemo } from 'react';

const COLORS = ['#FDB913', '#30B2E7', '#75B543', '#111111'];

/** CSS-only confetti burst from the centre of the nearest positioned parent. Hidden for reduced-motion. */
export default function Confetti({ pieces = 16 }: { pieces?: number }) {
  // Deterministic "scatter" from the piece index (golden-ratio sequence): looks random, stays pure.
  const items = useMemo(
    () =>
      Array.from({ length: pieces }, (_, i) => {
        const r = (n: number) => (i * 0.6180339887 * n) % 1;
        const angle = (i / pieces) * Math.PI * 2 + r(1) * 0.5;
        const dist = 50 + r(2) * 60;
        return {
          key: i,
          color: COLORS[i % COLORS.length],
          dx: Math.round(Math.cos(angle) * dist),
          dy: Math.round(Math.sin(angle) * dist - 20),
          rot: Math.round((r(3) - 0.5) * 540),
          delay: Math.round(r(4) * 90),
        };
      }),
    [pieces],
  );

  return (
    <span className="pointer-events-none absolute inset-0 overflow-visible" aria-hidden>
      {items.map((p) => (
        <span
          key={p.key}
          className="qt-confetti-piece"
          style={
            {
              background: p.color,
              animationDelay: `${p.delay}ms`,
              '--dx': `${p.dx}px`,
              '--dy': `${p.dy}px`,
              '--rot': `${p.rot}deg`,
            } as React.CSSProperties
          }
        />
      ))}
    </span>
  );
}
