import QT, { type QTPose } from './QT';

/** Rounded progress bar. With `rider`, a tiny QT sits on the leading edge of the fill. */
export default function Progress({
  value,
  tone = 'sun',
  rider,
  label,
  className = '',
}: {
  /** 0-100 */
  value: number;
  tone?: 'sun' | 'sky' | 'leaf';
  rider?: QTPose;
  label?: string;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  const fill = { sun: 'bg-sun', sky: 'bg-sky', leaf: 'bg-leaf' }[tone];
  return (
    <div
      className={`relative h-3.5 rounded-pill border-2 border-ink bg-white ${rider ? 'mt-6' : ''} ${className}`}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
    >
      <div className={`h-full rounded-pill transition-[width] duration-500 ease-out ${fill}`} style={{ width: `${pct}%` }} />
      {rider && (
        <span
          className="absolute -top-[26px] transition-[left] duration-500 ease-out"
          style={{ left: `calc(${pct}% - 20px)` }}
          aria-hidden
        >
          <QT pose={rider} height={26} />
        </span>
      )}
    </div>
  );
}
