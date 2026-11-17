'use client';

import { useEffect } from 'react';

// Root-level error boundary (QShala priority plan P2-2). error.tsx only catches errors
// thrown inside the root layout's children -- if the root layout ITSELF throws (a provider,
// AppShell, etc.), only this file catches it. Next.js requires it to render its own
// <html>/<body> since it replaces the whole root layout when it fires.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('[GlobalErrorBoundary]', error);
  }, [error]);

  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col items-center justify-center bg-slate-50 p-8 text-center font-sans">
        <h2 className="text-xl font-semibold text-slate-900">Something went wrong</h2>
        <p className="mt-2 max-w-md text-sm text-slate-500">
          The app hit an unexpected error. Refreshing usually fixes it.
        </p>
        <button
          onClick={() => reset()}
          className="mt-6 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          Try again
        </button>
      </body>
    </html>
  );
}
