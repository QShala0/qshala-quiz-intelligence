'use client';



import { useEffect, useState } from 'react';

import {
  HardDrive,
  RefreshCw,
  Play,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  FileText,
  Cloud,
  Layers,
  X
} from 'lucide-react';
import { api, getApiBase } from '@/lib/api';
import { SharePointSyncStatus, SharePointFileItem, IngestionJobItem, SpNotImportedResponse } from '@/lib/types';
import SharePointImporter from '@/components/SharePointImporter';
import CustomTemplateManager from '@/components/CustomTemplateManager';
import { useToast } from '@/components/ui/Feedback';

export default function SettingsPage() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<'platform' | 'sharepoint' | 'templates'>('platform');
  const [serverOnline, setServerOnline] = useState<boolean | null>(null);

  // SharePoint & Jobs state
  const [syncStatus, setSyncStatus] = useState<SharePointSyncStatus | null>(null);
  const [files, setFiles] = useState<SharePointFileItem[]>([]);
  const [jobs, setJobs] = useState<IngestionJobItem[]>([]);
  const [loadingSync, setLoadingSync] = useState(false);
  const [loadingWorker, setLoadingWorker] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Not in platform yet drawer state
  const [notImportedData, setNotImportedData] = useState<SpNotImportedResponse | null>(null);
  const [loadingNotImported, setLoadingNotImported] = useState(false);
  const [notImportedError, setNotImportedError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [importingDrawer, setImportingDrawer] = useState(false);
  const [drawerImportProgress, setDrawerImportProgress] = useState<{ total: number; remaining: number } | null>(null);

  useEffect(() => {
    fetch(`${getApiBase()}/health`)
      .then((res) => setServerOnline(res.ok))
      .catch(() => setServerOnline(false));
  }, []);

  const loadSharePointData = async () => {
    try {
      const [sStatus, sFiles, sJobs] = await Promise.all([
        api.getSharePointStatus().catch(() => null),
        api.listSharePointFiles().then(res => res.files).catch(() => []),
        api.getJobs(undefined, 'MANUAL_UPLOAD').then(res => res.jobs).catch(() => [])
      ]);
      setSyncStatus(sStatus);
      setFiles(sFiles);
      setJobs(sJobs);

    } catch (err) {
      console.error('Failed to load SharePoint admin data:', err);
    }
  };

  const handleCheckNotImported = async (forceRefresh = false) => {
    setLoadingNotImported(true);
    setNotImportedError(null);
    try {
      const res = await api.spNotImported(undefined, forceRefresh);
      setNotImportedData(res);
      setSelectedIds(new Set(res.files.map((f) => f.id)));
      setDrawerOpen(true);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to check SharePoint files';
      setNotImportedError(msg);
      setDrawerOpen(true);
    } finally {
      setLoadingNotImported(false);
    }
  };

  const handleImportSelected = async () => {
    if (!notImportedData || selectedIds.size === 0 || importingDrawer) return;
    const selectedFiles = notImportedData.files.filter((f) => selectedIds.has(f.id));
    if (selectedFiles.length === 0) return;

    setImportingDrawer(true);
    const driveId = selectedFiles[0].drive_id;
    try {
      const batchFiles = selectedFiles.map((f) => ({ id: f.id, kind: 'file' as const }));
      const staged = await api.spImport({
        drive_id: driveId,
        items: batchFiles,
        recursive: false,
      });

      let remaining = staged.selected;
      setDrawerImportProgress({ total: staged.selected, remaining });

      while (remaining > 0) {
        const step = await api.spTransferNext(staged.batch_id, 25);
        remaining = step.remaining;
        setDrawerImportProgress({ total: staged.selected, remaining });
        if (step.queued + step.skipped_duplicate + step.failed === 0) break;
      }

      toast.success(`Import complete! Transferred ${staged.selected} files.`);
      await Promise.all([
        handleCheckNotImported(true),
        loadSharePointData(),
      ]);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImportingDrawer(false);
      setDrawerImportProgress(null);
    }
  };



  useEffect(() => {
    if (activeTab === 'sharepoint') {
      let isSubscribed = true;
      (async () => {
        try {
          const [sStatus, sFiles, sJobs] = await Promise.all([
            api.getSharePointStatus().catch(() => null),
            api.listSharePointFiles().then(res => res.files).catch(() => []),
            api.getJobs(undefined, 'MANUAL_UPLOAD').then(res => res.jobs).catch(() => [])
          ]);
          if (isSubscribed) {
            setSyncStatus(sStatus);
            setFiles(sFiles);
            setJobs(sJobs);
          }
        } catch (err) {
          console.error('Failed to load SharePoint admin data:', err);
        }
      })();
      return () => {
        isSubscribed = false;
      };
    }
  }, [activeTab]);

  const handleTriggerSync = async () => {
    setLoadingSync(true);
    setActionMessage(null);
    try {
      const res = await api.triggerSharePointSync();
      setActionMessage(`Sync successful! Discovered ${res.discovered_files_count || 0} files, enqueued ${res.enqueued_jobs_count || 0} jobs.`);
      await loadSharePointData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Sync error: ${msg}`);
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
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Worker error: ${msg}`);
    } finally {
      setLoadingWorker(false);
    }
  };

  const handleRetryJob = async (jobId: string) => {
    try {
      await api.retryJob(jobId);
      setActionMessage(`Job ${jobId.slice(0, 8)} reset to PENDING.`);
      await loadSharePointData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Failed to retry job: ${msg}`);
    }
  };

  const handleRequeueFile = async (fileId: string) => {
    try {
      await api.requeueSharePointFile(fileId);
      setActionMessage(`File re-queued successfully.`);
      await loadSharePointData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Failed to re-queue file: ${msg}`);
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

        </>

      ) : activeTab === 'sharepoint' ? (

        /* SharePoint Sync & Jobs Admin Dashboard */

        <div className="space-y-6">

          <SharePointImporter />

          {/* Sync Overview Card */}

          <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm shadow-slate-100/50 space-y-4">

            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-100 pb-4">

              <div className="flex items-center gap-2.5">
                <Cloud className="h-5 w-5 text-blue-600" />
                <div>
                  <h2 className="text-[16px] font-bold text-slate-900">SharePoint Sync</h2>
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
                <span className="text-[12px] font-semibold text-slate-500">Status</span>
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
                <span className="text-[12px] font-semibold text-slate-500">Last synced</span>
                <div className="text-[14px] font-semibold text-slate-800 mt-1">
                  {syncStatus?.last_sync_at ? new Date(syncStatus.last_sync_at).toLocaleString() : 'Never'}
                </div>
              </div>
            </div>

            {/* Not in platform yet */}
            <div className="flex items-center justify-between pt-3 border-t border-slate-100 text-[13px]">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-700">Not in platform yet</span>
                {notImportedData !== null && (
                  <span className="rounded-full bg-slate-100 border border-slate-200 px-2 py-0.5 text-[11px] font-bold text-slate-700">
                    {notImportedData.count}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => handleCheckNotImported(false)}
                disabled={loadingNotImported}
                className="rounded-lg border border-slate-200 hover:bg-slate-50 px-3 py-1.5 text-[12px] font-semibold text-slate-700 transition-colors cursor-pointer flex items-center gap-1.5"
              >
                {loadingNotImported ? (
                  <>
                    <RefreshCw className="h-3.5 w-3.5 animate-spin text-slate-500" />
                    <span>Checking...</span>
                  </>
                ) : (
                  <span>Check</span>
                )}
              </button>
            </div>

          </div>



          {/* Ingestion Jobs Queue Table */}
          <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden space-y-0">
            <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-blue-600" />
                <h3 className="text-[15px] font-bold text-slate-900">
                  Manually uploaded ({jobs.length})
                </h3>
              </div>
              <button
                type="button"
                onClick={loadSharePointData}
                className="text-[12px] text-blue-600 hover:underline font-medium cursor-pointer"
              >
                Refresh
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px] border-collapse">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50/60 font-semibold text-[12px] uppercase tracking-wider text-slate-600">
                    <th className="py-3 px-4">Filename</th>
                    <th className="py-3 px-4">Added</th>
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
                      <td colSpan={7} className="py-8 text-center text-slate-400">
                        No manually uploaded decks yet.
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
                        <td className="py-3 px-4 text-slate-500 whitespace-nowrap text-[12px]">
                          {job.created_at ? new Date(job.created_at).toLocaleString() : '—'}
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

                          {job.step_summary && (job.step_summary.slides_count > 0 || job.step_summary.questions_count > 0) ? (
                            <span>
                              {job.step_summary.slides_count} slides | {job.step_summary.questions_count} Qs{job.step_summary.media_count > 0 ? ` | ${job.step_summary.media_count} media` : ''}
                            </span>
                          ) : job.status === 'COMPLETED' ? (
                            <span className="text-emerald-700 font-semibold inline-flex items-center gap-1">
                              ✓ Ingested
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
          {(() => {
            const importedFiles = files.filter(
              (f) => f.sync_status === 'QUEUED' || f.sync_status === 'COMPLETED' || f.sync_status === 'FAILED'
            );
            return (
              <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden space-y-0">
                <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Cloud className="h-4 w-4 text-blue-600" />
                    <h3 className="text-[15px] font-bold text-slate-900">
                      Imported from SharePoint ({importedFiles.length})
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
                        <th className="py-3 px-4">File link</th>
                        <th className="py-3 px-4 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {importedFiles.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="py-8 text-center text-slate-400">
                            Nothing imported from SharePoint yet.
                          </td>
                        </tr>
                      ) : (
                        importedFiles.map((file) => (
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
            );
          })()}

        </div>

      ) : (

        <CustomTemplateManager />

      )}

      {/* Right-side Drawer: Not in platform yet */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 overflow-hidden">
          <div
            className="fixed inset-0 bg-slate-900/30 transition-opacity backdrop-blur-xs"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="fixed inset-y-0 right-0 flex max-w-full pl-10">
            <div className="w-[380px] max-w-full bg-white shadow-2xl border-l border-slate-200 flex flex-col z-50">
              {/* Header */}
              <div className="p-4 border-b border-slate-100 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="text-[15px] font-bold text-slate-900">Not in platform yet</h3>
                  {notImportedData && (
                    <span className="rounded-full bg-blue-50 border border-blue-200 px-2 py-0.5 text-[11px] font-bold text-blue-700">
                      {notImportedData.count}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                  className="rounded-lg p-1 text-slate-400 hover:text-slate-600 hover:bg-slate-100 cursor-pointer"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                {loadingNotImported ? (
                  <div className="py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
                    <RefreshCw className="h-5 w-5 animate-spin text-blue-600" />
                    <span className="text-[13px]">Checking SharePoint library...</span>
                  </div>
                ) : notImportedError ? (
                  <div className="rounded-lg bg-rose-50 border border-rose-200 p-3 text-[13px] text-rose-700 space-y-2">
                    <p>{notImportedError}</p>
                    <button
                      type="button"
                      onClick={() => handleCheckNotImported(true)}
                      className="text-[12px] font-semibold text-rose-800 underline cursor-pointer"
                    >
                      Retry
                    </button>
                  </div>
                ) : !notImportedData || notImportedData.files.length === 0 ? (
                  <div className="py-12 text-center text-slate-500 text-[13px]">
                    Everything in SharePoint is already in QShala
                  </div>
                ) : (
                  <>
                    <div className="flex items-center justify-between pb-2 border-b border-slate-100 text-[12px] text-slate-500">
                      <label className="flex items-center gap-2 cursor-pointer font-medium text-slate-700">
                        <input
                          type="checkbox"
                          checked={
                            notImportedData.files.length > 0 &&
                            selectedIds.size === notImportedData.files.length
                          }
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedIds(new Set(notImportedData.files.map((f) => f.id)));
                            } else {
                              setSelectedIds(new Set());
                            }
                          }}
                          className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                        />
                        <span>Select all</span>
                      </label>
                      <span>{selectedIds.size} of {notImportedData.files.length} selected</span>
                    </div>

                    <div className="divide-y divide-slate-100">
                      {notImportedData.files.map((file) => (
                        <div
                          key={file.id}
                          className="py-2.5 flex items-start gap-2.5 hover:bg-slate-50/60 p-1.5 rounded transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={selectedIds.has(file.id)}
                            onChange={(e) => {
                              const next = new Set(selectedIds);
                              if (e.target.checked) next.add(file.id);
                              else next.delete(file.id);
                              setSelectedIds(next);
                            }}
                            className="mt-1 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                          />
                          <div className="min-w-0 flex-1 text-[13px]">
                            <div className="font-semibold text-slate-900 truncate" title={file.name}>
                              {file.name}
                            </div>
                            {file.folder_path && (
                              <div className="text-[11px] text-slate-400 truncate" title={file.folder_path}>
                                {file.folder_path}
                              </div>
                            )}
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              {(file.size / (1024 * 1024)).toFixed(1)} MB · {file.last_modified ? new Date(file.last_modified).toLocaleDateString() : '—'}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>

              {/* Footer */}
              {notImportedData && notImportedData.files.length > 0 && (
                <div className="p-4 border-t border-slate-100 bg-slate-50/50 space-y-2">
                  {drawerImportProgress && (
                    <div className="text-[12px] text-blue-700 font-medium">
                      Importing... {drawerImportProgress.total - drawerImportProgress.remaining} of {drawerImportProgress.total} completed
                    </div>
                  )}
                  <button
                    type="button"
                    disabled={selectedIds.size === 0 || importingDrawer}
                    onClick={handleImportSelected}
                    className="w-full rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 py-2 px-3 text-[13px] font-semibold text-white transition-colors cursor-pointer flex items-center justify-center gap-1.5"
                  >
                    {importingDrawer ? (
                      <>
                        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        <span>Importing...</span>
                      </>
                    ) : (
                      <span>Import selected ({selectedIds.size})</span>
                    )}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

    </div>

  );

}

