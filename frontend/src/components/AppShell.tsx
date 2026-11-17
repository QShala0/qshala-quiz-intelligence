'use client';

import React from 'react';
import { useSidebar } from '../context/SidebarContext';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { isCollapsed } = useSidebar();

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
