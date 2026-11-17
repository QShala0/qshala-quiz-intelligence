'use client';

import { useEffect, useState } from 'react';
import {
  Settings as SettingsIcon,
  Database,
  HardDrive,
  RefreshCw,
  Play,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  Clock,
  ExternalLink,
  FileText,
  Cloud,
  Layers
} from 'lucide-react';
import { api, getApiBase } from '@/lib/api';
import { SharePointSyncStatus, SharePointFileItem, IngestionJobItem } from '@/lib/types';
import SharePointImporter from '@/components/SharePointImporter';
import QCat from '@/components/mascot/QCat';

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<'platform' | 'sharepoint'>('platform');
  const [apiEndpoint, setApiEndpoint] = useState('/api/v1');

  // SharePoint & Jobs state
  const [syncStatus, setSyncStatus] = useState<SharePointSyncStatus | null>(null);
  const [files, setFiles] = useState<SharePointFileItem[]>([]);
  const [jobs, setJobs] = useState<IngestionJobItem[]>([]);
  const [loadingSync, setLoadingSync] = useState(false);
  const [loadingWorker, setLoadingWorker] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    setApiEndpoint(getApiBase());
  }, []);

  const loadSharePointData = async () => {
    try {
      const [sStatus, sFiles, sJobs] = await Promise.all([
        api.getSharePointStatus().catch(() => null),
        api.listSharePointFiles().then(res => res.files).catch(() => []),
        api.listJobs().then(res => res.jobs).catch(() => [])
      ]);
      setSyncStatus(sStatus);
      setFiles(sFiles);
      setJobs(sJobs);
    } catch (err) {
      console.error('Failed to load SharePoint admin data:', err);
    }
  };

  useEffect(() => {
    if (activeTab === 'sharepoint') {
      loadSharePointData();
    }
  }, [activeTab]);

  const handleTriggerSync = async () => {
    setLoadingSync(true);
    setActionMessage(null);
    try {
      const res = await api.triggerSharePointSync();
      setActionMessage(`Sync successful! Discovered ${res.discovered_files_count || 0} files, enqueued ${res.enqueued_jobs_count || 0} jobs.`);
      await loadSharePointData();
    } catch (err: any) {
      setActionMessage(`Sync error: ${err.message || String(err)}`);
    } finally {
      setLoadingSync(false);
    }
  };

  const handleProcessWorker = async () => {
    setLoadingWorker(true);
    setActionMessage(null);
    try {
      const res = await api.processNextJob();
      if (res.job) {
        setActionMessage(`Processed step for job: ${res.job.filename} -> Step: ${res.job.current_step} (${res.job.status})`);
      } else {
        setActionMessage('No pending jobs in queue.');
      }
      await loadSharePointData();
    } catch (err: any) {
      setActionMessage(`Worker error: ${err.message || String(err)}`);
    } finally {
      setLoadingWorker(false);
    }
  };

  const handleRetryJob = async (jobId: string) => {
    try {
      await api.retryJob(jobId);
      setActionMessage(`Job ${jobId.slice(0, 8)} reset to PENDING.`);
      await loadSharePointData();
    } catch (err: any) {
      setActionMessage(`Failed to retry job: ${err.message || String(err)}`);
    }
  };

  const handleRequeueFile = async (fileId: string) => {
    try {
      await api.requeueSharePointFile(fileId);
      setActionMessage(`File re-queued successfully.`);
      await loadSharePointData();
    } catch (err: any) {
      setActionMessage(`Failed to re-queue file: ${err.message || String(err)}`);
    }
  };

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-5xl mx-auto space-y-7 pb-20">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200/60 pb-5">
        <div>
          <h1 className="text-[28px] sm:text-[32px] font-bold tracking-tight text-slate-900 leading-tight">
            Settings & Admin
          </h1>
          <p className="mt-1 text-[14px] text-slate-500 font-normal leading-relaxed">
            Manage platform parameters, SharePoint delta sync daemon, and background job queue.
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
            onClick={() => setActiveTab('sharepoint')}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
              activeTab === 'sharepoint'
                ? 'bg-blue-600 text-white shadow-sm'
                : 'text-blue-700 hover:text-blue-800'
            }`}
          >
            <Cloud className="h-3.5 w-3.5" />
            <span>SharePoint Sync & Jobs</span>
          </button>
        </div>
      </div>

      {actionMessage && (
        <div className="rounded-lg bg-blue-50 border border-blue-200 p-3.5 text-[13px] text-blue-800 flex items-center justify-between">
          <span>{actionMessage}</span>
          <button
            type="button"
            onClick={() => setActionMessage(null)}
            className="text-blue-500 hover:text-blue-700 font-bold ml-3"
          >
            &times;
          </button>
        </div>
      )}

      {activeTab === 'platform' ? (
        <>

          {/* Storage & Engine Status */}
          <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm shadow-slate-100/50 space-y-4">
            <div className="flex items-center gap-2.5 border-b border-slate-100 pb-3">
              <HardDrive className="h-4 w-4 text-slate-600" />
              <h2 className="text-[16px] font-semibold text-slate-900">Database & System Health</h2>
            </div>

            <div className="divide-y divide-slate-100 text-[13px]">
              <div className="flex items-center justify-between py-2.5">
                <span className="text-slate-600 font-normal">Backend API Status</span>
                <span className="flex items-center gap-1.5 font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded text-[12px]">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  <span>Online ({apiEndpoint})</span>
                </span>
              </div>

              <div className="flex items-center justify-between py-2.5">
                <span className="text-slate-600 font-normal">Object Storage</span>
                <span className="font-mono text-slate-700 text-[12px]">Vercel Blob (blob.vercel-storage.com v7)</span>
              </div>

              <div className="flex items-center justify-between py-2.5">
                <span className="text-slate-600 font-normal">Platform Version</span>
                <div className="flex items-center gap-2">
                  <QCat pose="idle" size={26} alt="Professor QCat mascot" />
                  <span className="font-mono text-slate-700 text-[12px]">v2.0.0 (QShala Intelligence)</span>
                </div>
              </div>
            </div>
          </div>
        </>
      ) : (
        /* SharePoint Sync & Jobs Admin Dashboard */
        <div className="space-y-6">
          <SharePointImporter />
          {/* Sync Overview Card */}
          <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm shadow-slate-100/50 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-100 pb-4">
              <div className="flex items-center gap-2.5">
                <Cloud className="h-5 w-5 text-blue-600" />
                <div>
                  <h2 className="text-[16px] font-bold text-slate-900">SharePoint Library Delta Sync</h2>
                  <p className="text-[12px] text-slate-500">Microsoft Graph API incremental synchronization</p>
                </div>
              </div>

              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  disabled={loadingSync}
                  onClick={handleTriggerSync}
                  className="rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 px-3.5 py-2 text-[13px] font-semibold text-white transition-colors cursor-pointer flex items-center gap-1.5"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${loadingSync ? 'animate-spin' : ''}`} />
                  <span>{loadingSync ? 'Syncing...' : 'Sync Now'}</span>
                </button>

                <button
                  type="button"
                  disabled={loadingWorker}
                  onClick={handleProcessWorker}
                  className="rounded-lg border border-slate-200 hover:bg-slate-50 disabled:opacity-50 px-3.5 py-2 text-[13px] font-semibold text-slate-700 transition-colors cursor-pointer flex items-center gap-1.5"
                >
                  <Play className={`h-3.5 w-3.5 text-emerald-600 ${loadingWorker ? 'animate-pulse' : ''}`} />
                  <span>{loadingWorker ? 'Running...' : 'Process Next Job'}</span>
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-[13px]">
              <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3.5 space-y-1">
                <span className="text-[12px] font-semibold text-slate-500">Daemon Status</span>
                <div className="flex items-center gap-1.5 font-bold text-[18px] text-slate-900">
                  {syncStatus?.status === 'SYNCING' ? (
                    <span className="text-blue-600 flex items-center gap-1">
                      <RefreshCw className="h-4 w-4 animate-spin" /> Syncing
                    </span>
                  ) : syncStatus?.status === 'FAILED' ? (
                    <span className="text-red-600 flex items-center gap-1">
                      <AlertCircle className="h-4 w-4" /> Failed
                    </span>
                  ) : (
                    <span className="text-emerald-700 flex items-center gap-1">
                      <CheckCircle2 className="h-4 w-4 text-emerald-500" /> Idle / Ready
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-slate-400">Library: {syncStatus?.drive_id ? syncStatus.drive_id.slice(0, 12) + '...' : 'Default Library'}</p>
              </div>

              <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3.5 space-y-1">
                <span className="text-[12px] font-semibold text-slate-500">Tracked Files</span>
                <div className="text-[22px] font-bold text-slate-900">
                  {syncStatus?.total_files_tracked ?? files.length}
                </div>
                <p className="text-[11px] text-slate-400">Filtered to .pptx and .pdf only</p>
              </div>

              <div className="rounded-lg border border-slate-100 bg-slate-50/50 p-3.5 space-y-1">
                <span className="text-[12px] font-semibold text-slate-500">Last Delta Sync</span>
                <div className="text-[14px] font-semibold text-slate-800 mt-1">
                  {syncStatus?.last_sync_at ? new Date(syncStatus.last_sync_at).toLocaleString() : 'Never'}
                </div>
                <p className="text-[11px] text-slate-400">Incremental via deltaLink</p>
              </div>
            </div>
          </div>

          {/* Ingestion Jobs Queue Table */}
          <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden space-y-0">
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-blue-600" />
                <h3 className="text-[15px] font-bold text-slate-900">
                  Background Ingestion Jobs ({jobs.length})
                </h3>
              </div>
              <button
                type="button"
                onClick={loadSharePointData}
                className="text-[12px] text-blue-600 hover:underline font-medium cursor-pointer"
              >
                Refresh Queue
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px] border-collapse">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50/60 font-semibold text-[12px] uppercase tracking-wider text-slate-600">
                    <th className="py-3 px-4">Filename</th>
                    <th className="py-3 px-4">Status</th>
                    <th className="py-3 px-4">Current Step</th>
                    <th className="py-3 px-4">Retries</th>
                    <th className="py-3 px-4">Progress / Summary</th>
                    <th className="py-3 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {jobs.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-8 text-center text-slate-400">
                        No ingestion jobs queued yet. Trigger a sync or upload a deck to get started.
                      </td>
                    </tr>
                  ) : (
                    jobs.map((job) => (
                      <tr key={job.id} className="hover:bg-slate-50/70 transition-colors">
                        <td className="py-3 px-4 font-semibold text-slate-900">
                          <div className="flex items-center gap-1.5">
                            <FileText className="h-4 w-4 text-slate-400" />
                            <span>{job.filename}</span>
                          </div>
                        </td>

                        <td className="py-3 px-4">
                          <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-bold ${
                            job.status === 'COMPLETED'
                              ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                              : job.status === 'FAILED'
                              ? 'bg-red-50 text-red-700 border border-red-200'
                              : job.status === 'PROCESSING'
                              ? 'bg-blue-50 text-blue-700 border border-blue-200 animate-pulse'
                              : 'bg-amber-50 text-amber-700 border border-amber-200'
                          }`}>
                            {job.status}
                          </span>
                        </td>

                        <td className="py-3 px-4 font-mono text-[11.5px] text-slate-600">
                          {job.current_step}
                        </td>

                        <td className="py-3 px-4 text-slate-600">
                          {job.retry_count} / {job.max_retries}
                        </td>

                        <td className="py-3 px-4 text-[11.5px] text-slate-500">
                          {job.step_summary ? (
                            <span>
                              {job.step_summary.slides_count} slides | {job.step_summary.questions_count} Qs | {job.step_summary.media_count} media
                            </span>
                          ) : (
                            job.error_message ? (
                              <span className="text-red-500 truncate max-w-[200px] block" title={job.error_message}>
                                {job.error_message}
                              </span>
                            ) : '-'
                          )}
                        </td>

                        <td className="py-3 px-4 text-right">
                          {job.status === 'FAILED' ? (
                            <button
                              type="button"
                              onClick={() => handleRetryJob(job.id)}
                              className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white hover:bg-slate-50 px-2 py-1 text-[11px] font-semibold text-blue-600 cursor-pointer"
                            >
                              <RotateCcw className="h-3 w-3" />
                              <span>Retry</span>
                            </button>
                          ) : job.status === 'PENDING' || job.status === 'PROCESSING' ? (
                            <button
                              type="button"
                              onClick={handleProcessWorker}
                              className="inline-flex items-center gap-1 rounded bg-blue-50 border border-blue-200 hover:bg-blue-100 px-2 py-1 text-[11px] font-semibold text-blue-700 cursor-pointer"
                            >
                              <Play className="h-3 w-3 text-blue-600" />
                              <span>Step</span>
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* SharePoint Files Tracked */}
          <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden space-y-0">
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Cloud className="h-4 w-4 text-blue-600" />
                <h3 className="text-[15px] font-bold text-slate-900">
                  Tracked Files from Document Library ({files.length})
                </h3>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px] border-collapse">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50/60 font-semibold text-[12px] uppercase tracking-wider text-slate-600">
                    <th className="py-3 px-4">Filename</th>
                    <th className="py-3 px-4">Size</th>
                    <th className="py-3 px-4">Modified</th>
                    <th className="py-3 px-4">Sync Status</th>
                    <th className="py-3 px-4">Blob Location</th>
                    <th className="py-3 px-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {files.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-8 text-center text-slate-400">
                        No SharePoint files discovered yet. Click "Sync Now" above to pull from SharePoint.
                      </td>
                    </tr>
                  ) : (
                    files.map((file) => (
                      <tr key={file.id} className="hover:bg-slate-50/70 transition-colors">
                        <td className="py-3 px-4 font-semibold text-slate-900">
                          {file.name}
                        </td>

                        <td className="py-3 px-4 text-slate-600">
                          {(file.file_size_bytes / (1024 * 1024)).toFixed(1)} MB
                        </td>

                        <td className="py-3 px-4 text-slate-500 text-[12px]">
                          {file.last_modified_date_time ? new Date(file.last_modified_date_time).toLocaleDateString() : '-'}
                        </td>

                        <td className="py-3 px-4">
                          <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold ${
                            file.sync_status === 'COMPLETED'
                              ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                              : file.sync_status === 'FAILED'
                              ? 'bg-red-50 text-red-700 border border-red-200'
                              : 'bg-amber-50 text-amber-700 border border-amber-200'
                          }`}>
                            {file.sync_status}
                          </span>
                        </td>

                        <td className="py-3 px-4 text-[11.5px]">
                          {file.blob_url ? (
                            <a
                              href={file.blob_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-blue-600 hover:underline flex items-center gap-1 max-w-[160px] truncate"
                            >
                              <span className="truncate">{file.blob_url}</span>
                              <ExternalLink className="h-3 w-3 flex-shrink-0" />
                            </a>
                          ) : (
                            <span className="text-slate-400">Pending upload</span>
                          )}
                        </td>

                        <td className="py-3 px-4 text-right">
                          {file.sync_status === 'FAILED' && (
                            <button
                              type="button"
                              onClick={() => handleRequeueFile(file.id)}
                              className="rounded border border-slate-200 bg-white hover:bg-slate-50 px-2 py-1 text-[11px] font-semibold text-blue-600 cursor-pointer"
                            >
                              Re-queue
                            </button>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
