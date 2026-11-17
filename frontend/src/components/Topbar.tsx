'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Search, ChevronDown, PanelLeftOpen } from 'lucide-react';
import { useSidebar } from '../context/SidebarContext';
import { NAV_ITEMS } from './Sidebar';

/** Live page name for the breadcrumb (falls back to the nearest nav section). */
function currentSection(pathname: string): string {
  if (pathname.startsWith('/design')) return 'Brand Kit';
  const hit = NAV_ITEMS.filter((n) => (n.href === '/' ? pathname === '/' : pathname.startsWith(n.href))).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
  return hit?.label ?? 'Workspace';
}

export default function Topbar() {
  const { isCollapsed, toggleSidebar } = useSidebar();
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-20 flex h-16 w-full items-center justify-between border-b-2 border-ink/10 bg-cream/85 px-6 backdrop-blur-md sm:px-8">
      <div className="flex items-center gap-2.5">
        {isCollapsed && (
          <button
            onClick={toggleSidebar}
            type="button"
            className="-ml-1 mr-1 flex h-8 w-8 items-center justify-center rounded-lg text-ink/55 transition-colors hover:bg-sun-50 hover:text-ink"
            title="Expand sidebar (Ctrl+B)"
            aria-label="Expand sidebar"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        )}
        <span className="text-[13px] font-medium text-ink/50">QShala</span>
        <span className="text-[13px] text-ink/30">/</span>
        <span className="text-[13px] font-extrabold text-ink">{currentSection(pathname)}</span>
      </div>

      <div className="flex items-center gap-4">
        <Link
          href="/knowledge-base"
          className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-ink bg-white text-ink transition-colors hover:bg-sun"
          title="Search Questions"
          aria-label="Search questions"
        >
          <Search className="h-3.5 w-3.5" />
        </Link>

        <div className="flex items-center gap-2.5 pl-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-ink bg-sky text-[12px] font-black text-ink">
            Q
          </div>
          <button className="flex items-center gap-1.5 text-[13px] font-bold text-ink transition-colors hover:text-ink/70">
            <span>QShala Team</span>
            <ChevronDown className="h-3.5 w-3.5 text-ink/45" />
          </button>
        </div>
      </div>
    </header>
  );
}
