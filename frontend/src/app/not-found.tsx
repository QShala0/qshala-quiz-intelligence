import Link from 'next/link';
import QT from '../components/ui/QT';

export default function NotFound() {
  return (
    <div className="relative flex min-h-[75vh] flex-col items-center justify-center overflow-hidden px-6 text-center">
      <div className="relative flex flex-col items-center max-w-md">
        {/* Official QT peeking pose */}
        <div className="relative mb-6 flex flex-col items-center justify-center">
          <QT
            pose="peeking"
            height={96}
            interactive={true}
            motion="breathe"
            speechBubble="I peeked everywhere, but this page isn't here!"
            bubblePosition="top"
            alt="QT curiously peeking around the corner"
          />
        </div>

        <p className="text-xs font-bold uppercase tracking-wider text-sun-600 mb-1">Error 404</p>
        <h1 className="text-2xl sm:text-3xl font-black tracking-tight text-ink mb-2">
          Looks like this page took a curiosity detour.
        </h1>
        <p className="text-sm text-slate-600 leading-relaxed mb-6">
          QT looked around every corner, but couldn&apos;t find this page. The link might be a little off, or the question was moved into another quest.
        </p>

        <Link
          href="/"
          className="inline-flex h-11 items-center justify-center rounded-xl bg-sun px-6 text-sm font-bold text-ink transition hover:bg-sun-600 shadow-sticker-sm"
        >
          Return to Dashboard
        </Link>
      </div>
    </div>
  );
}
