import { AlertTriangle, RefreshCw } from 'lucide-react';

// One consistent "this failed, here is why, try again" message, so a failed load never looks like an empty list.
export default function LoadErrorBanner({
  title,
  message,
  onRetry,
  note,
}: {
  title: string;
  message?: string | null;
  onRetry?: () => void;
  note?: string;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-3 text-[13.5px] text-amber-900"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
        <div>
          <span className="font-semibold">{title}</span>
          {message ? <span className="text-amber-800"> {message}</span> : null}
          {note ? <div className="mt-0.5 text-[12.5px] text-amber-800/90">{note}</div> : null}
        </div>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 self-start sm:self-auto rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[12.5px] font-medium text-amber-900 hover:bg-amber-100/70 transition-colors cursor-pointer"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </button>
      )}
    </div>
  );
}
