'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Sparkles, SlidersHorizontal } from 'lucide-react';

export default function GenerateLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isClassic = pathname === '/generate/classic';

  return (
    <div className="w-full">
      <div className="mx-auto max-w-4xl px-5 pt-5 pb-1 flex justify-end">
        <div className="inline-flex items-center rounded-xl bg-slate-100 p-1 border border-slate-200/80 shadow-xs">
          <Link
            href="/generate"
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] font-semibold transition-all ${
              !isClassic
                ? 'bg-white text-slate-900 shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 text-amber-500" />
            AI Prompt Mode
          </Link>
          <Link
            href="/generate/classic"
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] font-semibold transition-all ${
              isClassic
                ? 'bg-white text-slate-900 shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            <SlidersHorizontal className="w-3.5 h-3.5 text-blue-600" />
            Classic Studio Form
          </Link>
        </div>
      </div>
      {children}
    </div>
  );
}
