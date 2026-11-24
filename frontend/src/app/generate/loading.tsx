import QT from '../../components/ui/QT';

export default function Loading() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
      <QT pose="quizzing" height={56} motion="bob" alt="QT thinking" />
      <p className="text-sm font-bold text-ink/70">Loading quiz engine…</p>
    </div>
  );
}
