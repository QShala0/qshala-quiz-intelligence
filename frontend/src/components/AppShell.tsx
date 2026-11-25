'use client';

import React from 'react';
import { usePathname } from 'next/navigation';
import { useSidebar } from '../context/SidebarContext';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { isCollapsed } = useSidebar();
  const pathname = usePathname();

  const isAuthPage =
    pathname === '/login' ||
    pathname.startsWith('/login/') ||
    pathname === '/pending' ||
    pathname.startsWith('/pending/');

  if (isAuthPage) {
    return <main className="min-h-screen w-full bg-[#FBF7EE]">{children}</main>;
  }

  return (
    <div className="flex min-h-screen w-full bg-slate-50">
      <Sidebar />
      <div
        className={`flex-1 flex flex-col min-w-0 transition-all duration-300 ease-in-out ${
          isCollapsed ? 'pl-16' : 'pl-52'
        }`}
      >
        <Topbar />
        <main className="flex-1">{children}</main>
      </div>
    </div>
  );
}
