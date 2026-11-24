'use client';

import { useEffect } from 'react';
import { RefreshCcw } from 'lucide-react';
import { QT } from '../components/ui';

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
      <div className="mb-4">
        <QT
          pose="scared"
          height={68}
          interactive={true}
          speechBubble="Oops! Even QT stumbled on that one..."
          alt="Startled QT"
        />
      </div>
      <h2 className="text-2xl font-black text-ink">Something went a little sideways</h2>
      <p className="mt-2 max-w-md text-sm text-slate-600 font-normal">
        This view hit an unexpected hiccup. Don&apos;t worry — your questions and quizzes are safe in the vault.
      </p>
      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <button
          onClick={() => reset()}
          className="inline-flex items-center gap-2 rounded-xl bg-sun px-4 py-2 text-sm font-bold text-ink shadow-sticker-sm hover:brightness-105 active:scale-[0.99] transition-all cursor-pointer"
        >
          <RefreshCcw className="h-4 w-4" />
          Try again
        </button>
        <button
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-2 rounded-xl border-2 border-ink/20 bg-white px-4 py-2 text-sm font-bold text-ink hover:bg-slate-50 transition-all cursor-pointer"
        >
          Refresh page
        </button>
      </div>
    </div>
  );
}
