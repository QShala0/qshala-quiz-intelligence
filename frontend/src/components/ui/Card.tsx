import type { HTMLAttributes } from 'react';

type Tone = 'white' | 'sun' | 'sky' | 'leaf';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: Tone;
  /** Lift on hover (use for clickable cards only). */
  interactive?: boolean;
  /** Sticker outline + hard shadow. Off = quiet card for dense work screens. */
  outlined?: boolean;
}

const TONES: Record<Tone, string> = {
  white: 'bg-white',
  sun: 'bg-sun-50',
  sky: 'bg-sky-50',
  leaf: 'bg-leaf-50',
};

export default function Card({ tone = 'white', interactive = false, outlined = true, className = '', ...rest }: CardProps) {
  const frame = outlined ? 'border border-slate-200 shadow-xs' : 'border border-slate-200/60 shadow-soft';
  const lift = interactive ? 'transition-all hover:-translate-y-0.5 hover:shadow-sm hover:border-slate-300 cursor-pointer' : '';
  return <div className={`rounded-2xl ${TONES[tone]} ${frame} ${lift} ${className}`} {...rest} />;
}

export function Badge({
  tone = 'sun',
  className = '',
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: 'sun' | 'sky' | 'leaf' | 'berry' | 'ink' }) {
  const map = {
    sun: 'bg-sun-100 text-ink',
    sky: 'bg-sky-100 text-ink',
    leaf: 'bg-leaf-100 text-ink',
    berry: 'bg-berry-50 text-berry',
    ink: 'bg-ink text-white',
  } as const;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-pill px-2.5 py-0.5 text-xs font-bold ${map[tone]} ${className}`}
      {...rest}
    />
  );
}
