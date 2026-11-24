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
      <body className="flex min-h-screen flex-col items-center justify-center bg-[#FFF6E1] p-8 text-center text-[#000000] font-sans">
        <h2 className="text-2xl font-black">Something went wrong</h2>
        <p className="mt-2 max-w-md text-sm text-[#000000]/70">
          The app hit an unexpected error. Refreshing usually fixes it.
        </p>
        <button
          onClick={() => reset()}
          className="mt-6 rounded-xl bg-[#FDB913] px-5 py-2.5 text-sm font-bold text-[#000000] shadow-sm hover:brightness-105 active:scale-[0.99] transition-all cursor-pointer"
        >
          Try again
        </button>
      </body>
    </html>
  );
}
