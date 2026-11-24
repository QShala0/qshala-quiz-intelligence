import QT from './QT';

interface LogoProps {
  /** Cat only (used when the sidebar is collapsed). */
  collapsed?: boolean;
  /** Show the "Questions • Quriosity • Quests" tagline under the wordmark. */
  tagline?: boolean;
  className?: string;
}

/** QShala lockup: QT + wordmark. The cat does a little wiggle on hover. */
export default function Logo({ collapsed = false, tagline = false, className = '' }: LogoProps) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <QT pose="sit" height={collapsed ? 30 : 34} alt="QT, the QShala cat" motion="wiggle" priority />
      {!collapsed && (
        <span className="flex flex-col leading-none">
          <span className="text-[22px] font-black tracking-tight text-slate-900">QSHALA</span>
          {tagline && (
            <span className="mt-1 text-[10px] font-semibold text-slate-600">Questions • Quriosity • Quests</span>
          )}
        </span>
      )}
    </span>
  );
}
