import type { ReactNode } from 'react';
import QT, { type QTPose } from './QT';

/** One header for every page: unifies the title style and gives each page a quiet QT. */
export default function PageHeader({
  title,
  subtitle,
  actions,
  pose,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  pose?: QTPose;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="flex items-end gap-3">
        {pose && <QT pose={pose} height={52} className="hidden sm:block" />}
        <div>
          <h1 className="text-[28px] font-black leading-8 tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-ink/65">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
