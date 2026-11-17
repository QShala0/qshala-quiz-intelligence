import Link from 'next/link';
import { QT, Doodles } from '../components/ui';

export default function NotFound() {
  return (
    <div className="relative flex min-h-[70vh] flex-col items-center justify-center overflow-hidden px-6 text-center">
      <Doodles opacity={0.14} />
      <div className="relative flex flex-col items-center gap-4">
        <div className="animate-peek">
          <QT pose="door-blue" height={170} alt="QT the cat peeking out from behind a door" />
        </div>
        <p className="text-sm font-black tracking-widest text-sky-600">ERROR 404</p>
        <h1 className="text-3xl font-black tracking-tight">QT looked everywhere. Nothing here.</h1>
        <p className="max-w-md text-sm text-ink/70">
          This page wandered off, or the link is a little off. Let&apos;s get you back to something real.
        </p>
        <Link
          href="/"
          className="qt-press mt-2 inline-flex h-11 items-center rounded-pill border-2 border-ink bg-sun px-6 text-sm font-bold shadow-sticker-sm hover:bg-sun-200"
        >
          Take me home
        </Link>
      </div>
    </div>
  );
}
