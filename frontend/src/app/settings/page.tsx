'use client';

import { useEffect, useState } from 'react';
import {
  HardDrive,
  Layers,
  Shield,
} from 'lucide-react';
import { getApiBase } from '@/lib/api';
import { useIsAdmin } from '@/context/AuthContext';
import CustomTemplateManager from '@/components/CustomTemplateManager';
import AccessTab from '@/components/settings/AccessTab';
import CostPanel from '@/components/CostPanel';

export default function SettingsPage() {
  const isAdmin = useIsAdmin();
  const [activeTab, setActiveTab] = useState<'platform' | 'templates' | 'access'>('platform');
  const [serverOnline, setServerOnline] = useState<boolean | null>(null);

  useEffect(() => {
    fetch(`${getApiBase()}/health`)
      .then((res) => setServerOnline(res.ok))
      .catch(() => setServerOnline(false));
  }, []);

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-5xl mx-auto space-y-7 pb-20">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200/60 pb-5">
        <div>
          <h1 className="text-[28px] sm:text-[32px] font-bold tracking-tight text-slate-900 leading-tight">
            Settings & Admin
          </h1>
          <p className="mt-1 text-[13.5px] text-slate-500 font-normal">
            Configure system health, database connections, and custom presentation templates.
          </p>
        </div>

        {/* Tab Navigation */}
        <div className="flex items-center bg-slate-100 p-1 rounded-lg border border-slate-200/80">
          <button
            type="button"
            onClick={() => setActiveTab('platform')}
            className={`px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
              activeTab === 'platform'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            Platform Config
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('templates')}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
              activeTab === 'templates'
                ? 'bg-amber-600 text-white shadow-sm'
                : 'text-amber-800 hover:text-amber-900'
            }`}
          >
            <Layers className="h-3.5 w-3.5" />
            <span>PPT Templates</span>
          </button>

          {isAdmin && (
            <button
              type="button"
              onClick={() => setActiveTab('access')}
              className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
                activeTab === 'access'
                  ? 'bg-[#1E222D] text-[#FFFCF5] shadow-sm'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Shield className="h-3.5 w-3.5" />
              <span>Access & Team</span>
            </button>
          )}
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

          {isAdmin && <CostPanel />}
        </div>
      ) : activeTab === 'templates' ? (
        <CustomTemplateManager />
      ) : (
        <AccessTab />
      )}
    </div>
  );
}
