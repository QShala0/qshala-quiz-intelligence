'use client';

import { useEffect } from 'react';
import { AlertTriangle, RefreshCcw } from 'lucide-react';

// Next.js App Router error boundary: catches any uncaught error thrown while rendering
// a page or its children, instead of taking down the whole app with a blank white screen
// (QShala priority plan P2-2). This file sits inside the root layout, so AppShell/nav
// stays visible and the person can navigate away even if this one page crashed.
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center p-8 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-red-50">
        <AlertTriangle className="h-7 w-7 text-red-600" />
      </div>
      <h2 className="text-xl font-semibold text-slate-900">Something went wrong</h2>
      <p className="mt-2 max-w-md text-sm text-slate-500">
        This page hit an unexpected error. Try again, or refresh the page if it keeps
        happening.
      </p>
      <div className="mt-6 flex gap-3">
        <button
          onClick={() => reset()}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          <RefreshCcw className="h-4 w-4" />
          Try again
        </button>
        <button
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Refresh page
        </button>
      </div>
    </div>
  );
}
