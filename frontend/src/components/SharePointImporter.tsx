'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  Cloud,
  CornerLeftUp,
  Download,
  FileText,
  Folder,
  FolderOpen,
  Home,
  Loader2,
  Presentation,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
  Calendar,
  ExternalLink
} from 'lucide-react';
import { api } from '../lib/api';
import { SpBrowse, SpConfig, SpDrive, SpImportStatus, SpItemStatus, SpPlan, SpSelection } from '../lib/types';

const STATUS_STYLE: Record<SpItemStatus, { label: string; cls: string; dotCls: string }> = {
  NEW: {
    label: 'Ready to import',
    cls: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    dotCls: 'bg-emerald-500'
  },
  CHANGED: {
    label: 'Updated',
    cls: 'border-blue-200 bg-blue-50 text-blue-700',
    dotCls: 'bg-blue-500'
  },
  RETRY: {
    label: 'Retry',
    cls: 'border-amber-200 bg-amber-50 text-amber-800',
    dotCls: 'bg-amber-500'
  },
  ALREADY_IMPORTED: {
    label: 'Already imported',
    cls: 'border-slate-200 bg-slate-100 text-slate-500',
    dotCls: 'bg-slate-400'
  },
  IN_PROGRESS: {
    label: 'In progress',
    cls: 'border-violet-200 bg-violet-50 text-violet-700',
    dotCls: 'bg-violet-500'
  },
  POSSIBLE_DUPLICATE: {
    label: 'Possible duplicate',
    cls: 'border-amber-300 bg-amber-50 text-amber-800',
    dotCls: 'bg-amber-500'
  },
};

