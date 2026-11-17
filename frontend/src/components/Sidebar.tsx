'use client';

import { useRef } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  Home,
  BookOpen,
  Sparkles,
  FileText,
  UploadCloud,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react';
import { useSidebar } from '../context/SidebarContext';
import { Logo, QT, useToast } from './ui';

// QT's one-liners: shown when you click the logo while already on the dashboard.
const QUIPS = [
  "Meow-ve over, I'm quizzing.",
  'Curiosity did not kill this cat.',
  'Paws for thought…',
  'I have a purr-fect question for you.',
  'Fur-tunately, the answer is in the vault.',
  'Ask me anything. Well, mostly quiz things.',
];

export const NAV_ITEMS = [
  { label: 'Dashboard', href: '/', icon: Home },
  { label: 'Questions', href: '/knowledge-base', icon: BookOpen },
  { label: 'Create Quiz', href: '/generate', icon: Sparkles },
  { label: 'Generated Quizzes', href: '/quizzes', icon: FileText },
  { label: 'Uploads', href: '/uploads', icon: UploadCloud },
  { label: 'Settings', href: '/settings', icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();
  const { isCollapsed, toggleSidebar } = useSidebar();
  const toast = useToast();
  const quipIndex = useRef(0);

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-30 flex flex-col border-r border-slate-200 bg-white transition-all duration-300 ease-in-out ${
        isCollapsed ? 'w-16' : 'w-52'
      }`}
    >
      {/* Brand header */}
      <div className={`flex h-16 items-center ${isCollapsed ? 'justify-center px-2' : 'justify-between px-4'}`}>
        <Link
          href="/"
          className="overflow-hidden"
          title="QShala"
          aria-label="QShala home"
          onClick={(e) => {
            // Already home? Let QT say something instead of a no-op navigation.
            if (pathname === '/') {
              e.preventDefault();
              toast.info(QUIPS[quipIndex.current++ % QUIPS.length]);
            }
          }}
        >
          <Logo collapsed={isCollapsed} />
        </Link>

        {!isCollapsed && (
          <button
            onClick={toggleSidebar}
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-amber-100/50 hover:text-slate-900"
            title="Collapse sidebar (Ctrl+B)"
            aria-label="Collapse sidebar"
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className={`flex-1 space-y-1 py-4 ${isCollapsed ? 'px-2' : 'px-3'}`} aria-label="Main">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.href === '/' ? pathname === '/' : pathname === item.href || pathname.startsWith(`${item.href}/`);

          return (
            <Link
              key={item.href}
              href={item.href}
              title={isCollapsed ? item.label : undefined}
              aria-current={isActive ? 'page' : undefined}
              className={`group flex items-center rounded-xl border transition-all ${
                isCollapsed ? 'h-10 w-full justify-center px-0' : 'gap-2.5 px-3 py-2 text-[13.5px] whitespace-nowrap'
              } ${
                isActive
                  ? 'border-amber-400 bg-amber-100/70 font-bold text-slate-900 shadow-xs'
                  : 'border-transparent font-medium text-slate-700 hover:bg-amber-50 hover:text-slate-900'
              }`}
            >
              <Icon
                className={`h-4 w-4 shrink-0 transition-transform group-hover:scale-105 ${
                  isActive ? 'text-slate-900' : 'text-slate-500 group-hover:text-slate-900'
                }`}
              />
              {!isCollapsed && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      {isCollapsed && (
        <div className="flex justify-center pb-2">
          <button
            onClick={toggleSidebar}
            type="button"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-amber-100/50 hover:text-slate-900"
            title="Expand sidebar (Ctrl+B)"
            aria-label="Expand sidebar"
          >
            <PanelLeftOpen className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Footer: a sleepy QT and the tagline */}
      <div className={`border-t border-slate-200 ${isCollapsed ? 'flex justify-center p-2' : 'flex items-end gap-2 px-4 py-3'}`}>
        <QT pose="sleepy" height={isCollapsed ? 24 : 38} motion="breathe" />
        {!isCollapsed && (
          <p className="pb-0.5 text-[11px] font-bold leading-tight text-slate-500">
            Questions<br />
            Quriosity<br />
            Quests
          </p>
        )}
      </div>
    </aside>
  );
}
