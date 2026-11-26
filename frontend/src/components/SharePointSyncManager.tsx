'use client';

import { useEffect, useState, useRef } from 'react';
import {
  Cloud,
  RefreshCw,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  FileText,
  Layers,
  X,
  Search,
  EyeOff,
  Loader2
} from 'lucide-react';
import { api } from '@/lib/api';
import { AdminOnly } from '@/context/AuthContext';
import { SharePointSyncStatus, SharePointFileItem, IngestionJobItem, IngestionStatus, SpNotImportedResponse } from '@/lib/types';
import SharePointImporter from '@/components/SharePointImporter';
import { useToast } from '@/components/ui';
import QT from '@/components/ui/QT';

function getInitialModifiedFilter(): '30d' | '90d' | '1y' | 'older' | 'all' {
  if (typeof window === 'undefined') return '90d';
  try {
    const saved = window.localStorage.getItem('qshala_sp_not_imported_modified');
    if (saved && ['30d', '90d', '1y', 'older', 'all'].includes(saved)) {
      return saved as '30d' | '90d' | '1y' | 'older' | 'all';
    }
  } catch {
    // Ignore
  }
  return '90d';
}

function formatTimeAgo(isoString?: string | null): string {
  if (!isoString) return 'never';
  const diffMs = Date.now() - new Date(isoString).getTime();
  if (isNaN(diffMs) || diffMs < 0) return 'just now';
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'just now';
  if (diffMins === 1) return '1 min ago';
  if (diffMins < 60) return `${diffMins} min ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours === 1) return '1 hour ago';
  if (diffHours < 24) return `${diffHours} hours ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

interface SharePointSyncManagerProps {
  showImporter?: boolean;
  onTransferComplete?: () => void;
}

export default function SharePointSyncManager({ showImporter = true, onTransferComplete }: SharePointSyncManagerProps) {
  const toast = useToast();

  // SharePoint & Jobs state
  const [syncStatus, setSyncStatus] = useState<SharePointSyncStatus | null>(null);
  const [files, setFiles] = useState<SharePointFileItem[]>([]);
  const [jobs, setJobs] = useState<IngestionJobItem[]>([]);
  const [loadingSync, setLoadingSync] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  // Not in platform yet drawer state
  const [notImportedData, setNotImportedData] = useState<SpNotImportedResponse | null>(null);
  const [loadingNotImported, setLoadingNotImported] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [indexingCount, setIndexingCount] = useState(0);
  const [notImportedError, setNotImportedError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [importingDrawer, setImportingDrawer] = useState(false);
  const [drawerImportProgress, setDrawerImportProgress] = useState<{ total: number; remaining: number } | null>(null);

  // Filter state for drawer
  const [drawerFilterView, setDrawerFilterView] = useState<'likely' | 'other' | 'all'>('likely');
  const [drawerIncludePdf, setDrawerIncludePdf] = useState(false);
  const [drawerRootFolder, setDrawerRootFolder] = useState<string>('all');
  const [drawerModifiedFilter, setDrawerModifiedFilter] = useState<'30d' | '90d' | '1y' | 'older' | 'all'>(getInitialModifiedFilter);
  const [drawerSearchQuery, setDrawerSearchQuery] = useState<string>('');
  const [hiddenFolderToast, setHiddenFolderToast] = useState<{ folderId: string; folderPath: string } | null>(null);
  const [hidingFolderId, setHidingFolderId] = useState<string | null>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const queueSectionRef = useRef<HTMLDivElement>(null);

  // Live per-document stage progress & QT mascot state for SharePoint queue
  const [activeDocStatuses, setActiveDocStatuses] = useState<Record<string, IngestionStatus>>({});
  const prevProcessingJobRef = useRef<{ id: string; filename: string; document_id?: string | null } | null>(null);
  const [justCompletedJob, setJustCompletedJob] = useState<{ id: string; filename: string; slides?: number; questions?: number } | null>(null);
  const [cancellingJobDocId, setCancellingJobDocId] = useState<string | null>(null);

  const loadSharePointData = async () => {
    try {
      const [sStatus, sFiles, sJobs] = await Promise.all([
        api.getSharePointStatus().catch(() => null),
        api.listSharePointFiles().then((res) => res.files).catch(() => []),
        api.getJobs(undefined, 'MANUAL_UPLOAD').then((res) => res.jobs).catch(() => []),
      ]);
      setSyncStatus(sStatus);
      setFiles(sFiles);
      setJobs(sJobs);

      const activeDocs = sJobs
        .filter((j) => (j.status === 'PROCESSING' || j.status === 'PENDING' || j.status === 'CANCELLING') && j.document_id)
        .slice(0, 10);

      if (activeDocs.length > 0) {
        const entries = await Promise.all(
          activeDocs.map(async (j) => {
            try {
              const st = await api.getIngestionStatus(j.document_id!);
              return [j.document_id!, st] as const;
            } catch {
              return null;
            }
          })
        );
        const valid = entries.filter((e): e is [string, IngestionStatus] => e !== null);
        setActiveDocStatuses(Object.fromEntries(valid));
      } else {
        setActiveDocStatuses({});
      }

      // Track completion of the previously processing job so QT can curl up asleep
      const prevProc = prevProcessingJobRef.current;
      if (prevProc) {
        const nowJob = sJobs.find((j) => j.id === prevProc.id);
        if (nowJob && nowJob.status === 'COMPLETED') {
          setJustCompletedJob({
            id: nowJob.id,
            filename: nowJob.filename,
            slides: nowJob.step_summary?.slides_count,
            questions: nowJob.step_summary?.questions_count,
          });
          setTimeout(() => {
            setJustCompletedJob((cur) => (cur?.id === nowJob.id ? null : cur));
          }, 6000);
        }
      }
      const curProc = sJobs.find((j) => j.status === 'PROCESSING') || null;
      prevProcessingJobRef.current = curProc ? { id: curProc.id, filename: curProc.filename, document_id: curProc.document_id } : null;
    } catch (err) {
      console.error('Failed to load SharePoint data:', err);
    }
  };

  const handleLocalTransferComplete = async () => {
    await Promise.all([
      fetchNotImported(0, false),
      loadSharePointData(),
    ]);
    onTransferComplete?.();
    setTimeout(() => {
      queueSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
  };

  const handleCancelActiveJob = async (docId: string) => {
    if (!docId || cancellingJobDocId) return;
    setCancellingJobDocId(docId);
    try {
      await api.cancelIngestion(docId);
      toast.info('Stopping deck ingestion...');
      await loadSharePointData();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to cancel deck');
    } finally {
      setCancellingJobDocId(null);
    }
  };

  const fetchNotImported = async (
    offset = 0,
    append = false,
    opts?: {
      view?: 'likely' | 'other' | 'all';
      includePdf?: boolean;
      folder?: string;
      q?: string;
      modified?: '30d' | '90d' | '1y' | 'older' | 'all';
    }
  ) => {
    if (append) setLoadingMore(true);
    else setLoadingNotImported(true);
    setNotImportedError(null);
    try {
      const activeView = opts?.view ?? drawerFilterView;
      const activeIncludePdf = opts?.includePdf !== undefined ? opts.includePdf : drawerIncludePdf;
      const activeFolder = opts?.folder !== undefined ? opts.folder : drawerRootFolder;
      const activeQ = opts?.q !== undefined ? opts.q : drawerSearchQuery;
      const activeModified = opts?.modified !== undefined ? opts.modified : drawerModifiedFilter;

      const res = await api.spNotImported({
        limit: 200,
        offset,
        view: activeView,
        includePdf: activeIncludePdf,
        folder: activeFolder === 'all' ? undefined : activeFolder,
        q: activeQ.trim() || undefined,
        modified: activeModified,
      });
      if (append) {
        setNotImportedData((prev) => (prev ? { ...res, files: [...prev.files, ...res.files] } : res));
        setSelectedIds((prev) => {
          const next = new Set(prev);
          res.files.forEach((f) => next.add(f.id));
          return next;
        });
      } else {
        setNotImportedData(res);
        setSelectedIds(new Set(res.files.map((f) => f.id)));
      }
      return res;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to check SharePoint files';
      setNotImportedError(msg);
      return null;
    } finally {
      if (append) setLoadingMore(false);
      else setLoadingNotImported(false);
    }
  };

  const handleViewChange = (v: 'likely' | 'other' | 'all') => {
    setDrawerFilterView(v);
    void fetchNotImported(0, false, { view: v });
  };

  const handleIncludePdfChange = (checked: boolean) => {
    setDrawerIncludePdf(checked);
    void fetchNotImported(0, false, { includePdf: checked });
  };

  const handleRootChange = (folder: string) => {
    setDrawerRootFolder(folder);
    void fetchNotImported(0, false, { folder });
  };

  const handleModifiedChange = (val: '30d' | '90d' | '1y' | 'older' | 'all') => {
    setDrawerModifiedFilter(val);
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem('qshala_sp_not_imported_modified', val);
      }
    } catch {
      // Ignore
    }
    void fetchNotImported(0, false, { modified: val });
  };

  const handleSearchChange = (val: string) => {
    setDrawerSearchQuery(val);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => {
      void fetchNotImported(0, false, { q: val });
    }, 300);
  };

  const handleHideFolder = async (folderId: string, driveId?: string, folderPath?: string) => {
    if (!folderId || hidingFolderId) return;
    setHidingFolderId(folderId);
    try {
      await api.spHideFolder(folderId, driveId, folderPath);
      setHiddenFolderToast({ folderId, folderPath: folderPath || 'Folder' });
      await fetchNotImported(0, false);
    } catch (err) {
      console.error('Failed to hide folder:', err);
    } finally {
      setHidingFolderId(null);
    }
  };

  const handleUnhideFolder = async () => {
    if (!hiddenFolderToast) return;
    const { folderId } = hiddenFolderToast;
    try {
      await api.spUnhideFolder(folderId);
      setHiddenFolderToast(null);
      await fetchNotImported(0, false);
    } catch (err) {
      console.error('Failed to unhide folder:', err);
    }
  };

  const runIndexLoop = async () => {
    setIndexing(true);
    setIndexingCount(notImportedData?.index?.items_indexed || 0);
    try {
      for (let i = 0; i < 30; i++) {
        const step = await api.spIndexStep(undefined, 20);
        setIndexingCount(step.items_indexed);
        if (step.finished || step.status === 'busy') break;
        await new Promise((r) => setTimeout(r, 400));
      }
    } catch (err) {
      console.error('SharePoint index step failed:', err);
    } finally {
      setIndexing(false);
      await fetchNotImported(0, false);
    }
  };

  const handleOpenDrawer = async () => {
    setDrawerOpen(true);
    const res = await fetchNotImported(0, false);
    const lastFull = res?.index?.last_full_at;
    const isStale = !lastFull || Date.now() - new Date(lastFull).getTime() > 3600000;
    if (isStale) {
      void runIndexLoop();
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
      let queuedCount = 0;
      setDrawerImportProgress({ total: staged.selected, remaining });

      while (remaining > 0) {
        const step = await api.spTransferNext(staged.batch_id, 25);
        remaining = step.remaining;
        queuedCount += step.queued;
        setDrawerImportProgress({ total: staged.selected, remaining });
        if (step.queued + step.skipped_duplicate + step.failed === 0) break;
      }

      toast.success(`Import complete! Transferred ${staged.selected} files.`);
      setDrawerOpen(false);
      setSelectedIds(new Set());
      await handleLocalTransferComplete();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImportingDrawer(false);
      setDrawerImportProgress(null);
    }
  };

  useEffect(() => {
    let isSubscribed = true;
    (async () => {
      try {
        const [sStatus, sFiles, sJobs, sNotImported] = await Promise.all([
          api.getSharePointStatus().catch(() => null),
          api.listSharePointFiles().then((res) => res.files).catch(() => []),
          api.getJobs(undefined, 'MANUAL_UPLOAD').then((res) => res.jobs).catch(() => []),
          api.spNotImported(undefined, 1, 0).catch(() => null),
        ]);
        if (isSubscribed) {
          setSyncStatus(sStatus);
          setFiles(sFiles);
          setJobs(sJobs);
          if (sNotImported) setNotImportedData(sNotImported);
        }
      } catch (err) {
        console.error('Failed to load initial SharePoint data:', err);
      }
    })();
    return () => {
      isSubscribed = false;
    };
  }, []);

  const hasActiveJobs = jobs.some((j) => j.status === 'PROCESSING' || j.status === 'PENDING');

  useEffect(() => {
    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      void loadSharePointData();
    }, hasActiveJobs ? 2000 : 15000);

    return () => clearInterval(interval);
  }, [hasActiveJobs]);

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

  const handleRetryJob = async (jobId: string) => {
    try {
      await api.retryJob(jobId);
      setActionMessage(`Job ${jobId.slice(0, 8)} reset to PENDING.`);
      await loadSharePointData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Retry error: ${msg}`);
    }
  };

  const handleRequeueFile = async (fileId: string) => {
    try {
      await api.requeueSharePointFile(fileId);
      setActionMessage(`File ${fileId.slice(0, 8)} re-queued.`);
      await loadSharePointData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setActionMessage(`Re-queue error: ${msg}`);
    }
  };

  const importedFiles = files.filter(
    (f) => f.sync_status === 'QUEUED' || f.sync_status === 'COMPLETED' || f.sync_status === 'FAILED'
  );

  return (
    <div className="space-y-6">
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
              className="rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 px-3.5 py-2 text-[13px] font-semibold text-white transition-colors cursor-pointer flex items-center gap-1.5 shadow-sm shadow-blue-500/10"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loadingSync ? 'animate-spin' : ''}`} />
              <span>{loadingSync ? 'Syncing...' : 'Sync Now'}</span>
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
                {notImportedData.total}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={handleOpenDrawer}
            disabled={loadingNotImported}
            className="rounded-lg border border-slate-200 hover:bg-slate-50 px-3 py-1.5 text-[12px] font-semibold text-slate-700 transition-colors cursor-pointer flex items-center gap-1.5"
          >
            {loadingNotImported ? (
              <>
                <RefreshCw className="h-3.5 w-3.5 animate-spin text-slate-500" />
                <span>Checking...</span>
              </>
            ) : (
              <span>Check files</span>
            )}
          </button>
        </div>
      </div>

      {/* SharePoint Folder Tree Importer */}
      {showImporter && <SharePointImporter onTransferComplete={handleLocalTransferComplete} />}

      {/* Ingestion Jobs Queue Table */}
      <div ref={queueSectionRef} className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden space-y-0">
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-blue-600" />
            <h3 className="text-[15px] font-bold text-slate-900">
              Ingestion Queue ({jobs.length})
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

        {/* Live Active Deck Processing Card with QShala Cat (QT) */}
        {(() => {
          const activeJob =
            jobs.find((j) => j.status === 'PROCESSING') ||
            jobs.find((j) => j.status === 'PENDING');
          const docStatus = activeJob?.document_id ? activeDocStatuses[activeJob.document_id] : undefined;

          if (!activeJob && !justCompletedJob) return null;

          const isCompletedCard = !activeJob && Boolean(justCompletedJob);
          const pct = isCompletedCard
            ? 100
            : Math.max(0, Math.min(100, Math.round(docStatus?.progress_percentage ?? (activeJob?.status === 'PROCESSING' ? 15 : 5))));
          const stepText = isCompletedCard
            ? `Indexed ${justCompletedJob?.slides ?? 0} slides & ${justCompletedJob?.questions ?? 0} questions`
            : docStatus?.current_step || activeJob?.current_step || 'Queued for worker...';
          const detailText = !isCompletedCard ? docStatus?.detail : undefined;
          const filename = isCompletedCard ? justCompletedJob?.filename : activeJob?.filename;

          return (
            <div
              className={`border-b border-slate-100 p-6 space-y-4 transition-all ${
                isCompletedCard ? 'bg-emerald-50/30' : 'bg-blue-50/20'
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  {isCompletedCard ? (
                    <CheckCircle2 className="h-5 w-5 text-emerald-600 shrink-0" />
                  ) : (
                    <Loader2 className="h-5 w-5 animate-spin text-blue-600 shrink-0" />
                  )}
                  <div className="min-w-0">
                    <h4 className="text-[13px] font-semibold text-slate-900 flex items-center gap-2 flex-wrap">
                      <span>{isCompletedCard ? 'Deck Indexed' : 'Active Deck Processing'}</span>
                      {filename && (
                        <span className="font-normal text-slate-500 truncate max-w-md">
                          · {filename}
                        </span>
                      )}
                    </h4>
                    <p className="text-[12px] text-slate-500 font-normal truncate">
                      {detailText || stepText}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <span
                    className={`text-[14px] font-bold tabular-nums ${
                      isCompletedCard ? 'text-emerald-600' : 'text-blue-600'
                    }`}
                  >
                    {pct}%
                  </span>
                  {activeJob?.document_id && !isCompletedCard && (
                    <button
                      type="button"
                      onClick={() => void handleCancelActiveJob(activeJob.document_id!)}
                      disabled={cancellingJobDocId === activeJob.document_id}
                      className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-rose-600 hover:bg-rose-50 transition-colors disabled:opacity-60 cursor-pointer"
                      title="Stop this ingestion and remove created artifacts"
                    >
                      <X className="h-3.5 w-3.5" />
                      {cancellingJobDocId === activeJob.document_id ? 'Cancelling...' : 'Cancel deck'}
                    </button>
                  )}
                  {isCompletedCard && (
                    <button
                      type="button"
                      onClick={() => setJustCompletedJob(null)}
                      className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11.5px] font-medium text-slate-600 hover:bg-slate-50 transition-colors cursor-pointer"
                    >
                      Dismiss
                    </button>
                  )}
                </div>
              </div>

              {/* QShala Cat chasing progress ball along the progress bar */}
              <div className="relative pt-7 pb-1">
                <div
                  className="absolute top-0 -translate-x-1/2 select-none transition-all duration-300 z-10"
                  style={{ left: `${Math.max(6, Math.min(94, pct))}%` }}
                >
                  <QT
                    pose={isCompletedCard || pct >= 100 ? 'sleepy' : 'chase-blue'}
                    height={34}
                    interactive={true}
                    motion={isCompletedCard ? 'breathe' : 'pounce'}
                    speechBubble={
                      isCompletedCard
                        ? 'All decks indexed! Time for a catnap.'
                        : 'Pouncing through slides!'
                    }
                    bubblePosition="top"
                    alt={isCompletedCard ? 'QT sleeping after import' : 'QT chasing the progress ball'}
                  />
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full transition-all duration-300 ${
                      isCompletedCard || pct >= 100 ? 'bg-emerald-600' : 'bg-blue-600'
                    }`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })()}

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
                    No ingestion jobs in queue.
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
                    <td className="py-3 px-4 min-w-[180px]">
                      {(() => {
                        const docSt = job.document_id ? activeDocStatuses[job.document_id] : undefined;
                        const rowStep = docSt?.current_step || job.current_step;
                        const rowPct = Math.max(0, Math.min(100, Math.round(docSt?.progress_percentage ?? 0)));
                        return (
                          <div className="space-y-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-mono text-[11.5px] text-slate-700 block truncate max-w-[180px]" title={rowStep}>
                                {rowStep || '—'}
                              </span>
                              {(job.status === 'PROCESSING' || job.status === 'PENDING') && docSt && (
                                <span className="text-[11px] font-bold text-blue-600 tabular-nums shrink-0">
                                  {rowPct}%
                                </span>
                              )}
                            </div>
                            {(job.status === 'PROCESSING' || job.status === 'PENDING') && (
                              <div className="h-1.5 w-full bg-slate-100 rounded-full overflow-hidden">
                                <div
                                  className="h-full bg-blue-600 rounded-full transition-all duration-300"
                                  style={{ width: `${rowPct}%` }}
                                />
                              </div>
                            )}
                          </div>
                        );
                      })()}
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

      {/* Right-side Drawer: Not in platform yet */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 overflow-hidden">
          <div
            className="fixed inset-0 bg-slate-900/40 transition-opacity backdrop-blur-xs"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="fixed inset-y-0 right-0 flex max-w-full pl-10">
            <div className="w-[480px] max-w-full bg-white shadow-2xl border-l border-slate-200 flex flex-col z-50">
              {/* Header */}
              <div className="p-4 border-b border-slate-100 flex items-start justify-between bg-slate-50/70">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-[15px] font-bold text-slate-900">Not in platform yet</h3>
                    {notImportedData && (
                      <span className="rounded-full bg-blue-50 border border-blue-200 px-2 py-0.5 text-[11px] font-bold text-blue-700">
                        {notImportedData.total}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-1.5 text-[12px] text-slate-500">
                    <span>Updated {formatTimeAgo(notImportedData?.index?.last_full_at)}</span>
                    <span>·</span>
                    <button
                      type="button"
                      disabled={indexing}
                      onClick={() => runIndexLoop()}
                      className="text-blue-600 hover:text-blue-700 font-semibold cursor-pointer disabled:opacity-50 inline-flex items-center gap-1"
                    >
                      {indexing && <RefreshCw className="h-3 w-3 animate-spin" />}
                      <span>Refresh</span>
                    </button>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                  className="rounded-lg p-1 text-slate-400 hover:text-slate-600 hover:bg-slate-100 cursor-pointer"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>

              {/* Indexing Progress Banner */}
              {indexing && (
                <div className="bg-blue-50/90 border-b border-blue-100 px-4 py-2 flex items-center gap-2 text-[12px] text-blue-700 font-medium">
                  <RefreshCw className="h-3.5 w-3.5 animate-spin text-blue-600 shrink-0" />
                  <span>
                    {!notImportedData?.index?.last_full_at
                      ? `Building the list for the first time… ${indexingCount.toLocaleString()} files so far`
                      : `Indexing… ${indexingCount.toLocaleString()} files`}
                  </span>
                </div>
              )}

              {/* Hidden Folder Toast */}
              {hiddenFolderToast && (
                <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 flex items-center justify-between text-[12px] text-amber-800">
                  <span className="truncate mr-2">
                    Folder hidden: <span className="font-semibold">{hiddenFolderToast.folderPath}</span>
                  </span>
                  <button
                    type="button"
                    onClick={handleUnhideFolder}
                    className="font-bold text-amber-900 underline hover:text-amber-950 cursor-pointer shrink-0"
                  >
                    Undo
                  </button>
                </div>
              )}

              {/* Drawer Filter Controls */}
              <div className="px-4 py-2.5 border-b border-slate-100 bg-slate-50/60 space-y-2">
                {/* Bucket Chips */}
                <div className="flex items-center gap-1.5 text-[11.5px] overflow-x-auto">
                  <button
                    type="button"
                    onClick={() => handleViewChange('likely')}
                    className={`px-2.5 py-0.5 rounded-full font-medium transition-colors cursor-pointer whitespace-nowrap ${
                      drawerFilterView === 'likely'
                        ? 'bg-blue-600 text-white shadow-xs'
                        : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100'
                    }`}
                  >
                    Likely quiz decks ({notImportedData?.buckets?.likely ?? 0})
                  </button>
                  <button
                    type="button"
                    onClick={() => handleViewChange('other')}
                    className={`px-2.5 py-0.5 rounded-full font-medium transition-colors cursor-pointer whitespace-nowrap ${
                      drawerFilterView === 'other'
                        ? 'bg-blue-600 text-white shadow-xs'
                        : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100'
                    }`}
                  >
                    Other .pptx ({notImportedData?.buckets?.other ?? 0})
                  </button>
                  <button
                    type="button"
                    onClick={() => handleViewChange('all')}
                    className={`px-2.5 py-0.5 rounded-full font-medium transition-colors cursor-pointer whitespace-nowrap ${
                      drawerFilterView === 'all'
                        ? 'bg-blue-600 text-white shadow-xs'
                        : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-100'
                    }`}
                  >
                    All ({((notImportedData?.buckets?.likely ?? 0) + (notImportedData?.buckets?.other ?? 0))})
                  </button>
                </div>

                {/* Search & Root Row */}
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Search files or folders..."
                      value={drawerSearchQuery}
                      onChange={(e) => handleSearchChange(e.target.value)}
                      className="w-full pl-8 pr-2.5 py-1 text-[12px] bg-white border border-slate-200 rounded-lg placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
                    />
                  </div>
                  <select
                    value={drawerRootFolder}
                    onChange={(e) => handleRootChange(e.target.value)}
                    className="text-[12px] py-1 px-2 bg-white border border-slate-200 rounded-lg text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                  >
                    <option value="all">All roots</option>
                    <option value="Quizzes">Quizzes</option>
                    <option value="QShala School Program">QShala School Program</option>
                  </select>
                  <select
                    value={drawerModifiedFilter}
                    onChange={(e) => handleModifiedChange(e.target.value as '30d' | '90d' | '1y' | 'older' | 'all')}
                    className="text-[12px] py-1 px-2 bg-white border border-slate-200 rounded-lg text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 cursor-pointer"
                  >
                    <option value="all">All time</option>
                    <option value="30d">Last 30 days</option>
                    <option value="90d">Last 90 days</option>
                    <option value="1y">Last year</option>
                    <option value="older">Older than a year</option>
                  </select>
                </div>

                {/* PDF Toggle & Excluded Count */}
                <div className="flex items-center justify-between text-[11px] text-slate-500">
                  <label className="flex items-center gap-1.5 cursor-pointer font-medium text-slate-600">
                    <input
                      type="checkbox"
                      checked={drawerIncludePdf}
                      onChange={(e) => handleIncludePdfChange(e.target.checked)}
                      className="rounded border-slate-300 text-blue-600 focus:ring-blue-500 h-3.5 w-3.5 cursor-pointer"
                    />
                    <span>Include PDFs</span>
                  </label>
                  <span className="text-slate-400 truncate ml-2">
                    {(notImportedData?.buckets?.excluded ?? 0).toLocaleString()} excluded files
                  </span>
                </div>
              </div>

              {/* Body */}
              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                {loadingNotImported && !notImportedData ? (
                  <div className="py-12 flex flex-col items-center justify-center gap-2 text-slate-400">
                    <RefreshCw className="h-5 w-5 animate-spin text-blue-600" />
                    <span className="text-[13px]">Checking SharePoint library...</span>
                  </div>
                ) : notImportedError ? (
                  <div className="rounded-lg bg-rose-50 border border-rose-200 p-3 text-[13px] text-rose-700 space-y-2">
                    <p>{notImportedError}</p>
                    <button
                      type="button"
                      onClick={() => fetchNotImported(0, false)}
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

                    <div className="divide-y divide-slate-100 space-y-1">
                      {notImportedData.files.map((file) => (
                        <div
                          key={file.id}
                          className="py-2.5 flex items-start gap-2.5 hover:bg-slate-50/80 rounded-lg px-1 transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={selectedIds.has(file.id)}
                            onChange={(e) => {
                              setSelectedIds((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(file.id);
                                else next.delete(file.id);
                                return next;
                              });
                            }}
                            className="mt-1 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                          />
                          <div className="flex-1 min-w-0 text-[12.5px]">
                            <div className="font-semibold text-slate-900 truncate" title={file.name}>
                              {file.name}
                            </div>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              {file.root && (
                                <span className="rounded bg-slate-100 text-slate-600 font-semibold px-1.5 py-0.5 text-[10.5px] shrink-0">
                                  {file.root}
                                </span>
                              )}
                              {file.folder_path && (
                                <div className="flex items-center gap-1 min-w-0 group/folder">
                                  <span className="text-[11px] text-slate-400 truncate" title={file.folder_path}>
                                    {file.folder_path}
                                  </span>
                                  {file.folder_id && (
                                    <AdminOnly>
                                      <button
                                        type="button"
                                        title="Hide folder"
                                        onClick={() => handleHideFolder(file.folder_id!, file.drive_id, file.folder_path)}
                                        disabled={hidingFolderId === file.folder_id}
                                        className="opacity-0 group-hover/folder:opacity-100 p-0.5 text-slate-400 hover:text-rose-600 hover:bg-slate-100 rounded transition-opacity cursor-pointer shrink-0"
                                      >
                                        <EyeOff className="h-3 w-3" />
                                      </button>
                                    </AdminOnly>
                                  )}
                                </div>
                              )}
                            </div>
                            <div className="text-[11px] text-slate-400 mt-0.5">
                              {(file.size / (1024 * 1024)).toFixed(1)} MB · {file.last_modified ? new Date(file.last_modified).toLocaleDateString() : '—'}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>

                    {notImportedData.total > notImportedData.files.length && (
                      <div className="pt-2 pb-1">
                        <button
                          type="button"
                          disabled={loadingMore}
                          onClick={() => fetchNotImported(notImportedData.files.length, true)}
                          className="w-full py-2 px-3 rounded-lg border border-slate-200 bg-slate-50/50 hover:bg-slate-100 text-[12.5px] font-semibold text-slate-700 cursor-pointer flex items-center justify-center gap-1.5 transition-colors disabled:opacity-50"
                        >
                          {loadingMore ? (
                            <>
                              <RefreshCw className="h-3.5 w-3.5 animate-spin text-slate-500" />
                              <span>Loading more...</span>
                            </>
                          ) : (
                            <span>Load more ({notImportedData.total - notImportedData.files.length} remaining)</span>
                          )}
                        </button>
                      </div>
                    )}
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
                    className="w-full rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 py-2 px-3 text-[13px] font-semibold text-white transition-colors cursor-pointer flex items-center justify-center gap-1.5 shadow-sm shadow-blue-500/10"
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
