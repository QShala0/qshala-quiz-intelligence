'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  HardDrive,
  Cloud,
  Layers,
  ArrowRight,
} from 'lucide-react';
import { getApiBase } from '@/lib/api';
import CustomTemplateManager from '@/components/CustomTemplateManager';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'platform' | 'templates'>('platform');
  const [serverOnline, setServerOnline] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${getApiBase()}/health`)
      .then((res) => setServerOnline(res.ok))
      .catch(() => setServerOnline(false));
  }, []);

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-5xl mx-auto space-y-7 pb-20">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b-2 border-sun pb-5">
        <div>
          <h1 className="text-[28px] sm:text-[32px] font-black tracking-tight text-ink leading-tight">
            Settings & Admin
          </h1>
          <p className="mt-1 text-[13.5px] text-slate-500 font-normal">
            Configure system health, database connections, and custom presentation templates.
          </p>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/80">
          <button
            type="button"
            onClick={() => setActiveTab('platform')}
            className={`px-3.5 py-1.5 rounded-lg text-[13px] font-bold transition-all cursor-pointer ${
              activeTab === 'platform'
                ? 'bg-sky-100 text-ink shadow-sticker-sm'
                : 'text-slate-600 hover:text-ink'
            }`}
          >
            Platform Config
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('templates')}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[13px] font-bold transition-all cursor-pointer ${
              activeTab === 'templates'
                ? 'bg-sky-100 text-ink shadow-sticker-sm'
                : 'text-slate-600 hover:text-ink'
            }`}
          >
            <Layers className="h-3.5 w-3.5" />
            <span>PPT Templates</span>
          </button>
        </div>
      </div>

      {activeTab === 'platform' ? (
        <div className="space-y-6">
          {/* Storage & Engine Status */}
          <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm shadow-slate-100/50 space-y-4">
            <div className="flex items-center gap-2.5 border-b border-slate-100 pb-3">
              <HardDrive className="h-4 w-4 text-slate-600" />
              <h2 className="text-[16px] font-semibold text-slate-900">Database & System Health</h2>
            </div>

            <div className="divide-y divide-slate-100 text-[13px]">
              <div className="flex items-center justify-between py-2.5">
                <span className="text-slate-600 font-normal">Database</span>
                <span className="font-medium text-slate-800">Neon Postgres</span>
              </div>

              <div className="flex items-center justify-between py-2.5">
                <span className="text-slate-600 font-normal">Object storage</span>
                <span className="font-medium text-slate-800">Cloudflare R2</span>
              </div>

              <div className="flex items-center justify-between py-2.5">
                <span className="text-slate-600 font-normal">Server</span>
                <span className="inline-flex items-center gap-1.5 font-medium text-slate-800">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      serverOnline === false ? 'bg-rose-500' : 'bg-emerald-500'
                    }`}
                  />
                  Google Cloud Run (Singapore)
                </span>
              </div>
            </div>
          </div>

          {/* SharePoint Library & Import Info Card */}
          <div className="rounded-[20px] border border-sun/30 bg-white p-6 shadow-sticker-sm space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
              <div className="flex items-start sm:items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-sun text-ink font-bold shadow-xs">
                  <Cloud className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="text-[15px] font-bold text-ink">SharePoint Library & Import</h3>
                  <p className="text-[12.5px] text-slate-500 font-normal mt-0.5">
                    SharePoint importing and folder browsing has been integrated directly into the Upload Center for a unified ingestion experience.
                  </p>
                </div>
              </div>
              <Link
                href="/uploads?tab=sharepoint"
                className="inline-flex items-center gap-1.5 shrink-0 rounded-xl bg-sky-100 border border-sky-200 hover:bg-sky-200  px-4 py-2 text-[13px] font-bold text-ink transition-all cursor-pointer shadow-sticker-sm"
              >
                <span>Go to Upload Center</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>
        </div>
      ) : (
        <CustomTemplateManager />
      )}
    </div>
  );
}
