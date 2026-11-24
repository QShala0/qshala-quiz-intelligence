import QT from '../../components/ui/QT';

export default function Loading() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
      <QT pose="looking-up" height={56} motion="bob" alt="QT looking through quizzes" />
      <p className="text-sm font-bold text-ink/70">Loading quizzes…</p>
    </div>
  );
}
