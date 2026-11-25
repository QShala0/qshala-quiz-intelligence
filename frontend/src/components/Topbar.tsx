'use client';

import { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Search, ChevronDown, PanelLeftOpen, LogOut, ShieldCheck, User } from 'lucide-react';
import { useSidebar } from '../context/SidebarContext';
import { useAuth } from '../context/AuthContext';
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
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    if (menuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [menuOpen]);

  const initial = (user?.display_name || user?.username || 'U')[0].toUpperCase();

  return (
    <header className="sticky top-0 z-20 flex h-16 w-full items-center justify-between border-b border-slate-200 bg-slate-50/85 px-6 backdrop-blur-md sm:px-8">
      <div className="flex items-center gap-2.5">
        {isCollapsed && (
          <button
            onClick={toggleSidebar}
            type="button"
            className="-ml-1 mr-1 flex h-8 w-8 items-center justify-center rounded-lg text-slate-600 transition-colors hover:bg-amber-100/50 hover:text-slate-900"
            title="Expand sidebar (Ctrl+B)"
            aria-label="Expand sidebar"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        )}
        <span className="text-[13px] font-medium text-slate-500">QShala</span>
        <span className="text-[13px] text-slate-400">/</span>
        <span className="text-[13px] font-extrabold text-slate-900">{currentSection(pathname)}</span>
      </div>

      <div className="flex items-center gap-4">
        <Link
          href="/knowledge-base"
          className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-[#1E222D] bg-white text-[#1E222D] transition-colors hover:bg-[#F8B42C]"
          title="Search Questions"
          aria-label="Search questions"
        >
          <Search className="h-3.5 w-3.5" />
        </Link>

        {user && (
          <div className="relative pl-2" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((prev) => !prev)}
              className="flex items-center gap-2 rounded-full py-1 pl-1 pr-2.5 transition-colors hover:bg-slate-200/60 focus:outline-none focus:ring-2 focus:ring-amber-400"
              aria-expanded={menuOpen}
              aria-haspopup="true"
            >
              <div className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-[#1E222D] bg-[#5BB4DE] text-[12px] font-black text-[#1E222D]">
                {initial}
              </div>
              <div className="hidden text-left sm:block">
                <span className="block text-[13px] font-bold text-[#1E222D] leading-tight">
                  {user.display_name}
                </span>
                <span className="block text-[11px] font-medium text-slate-500 leading-tight">
                  {user.team || 'QShala'}
                </span>
              </div>
              <ChevronDown className="h-3.5 w-3.5 text-slate-500" />
            </button>

            {menuOpen && (
              <div className="absolute right-0 mt-2 w-56 rounded-xl border border-[#E9DFC9] bg-[#FFFCF5] p-2 shadow-lg shadow-amber-950/10 z-50">
                <div className="border-b border-[#E9DFC9]/80 px-3 py-2">
                  <p className="text-[13.5px] font-bold text-[#1E222D] truncate">
                    {user.display_name}
                  </p>
                  <p className="text-[11.5px] text-[#4B5263] truncate">
                    {user.email || user.username}
                  </p>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <span className="inline-flex items-center rounded-md bg-amber-100/70 px-2 py-0.5 text-[10.5px] font-semibold text-amber-900">
                      {user.team || 'No Team'}
                    </span>
                    {user.role === 'admin' ? (
                      <span className="inline-flex items-center gap-0.5 rounded-md bg-emerald-100/80 px-2 py-0.5 text-[10.5px] font-semibold text-emerald-900">
                        <ShieldCheck className="h-3 w-3" />
                        Admin
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-0.5 rounded-md bg-slate-100 px-2 py-0.5 text-[10.5px] font-medium text-slate-700">
                        <User className="h-3 w-3" />
                        Member
                      </span>
                    )}
                  </div>
                </div>

                <div className="pt-1">
                  <button
                    type="button"
                    onClick={async () => {
                      setMenuOpen(false);
                      await logout();
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-[13px] font-semibold text-red-700 transition-colors hover:bg-red-50 focus:outline-none"
                  >
                    <LogOut className="h-4 w-4" />
                    <span>Sign out</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}
