import Link from 'next/link';
import QCat from '../components/mascot/QCat';

export default function NotFound() {
  return (
    <div className="relative flex min-h-[75vh] flex-col items-center justify-center overflow-hidden px-6 text-center">
      <div className="relative flex flex-col items-center max-w-md">
        {/* Cat sitting on a shelf with knocked-over vase illustration */}
        <div className="relative mb-6 w-56 h-36 flex flex-col items-center justify-end">
          {/* Tilted / knocked over vase */}
          <div className="absolute left-6 bottom-5 -rotate-45 opacity-90 transition-transform">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 4h8v4l4 8v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V16l4-8V4z" fill="#5BB4DE" stroke="#2B3040" strokeWidth="2" strokeLinejoin="round" />
              <path d="M10 16h12" stroke="#FFFCF5" strokeWidth="1.5" strokeDasharray="2 2" />
              {/* Droplets / flower falling */}
              <circle cx="28" cy="8" r="2" fill="#F8B42C" />
              <circle cx="25" cy="2" r="1.5" fill="#5BB4DE" />
            </svg>
          </div>

          {/* Innocent Cat sitting on shelf */}
          <div className="absolute right-8 bottom-4">
            <QCat pose="paw" size={80} alt="Cat sitting on knocked shelf" />
          </div>

          {/* Wooden Shelf */}
          <div className="w-full h-3 rounded-full bg-slate-300 border border-slate-400/60 shadow-xs" />
        </div>

        <p className="text-xs font-bold uppercase tracking-wider text-amber-600 mb-1">Error 404</p>
        <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900 mb-2">
          Looks like this page got pushed off the edge.
        </h1>
        <p className="text-sm text-slate-500 leading-relaxed mb-6">
          Curiosity got the better of the cat. The page you are looking for tumbled into the void, or the URL might be a little off.
        </p>

        <Link
          href="/"
          className="inline-flex h-11 items-center justify-center rounded-xl bg-slate-900 px-6 text-sm font-semibold text-white transition hover:bg-slate-800 shadow-xs"
        >
          Return to Dashboard
        </Link>
      </div>
    </div>
  );
}