const mb = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} KB`;

const formatDate = (isoString?: string | null) => {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '';
  }
};

function HighlightText({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp(`(${escaped})`, 'gi');
  const parts = text.split(regex);
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === query.toLowerCase() ? (
          <mark key={i} className="bg-amber-200/90 text-amber-950 font-semibold px-0.5 rounded">
            {part}
          </mark>
        ) : (
          part
        )
      )}
    </>
  );
}

type Picked = { id: string; kind: 'file' | 'folder'; name: string };
type Crumb = { id: string | null; name: string };

export default function SharePointImporter() {
  const [cfg, setCfg] = useState<SpConfig | null>(null);
  const [drives, setDrives] = useState<SpDrive[]>([]);
  const [driveId, setDriveId] = useState('');
  const [crumbs, setCrumbs] = useState<Crumb[]>([{ id: null, name: 'Library root' }]);
  const [page, setPage] = useState<SpBrowse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<Map<string, Picked>>(new Map());

  // Interactive UI & Search States
  const [searchQuery, setSearchQuery] = useState('');
  const [foldersCollapsed, setFoldersCollapsed] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [fileFilterTab, setFileFilterTab] = useState<'ALL' | 'NEW' | 'IMPORTED'>('ALL');

  // Advanced Query Options
  const [recursive, setRecursive] = useState(true);
  const [includeDups, setIncludeDups] = useState(false);
  const [modifiedAfter, setModifiedAfter] = useState('');

  // Execution States
  const [plan, setPlan] = useState<SpPlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ total: number; remaining: number } | null>(null);
  const [result, setResult] = useState<SpImportStatus | null>(null);
  const [waiting, setWaiting] = useState(0);
  const cancelRef = useRef(false);

  useEffect(() => {
    api.spConfig().then(setCfg).catch((e: Error) => setError(e.message));
    api.spImportStatus().then((s) => setWaiting(s.remaining)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!cfg?.configured) return;
    api.spDrives().then((r) => {
      setDrives(r.drives);
      if (r.drives.length > 0) {
        setDriveId(r.drives[0].id);
        void open(r.drives[0].id, [{ id: null, name: 'Library root' }]);
      }
    }).catch((e: Error) => setError(e.message));
  }, [cfg?.configured]);

  async function open(drive: string, trail: Crumb[]) {
    setLoading(true);
    setError('');
    setSearchQuery('');
    try {
      const folder = trail[trail.length - 1].id;
      setPage(await api.spBrowse(drive, folder));
      setCrumbs(trail);
      setPlan(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open that folder');
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    if (!page?.next_url) return;
    setLoading(true);
    try {
      const more = await api.spBrowse(driveId, page.folder_id, page.next_url);
      setPage({
        ...more,
        folders: [...page.folders, ...more.folders],
        files: [...page.files, ...more.files],
        other_files_hidden: page.other_files_hidden + more.other_files_hidden
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load more');
    } finally {
      setLoading(false);
    }
  }

  function toggle(p: Picked) {
    setPicked((cur) => {
      const next = new Map(cur);
      if (next.has(p.id)) next.delete(p.id);
      else next.set(p.id, p);
      return next;
    });
    setPlan(null);
  }

  function selectAllNewInCurrentView() {
    if (!filteredFiles) return;
    setPicked((cur) => {
      const next = new Map(cur);
      for (const f of filteredFiles) {
        if (f.status === 'NEW' || f.status === 'CHANGED' || f.status === 'RETRY') {
          next.set(f.id, { id: f.id, kind: 'file', name: f.name });
        }
      }
      return next;
    });
    setPlan(null);
  }

  function selectAllFiles() {
    if (!filteredFiles) return;
    setPicked((cur) => {
      const next = new Map(cur);
      for (const f of filteredFiles) {
        next.set(f.id, { id: f.id, kind: 'file', name: f.name });
      }
      return next;
    });
    setPlan(null);
  }

  function clearSelection() {
    setPicked(new Map());
    setPlan(null);
  }

  function selectionBody(items: Picked[]): SpSelection {
    return {
      drive_id: driveId,
      items: items.map((i) => ({ id: i.id, kind: i.kind })),
      recursive,
      include_possible_duplicates: includeDups,
      filters: {
        name_contains: searchQuery.trim() || undefined,
        modified_after: modifiedAfter ? new Date(modifiedAfter).toISOString() : undefined,
      },
    };
  }

  const currentFolder: Picked = useMemo(() => {
    const last = crumbs[crumbs.length - 1];
    return { id: last.id ?? 'root', kind: 'folder', name: last.name };
  }, [crumbs]);

  const chosen = useMemo(() => Array.from(picked.values()), [picked]);

  // Live filter as user types
  const filteredFolders = useMemo(() => {
    if (!page?.folders) return [];
    if (!searchQuery.trim()) return page.folders;
    const q = searchQuery.toLowerCase().trim();
    return page.folders.filter((f) => (f.name || '').toLowerCase().includes(q));
  }, [page?.folders, searchQuery]);

  const filteredFiles = useMemo(() => {
    if (!page?.files) return [];
    let list = page.files;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter((f) => f.name.toLowerCase().includes(q));
    }

    if (fileFilterTab === 'NEW') {
      list = list.filter((f) => f.status === 'NEW' || f.status === 'CHANGED' || f.status === 'RETRY');
    } else if (fileFilterTab === 'IMPORTED') {
      list = list.filter((f) => f.status === 'ALREADY_IMPORTED');
    }

    return list;
  }, [page?.files, searchQuery, fileFilterTab]);

  const counts = useMemo(() => {
    if (!page?.files) return { total: 0, new: 0, imported: 0 };
    let newCount = 0;
    let importedCount = 0;
    for (const f of page.files) {
      if (f.status === 'NEW' || f.status === 'CHANGED' || f.status === 'RETRY') newCount++;
      if (f.status === 'ALREADY_IMPORTED') importedCount++;
    }
    return { total: page.files.length, new: newCount, imported: importedCount };
  }, [page?.files]);

  async function preview(items: Picked[]) {
    setPlanning(true);
    setError('');
    setResult(null);
    try {
      setPlan(await api.spPlan(selectionBody(items)));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setPlanning(false);
    }
  }

  async function drain(batchId: string | undefined, total: number) {
    setRunning(true);
    setError('');
    cancelRef.current = false;
    let remaining = total;
    try {
      setProgress({ total, remaining });
      while (remaining > 0 && !cancelRef.current) {
        const step = await api.spTransferNext(batchId, 3);
        remaining = step.remaining;
        setProgress({ total, remaining });
        if (step.queued + step.skipped_duplicate + step.failed === 0) break;
      }
      const status = await api.spImportStatus(batchId);
      setResult(status);
      setWaiting(status.remaining);
      setPlan(null);
      setPicked(new Map());
      void open(driveId, crumbs);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed');
      api.spImportStatus(batchId).then((s) => setWaiting(s.remaining)).catch(() => {});
    } finally {
      setRunning(false);
    }
  }

  async function runImport(items: Picked[]) {
    setResult(null);
    setError('');
    setRunning(true);
    try {
      const staged = await api.spImport(selectionBody(items));
      setRunning(false);
      await drain(staged.batch_id, staged.selected);
    } catch (e) {
      setRunning(false);
      setError(e instanceof Error ? e.message : 'Import failed');
    }
  }

  if (!cfg) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-7 text-[13.5px] text-slate-500 flex items-center justify-center gap-3">
        <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
        <span>Connecting to SharePoint...</span>
      </div>
    );
  }

  if (!cfg.configured) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50/80 p-6 text-[13.5px] text-amber-900 space-y-2">
        <div className="flex items-center gap-2 font-semibold">
          <AlertTriangle className="h-4 w-4 text-amber-600" />
          <span>SharePoint is not connected yet</span>
        </div>
        <p className="text-amber-800">
          An admin needs to set the Azure app credentials and SharePoint site on the server (
          <code>AZURE_TENANT_ID</code>, <code>AZURE_CLIENT_ID</code>, <code>AZURE_CLIENT_SECRET</code>, <code>SHAREPOINT_SITE_ID</code>).
        </p>
      </div>
    );
  }

  const wantedItems = chosen.length > 0 ? chosen : null;
  const isAllNewSelected =
    filteredFiles.length > 0 &&
    filteredFiles
      .filter((f) => f.status === 'NEW' || f.status === 'CHANGED' || f.status === 'RETRY')
      .every((f) => picked.has(f.id));

  return (
    <div className="rounded-2xl border border-slate-200/90 bg-white shadow-sm shadow-slate-100 overflow-hidden transition-all">
      {/* Importer Top Header */}
      <div className="border-b border-slate-100 bg-gradient-to-r from-blue-50/50 via-white to-slate-50/30 p-5 sm:p-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-start gap-3.5">
            <div className="rounded-xl bg-blue-600 p-2.5 text-white shadow-sm shadow-blue-500/20">
              <Cloud className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-[17px] font-bold text-slate-900">SharePoint Library Browser</h2>
                <span className="rounded-full bg-emerald-50 border border-emerald-200 px-2.5 py-0.5 text-[11px] font-bold text-emerald-700 flex items-center gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Connected
                </span>
              </div>
              <p className="mt-0.5 text-[13px] text-slate-500">
                Browse quiz folders, search files, preview what is new, and import decks directly into QShala.
                {cfg.root_folder && (
                  <span className="ml-1 text-slate-600 font-medium">
                    (Scoped to: <span className="font-semibold text-slate-800">“{cfg.root_folder}”</span>)
                  </span>
                )}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 self-start sm:self-auto">
            {crumbs.length > 1 && (
              <button
                type="button"
                onClick={() => void open(driveId, crumbs.slice(0, -1))}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[12.5px] font-semibold text-slate-700 shadow-xs hover:bg-slate-50 hover:text-slate-900 transition-colors cursor-pointer"
              >
                <CornerLeftUp className="h-3.5 w-3.5 text-slate-500" />
                <span>Up Level</span>
              </button>
            )}

            <button
              type="button"
              onClick={() => void preview([currentFolder])}
              disabled={planning || running}
              className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-3.5 py-1.5 text-[12.5px] font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-50 transition-colors cursor-pointer"
            >
              <FileText className="h-3.5 w-3.5" />
              <span>Preview Whole Folder</span>
            </button>
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-xl border border-red-200 bg-red-50/90 p-3.5 text-[13px] text-red-800 flex items-center justify-between">
            <span className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-red-600 shrink-0" />
              <span>{error}</span>
            </span>
            <button
              type="button"
              onClick={() => setError('')}
              className="text-red-500 hover:text-red-700 text-sm font-bold ml-2 cursor-pointer"
            >
              &times;
            </button>
          </div>
        )}

        {waiting > 0 && !running && (
          <div className="mt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-3 text-[13px] text-amber-900">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
              <span><strong>{waiting} deck(s)</strong> are selected but not finished transferring.</span>
            </div>
            <button
              type="button"
              onClick={() => void drain(undefined, waiting)}
              className="rounded-lg bg-amber-600 px-3.5 py-1.5 text-[12.5px] font-semibold text-white hover:bg-amber-700 shadow-xs transition-colors cursor-pointer"
            >
              Resume Transfer
            </button>
          </div>
        )}
      </div>

      <div className="p-5 sm:p-6 space-y-5">
        {/* Navigation Breadcrumbs & Drive Selector */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-100 bg-slate-50/70 p-3">
          <div className="flex items-center gap-1.5 flex-wrap text-[13px]">
            {drives.length > 1 && (
              <select
                value={driveId}
                onChange={(e) => {
                  setDriveId(e.target.value);
                  setPicked(new Map());
                  void open(e.target.value, [{ id: null, name: 'Library root' }]);
                }}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-slate-700 font-medium"
                aria-label="Library"
              >
                {drives.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name || d.id}
                  </option>
                ))}
              </select>
            )}

            <nav className="flex items-center gap-1.5 flex-wrap" aria-label="Folder trail">
              {crumbs.map((c, i) => {
                const isLast = i === crumbs.length - 1;
                return (
                  <span key={`${c.id}-${i}`} className="flex items-center gap-1.5">
                    {i === 0 ? (
                      <button
                        type="button"
                        onClick={() => void open(driveId, crumbs.slice(0, 1))}
                        disabled={isLast}
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md transition-colors ${
                          isLast
                            ? 'font-bold text-slate-900 bg-white border border-slate-200 shadow-2xs'
                            : 'text-blue-700 hover:bg-blue-50 cursor-pointer'
                        }`}
                      >
                        <Home className="h-3.5 w-3.5 text-blue-600" />
                        <span>{c.name}</span>
                      </button>
                    ) : (
                      <>
                        <ChevronRight className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                        <button
                          type="button"
                          onClick={() => void open(driveId, crumbs.slice(0, i + 1))}
                          disabled={isLast}
                          className={`px-2 py-0.5 rounded-md transition-colors ${
                            isLast
                              ? 'font-bold text-slate-900 bg-white border border-slate-200 shadow-2xs'
                              : 'text-blue-700 hover:bg-blue-50 cursor-pointer'
                          }`}
                        >
                          {c.name}
                        </button>
                      </>
                    )}
                  </span>
                );
              })}
            </nav>
          </div>

          {/* Advanced Options Toggle */}
          <button
            type="button"
            onClick={() => setShowAdvanced(!showAdvanced)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-semibold transition-all cursor-pointer border ${
              showAdvanced || !recursive || includeDups || modifiedAfter
                ? 'bg-blue-50 border-blue-200 text-blue-700'
                : 'bg-white border-slate-200 text-slate-600 hover:text-slate-900 hover:bg-slate-50'
            }`}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            <span>Options</span>
            {(!recursive || includeDups || modifiedAfter) && (
              <span className="h-1.5 w-1.5 rounded-full bg-blue-600" />
            )}
            <ChevronDown className={`h-3 w-3 transition-transform ${showAdvanced ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {/* Collapsible Advanced Filters */}
        {showAdvanced && (
          <div className="rounded-xl border border-blue-100 bg-blue-50/30 p-4 text-[12.5px] text-slate-700 space-y-3 transition-all animate-fadeIn">
            <div className="font-semibold text-slate-800 text-[13px] flex items-center gap-1.5">
              <SlidersHorizontal className="h-3.5 w-3.5 text-blue-600" />
              <span>Import & Scan Configuration</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <label className="flex items-center gap-2 rounded-lg bg-white p-2.5 border border-slate-200/80 cursor-pointer hover:bg-slate-50">
                <input
                  type="checkbox"
                  checked={recursive}
                  onChange={(e) => {
                    setRecursive(e.target.checked);
                    setPlan(null);
                  }}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                <div>
                  <div className="font-medium text-slate-800">Scan Sub-folders</div>
                  <div className="text-[11px] text-slate-500">Recursively scan nested directories</div>
                </div>
              </label>

              <label className="flex items-center gap-2 rounded-lg bg-white p-2.5 border border-slate-200/80 cursor-pointer hover:bg-slate-50">
                <input
                  type="checkbox"
                  checked={includeDups}
                  onChange={(e) => {
                    setIncludeDups(e.target.checked);
                    setPlan(null);
                  }}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                <div>
                  <div className="font-medium text-slate-800">Include Duplicates</div>
                  <div className="text-[11px] text-slate-500">Allow decks matching existing titles</div>
                </div>
              </label>

              <div className="rounded-lg bg-white p-2.5 border border-slate-200/80 flex flex-col justify-center">
                <span className="font-medium text-slate-800 flex items-center gap-1">
                  <Calendar className="h-3.5 w-3.5 text-slate-400" />
                  Modified After
                </span>
                <input
                  type="date"
                  value={modifiedAfter}
                  onChange={(e) => {
                    setModifiedAfter(e.target.value);
                    setPlan(null);
                  }}
                  className="mt-1 w-full rounded border border-slate-200 px-2 py-1 text-[12px] bg-slate-50 focus:bg-white"
                />
              </div>
            </div>
          </div>
        )}

        {/* Live Search & Filter Bar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search folders or decks in current view as you type..."
              className="w-full rounded-xl border border-slate-200 bg-white pl-10 pr-10 py-2.5 text-[13.5px] text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-all shadow-xs"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-slate-400 hover:text-slate-600 rounded-md cursor-pointer"
                title="Clear search"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>

          {/* Quick Selection Actions */}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={selectAllNewInCurrentView}
              disabled={counts.new === 0}
              className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50/80 px-3.5 py-2 text-[12.5px] font-semibold text-emerald-800 hover:bg-emerald-100 disabled:opacity-40 transition-colors cursor-pointer"
              title="Select all unimported decks in this folder"
            >
              <Sparkles className="h-3.5 w-3.5 text-emerald-600" />
              <span>Select Ready ({counts.new})</span>
            </button>

            {chosen.length > 0 && (
              <button
                type="button"
                onClick={clearSelection}
                className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-[12.5px] font-semibold text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-colors cursor-pointer"
              >
                Clear ({chosen.length})
              </button>
            )}
          </div>
        </div>

        {/* Loading Indicator */}
        {loading && !page && (
          <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-10 text-center">
            <Loader2 className="mx-auto h-6 w-6 animate-spin text-blue-600 mb-2" />
            <p className="text-[13.5px] font-medium text-slate-600">Reading SharePoint directory...</p>
          </div>
        )}

        {/* Browsing Content */}
        {page && (
          <div className="space-y-5">
            {/* 1. Folders Section (Collapsible) */}
            {filteredFolders.length > 0 && (
              <div className="rounded-xl border border-slate-200/90 overflow-hidden bg-white shadow-2xs">
                <div
                  onClick={() => setFoldersCollapsed(!foldersCollapsed)}
                  className="flex items-center justify-between px-4 py-3 bg-slate-50/80 hover:bg-slate-100/70 border-b border-slate-100 cursor-pointer select-none transition-colors"
                >
                  <div className="flex items-center gap-2.5">
                    {foldersCollapsed ? (
                      <ChevronRight className="h-4 w-4 text-slate-500" />
                    ) : (
                      <ChevronDown className="h-4 w-4 text-slate-500" />
                    )}
                    <FolderOpen className="h-4 w-4 text-amber-500" />
                    <span className="text-[13.5px] font-bold text-slate-800">
                      Folders ({filteredFolders.length})
                    </span>
                    {searchQuery && (
                      <span className="text-[11.5px] text-slate-400 font-normal">
                        (filtered from {page.folders.length})
                      </span>
                    )}
                  </div>

                  <span className="text-[12px] text-slate-400">
                    {foldersCollapsed ? 'Click to show' : 'Click to collapse'}
                  </span>
                </div>

                {!foldersCollapsed && (
                  <div className="p-3 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5">
                    {filteredFolders.map((f) => {
                      const isPicked = picked.has(f.id);
                      return (
                        <div
                          key={f.id}
                          className={`group flex items-center justify-between gap-2.5 rounded-xl border p-2.5 transition-all ${
                            isPicked
                              ? 'border-blue-300 bg-blue-50/60 shadow-xs'
                              : 'border-slate-200/80 bg-white hover:border-slate-300 hover:bg-slate-50/60'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0">
                            <input
                              type="checkbox"
                              checked={isPicked}
                              onChange={() => toggle({ id: f.id, kind: 'folder', name: f.name || 'Folder' })}
                              className="rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer shrink-0"
                              aria-label={`Select folder ${f.name}`}
                            />
                            <Folder className="h-4 w-4 text-amber-500 shrink-0" />
                            <button
                              type="button"
                              onClick={() => void open(driveId, [...crumbs, { id: f.id, name: f.name || 'Folder' }])}
                              className="text-left font-medium text-[13px] text-slate-800 hover:text-blue-600 hover:underline truncate cursor-pointer"
                              title={`Open folder: ${f.name}`}
                            >
                              <HighlightText text={f.name || 'Folder'} query={searchQuery} />
                            </button>
                          </div>

                          <div className="flex items-center gap-1 shrink-0">
                            {typeof f.child_count === 'number' && (
                              <span className="text-[11px] font-medium text-slate-400 bg-slate-100 rounded-md px-1.5 py-0.5">
                                {f.child_count}
                              </span>
                            )}
                            <button
                              type="button"
                              onClick={() => void open(driveId, [...crumbs, { id: f.id, name: f.name || 'Folder' }])}
                              className="p-1 text-slate-400 hover:text-blue-600 rounded-md cursor-pointer"
                              title="Open"
                            >
                              <ChevronRight className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* 2. Decks & Files Section */}
            <div className="rounded-xl border border-slate-200/90 overflow-hidden bg-white shadow-2xs">
              {/* Files Filter & Status Header */}
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-4 py-3 bg-slate-50/80 border-b border-slate-100">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-blue-600" />
                  <span className="text-[13.5px] font-bold text-slate-800">
                    Quiz Decks & Files ({filteredFiles.length})
                  </span>
                  {searchQuery && (
                    <span className="text-[11.5px] text-slate-400 font-normal">
                      (matching &ldquo;{searchQuery}&rdquo;)
                    </span>
                  )}
                </div>

                {/* Sub-filter tabs */}
                <div className="flex items-center gap-1 bg-white p-0.5 rounded-lg border border-slate-200">
                  <button
                    type="button"
                    onClick={() => setFileFilterTab('ALL')}
                    className={`px-2.5 py-1 text-[11.5px] font-semibold rounded-md transition-colors cursor-pointer ${
                      fileFilterTab === 'ALL'
                        ? 'bg-slate-900 text-white shadow-2xs'
                        : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    All ({counts.total})
                  </button>
                  <button
                    type="button"
                    onClick={() => setFileFilterTab('NEW')}
                    className={`px-2.5 py-1 text-[11.5px] font-semibold rounded-md transition-colors cursor-pointer ${
                      fileFilterTab === 'NEW'
                        ? 'bg-emerald-600 text-white shadow-2xs'
                        : 'text-slate-600 hover:text-emerald-700'
                    }`}
                  >
                    Ready ({counts.new})
                  </button>
                  <button
                    type="button"
                    onClick={() => setFileFilterTab('IMPORTED')}
                    className={`px-2.5 py-1 text-[11.5px] font-semibold rounded-md transition-colors cursor-pointer ${
                      fileFilterTab === 'IMPORTED'
                        ? 'bg-slate-600 text-white shadow-2xs'
                        : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    Imported ({counts.imported})
                  </button>
                </div>
              </div>

              {/* Files Table / List */}
              {filteredFiles.length > 0 ? (
                <ul className="divide-y divide-slate-100">
                  {filteredFiles.map((f) => {
                    const isPicked = picked.has(f.id);
                    const isPptx = f.name.toLowerCase().endsWith('.pptx');
                    const statusMeta = STATUS_STYLE[f.status] || {
                      label: f.status,
                      cls: 'border-slate-200 bg-slate-100 text-slate-600',
                      dotCls: 'bg-slate-400'
                    };

                    return (
                      <li
                        key={f.id}
                        className={`group flex items-center justify-between gap-3 px-4 py-3 transition-colors ${
                          isPicked
                            ? 'bg-blue-50/50'
                            : 'hover:bg-slate-50/80'
                        }`}
                      >
                        <div className="flex items-center gap-3 min-w-0 flex-1">
                          <input
                            type="checkbox"
                            checked={isPicked}
                            onChange={() => toggle({ id: f.id, kind: 'file', name: f.name })}
                            className="rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer shrink-0"
                            aria-label={`Select ${f.name}`}
                          />

                          {/* File Type Badge */}
                          <div
                            className={`rounded-lg px-2 py-1 text-[10.5px] font-bold shrink-0 flex items-center gap-1 ${
                              isPptx
                                ? 'bg-orange-50 text-orange-700 border border-orange-200'
                                : 'bg-red-50 text-red-700 border border-red-200'
                            }`}
                          >
                            <Presentation className="h-3 w-3" />
                            <span>{isPptx ? 'PPTX' : 'PDF'}</span>
                          </div>

                          <div className="min-w-0 flex-1">
                            <span
                              onClick={() => toggle({ id: f.id, kind: 'file', name: f.name })}
                              className="text-[13.5px] font-medium text-slate-800 hover:text-blue-700 cursor-pointer block truncate"
                            >
                              <HighlightText text={f.name} query={searchQuery} />
                            </span>
                            {f.modified && (
                              <span className="text-[11.5px] text-slate-400 font-normal">
                                Modified {formatDate(f.modified)}
                              </span>
                            )}
                          </div>
                        </div>

                        <div className="flex items-center gap-3 shrink-0">
                          <span className="text-[12px] font-mono text-slate-400">{mb(f.size)}</span>
                          <span
                            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${statusMeta.cls}`}
                          >
                            <span className={`h-1.5 w-1.5 rounded-full ${statusMeta.dotCls}`} />
                            <span>{statusMeta.label}</span>
                          </span>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <div className="p-8 text-center text-slate-500">
                  <p className="text-[13.5px]">No decks matching your current filter.</p>
                  {searchQuery && (
                    <button
                      type="button"
                      onClick={() => setSearchQuery('')}
                      className="mt-2 text-[12.5px] font-semibold text-blue-600 hover:underline cursor-pointer"
                    >
                      Clear search filter
                    </button>
                  )}
                </div>
              )}

              {/* Load More Pagination */}
              {page?.next_url && (
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loading}
                  className="w-full border-t border-slate-100 py-2.5 text-[13px] font-semibold text-blue-600 hover:bg-blue-50/50 flex items-center justify-center gap-1.5 transition-colors cursor-pointer"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
                  <span>Load more decks from SharePoint</span>
                </button>
              )}

              {page.other_files_hidden > 0 && (
                <div className="border-t border-slate-100 bg-slate-50/50 px-4 py-2 text-[11.5px] text-slate-400">
                  {page.other_files_hidden} other file(s) hidden (only {cfg.supported_extensions.join(', ')} can be imported).
                </div>
              )}
            </div>

            {/* Empty State */}
            {page.folders.length === 0 && page.files.length === 0 && (
              <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center text-slate-400">
                <Folder className="mx-auto h-8 w-8 text-slate-300 mb-2" />
                <p className="text-[13.5px]">This folder is empty.</p>
              </div>
            )}
          </div>
        )}

        {/* Floating/Bottom Selection Summary Bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/90 bg-slate-50/90 p-4">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-slate-900 px-2.5 py-1 text-[12px] font-bold text-white">
              {chosen.length}
            </span>
            <span className="text-[13px] font-medium text-slate-700">
              {chosen.length === 1 ? 'item selected' : 'items selected'}
            </span>
            {chosen.length > 0 && (
              <button
                type="button"
                onClick={clearSelection}
                className="text-[12.5px] text-slate-500 hover:text-slate-800 ml-2 underline cursor-pointer"
              >
                Clear
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!wantedItems || planning || running}
              onClick={() => wantedItems && void preview(wantedItems)}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-[13px] font-bold text-white shadow-sm hover:bg-blue-700 disabled:opacity-40 transition-all cursor-pointer"
            >
              {planning ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Checking Decks...</span>
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  <span>Preview Selected ({chosen.length})</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Preview Plan Modal / Card */}
        {plan && (
          <div className="rounded-2xl border border-blue-200 bg-gradient-to-b from-blue-50/60 to-white p-5 space-y-4 shadow-sm animate-fadeIn">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-b border-blue-100 pb-3">
              <div>
                <h3 className="text-[15px] font-bold text-slate-900 flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-blue-600" />
                  <span>Import Preview Plan</span>
                </h3>
                <p className="text-[12.5px] text-slate-500">
                  {plan.total_found} total deck(s) discovered · {plan.to_import} new deck(s) ready for import ({mb(plan.bytes_to_import)})
                </p>
              </div>

              <div className="flex items-center gap-1.5 flex-wrap">
                {Object.entries(plan.counts).map(([k, n]) => (
                  <span
                    key={k}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
                      STATUS_STYLE[k as SpItemStatus]?.cls ?? 'border-slate-200 bg-slate-100 text-slate-600'
                    }`}
                  >
                    {STATUS_STYLE[k as SpItemStatus]?.label ?? k}: {n}
                  </span>
                ))}
              </div>
            </div>

            {plan.truncated && (
              <p className="text-[12.5px] text-amber-800 bg-amber-50 border border-amber-200 p-2.5 rounded-lg">
                Large selection: only the first part was scanned. Import it, then preview again to continue.
              </p>
            )}

            <div className="max-h-60 overflow-y-auto rounded-xl border border-blue-100/80 bg-white divide-y divide-blue-50 text-[12.5px]">
              {plan.files.map((f) => (
                <div key={f.id} className="flex items-center justify-between gap-2 p-2.5">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    {f.will_import ? (
                      <span className="rounded-full bg-emerald-100 text-emerald-700 p-0.5">
                        <Check className="h-3.5 w-3.5" />
                      </span>
                    ) : (
                      <span className="w-4.5" />
                    )}
                    <span className="truncate text-slate-800 font-medium">
                      {f.folder_path ? `${f.folder_path}/` : ''}
                      {f.name}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-slate-400 font-mono text-[11.5px]">{mb(f.size)}</span>
                    <span
                      className={`rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${
                        STATUS_STYLE[f.status].cls
                      }`}
                    >
                      {STATUS_STYLE[f.status].label}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex items-center justify-between gap-3 pt-2">
              <button
                type="button"
                onClick={() => setPlan(null)}
                className="text-[12.5px] text-slate-500 hover:text-slate-800 font-medium cursor-pointer"
              >
                Cancel Preview
              </button>

              <button
                type="button"
                disabled={running || plan.to_import === 0}
                onClick={() => void runImport(wantedItems ?? [currentFolder])}
                className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-[13.5px] font-bold text-white shadow-sm hover:bg-blue-700 disabled:opacity-40 transition-all cursor-pointer"
              >
                {running ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span>Importing Decks...</span>
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" />
                    <span>
                      Start Import ({plan.to_import} {plan.to_import === 1 ? 'deck' : 'decks'} · {mb(plan.bytes_to_import)})
                    </span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {/* Transfer Progress Bar */}
        {progress && running && (
          <div className="rounded-xl border border-blue-200 bg-blue-50/50 p-4 space-y-2 animate-fadeIn" aria-live="polite">
            <div className="flex items-center justify-between text-[13px] text-slate-700 font-medium">
              <span className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                <span>
                  Transferring decks: {progress.total - progress.remaining} of {progress.total} completed
                </span>
              </span>
              <button
                type="button"
                onClick={() => {
                  cancelRef.current = true;
                }}
                className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] font-semibold text-slate-600 hover:text-slate-900 cursor-pointer"
              >
                Pause
              </button>
            </div>

            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full bg-blue-600 transition-all duration-300"
                style={{
                  width: `${progress.total ? ((progress.total - progress.remaining) / progress.total) * 100 : 0}%`
                }}
              />
            </div>
          </div>
        )}

        {/* Import Results Card */}
        {result && (
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 text-[13px] space-y-2 animate-fadeIn">
            <div className="flex items-center gap-2 font-bold text-emerald-800 text-[14px]">
              <Check className="h-4 w-4 text-emerald-600" />
              <span>Import Handed Off to Pipeline</span>
            </div>
            <p className="text-slate-700">
              Decks are now securely stored in R2 and queued for the background worker job. Slides are being parsed,
              images extracted, and questions tagged.
            </p>
            <div className="flex items-center gap-1.5 flex-wrap pt-1">
              {Object.entries(result.counts).map(([k, n]) => (
                <span
                  key={k}
                  className="rounded-md bg-white border border-emerald-200 px-2 py-0.5 text-[11.5px] font-semibold text-emerald-800"
                >
                  {k.toLowerCase().replace('_', ' ')}: {n}
                </span>
              ))}
            </div>

            {result.remaining > 0 && (
              <p className="text-amber-800 text-[12px] pt-1">
                {result.remaining} file(s) are still waiting (paused). Click &ldquo;Resume&rdquo; above when ready.
              </p>
            )}
            {result.skipped_duplicates.map((s) => (
              <p key={s.id} className="text-slate-500 text-[12px]">
                Skipped {s.name}: {s.message}
              </p>
            ))}
            {result.failed.map((f) => (
              <p key={f.id} className="text-red-700 text-[12px]">
                Failed {f.name}: {f.error}
              </p>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
