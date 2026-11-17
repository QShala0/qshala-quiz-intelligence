/**
 * Faint school-doodle texture (question marks, plus signs, dots, stars) in brand colours.
 * Decorative only: place behind hero / empty areas, never behind dense tables or forms.
 */
export default function Doodles({ className = '', opacity = 0.18 }: { className?: string; opacity?: number }) {
  return (
    <svg
      aria-hidden
      className={`pointer-events-none absolute inset-0 h-full w-full ${className}`}
      style={{ opacity }}
    >
      <defs>
        <pattern id="qt-doodles" width="140" height="140" patternUnits="userSpaceOnUse">
          <text x="14" y="38" fontSize="28" fontWeight="900" fill="#30B2E7" transform="rotate(-12 14 38)">?</text>
          <path d="M98 24h14M105 17v14" stroke="#FDB913" strokeWidth="4" strokeLinecap="round" />
          <circle cx="68" cy="92" r="6" fill="none" stroke="#75B543" strokeWidth="3.5" />
          <path d="M20 108l6 10 10-6" fill="none" stroke="#111" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="116" cy="84" r="3.5" fill="#FDB913" />
          <text x="96" y="130" fontSize="20" fontWeight="900" fill="#FDB913" transform="rotate(10 96 130)">A</text>
          <text x="50" y="58" fontSize="16" fontWeight="900" fill="#75B543">2+2</text>
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#qt-doodles)" />
    </svg>
  );
}
