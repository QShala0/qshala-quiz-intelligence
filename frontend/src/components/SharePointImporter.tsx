'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, ArrowLeft, Check, ChevronRight, Cloud, FileText, Folder, Loader2 } from 'lucide-react';
import { api } from '../lib/api';
import { SpBrowse, SpConfig, SpDrive, SpImportStatus, SpItemStatus, SpPlan, SpSelection } from '../lib/types';

const STATUS_STYLE: Record<SpItemStatus, { label: string; cls: string }> = {
  NEW: { label: 'New', cls: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  CHANGED: { label: 'Changed', cls: 'border-blue-200 bg-blue-50 text-blue-700' },
  RETRY: { label: 'Retry', cls: 'border-amber-200 bg-amber-50 text-amber-800' },
  ALREADY_IMPORTED: { label: 'Already imported', cls: 'border-slate-200 bg-slate-100 text-slate-600' },
  IN_PROGRESS: { label: 'In progress', cls: 'border-violet-200 bg-violet-50 text-violet-700' },
  POSSIBLE_DUPLICATE: { label: 'Possible duplicate', cls: 'border-amber-300 bg-amber-50 text-amber-800' },
};

const mb = (bytes: number) => (bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`);

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
  const [recursive, setRecursive] = useState(true);
  const [includeDups, setIncludeDups] = useState(false);
  const [nameContains, setNameContains] = useState('');
  const [modifiedAfter, setModifiedAfter] = useState('');
  const [plan, setPlan] = useState<SpPlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ total: number; remaining: number } | null>(null);
  const [result, setResult] = useState<SpImportStatus | null>(null);
  const [waiting, setWaiting] = useState(0);
  const cancelRef = useRef(false);

  useEffect(() => {
    api.spConfig().then(setCfg).catch((e: Error) => setError(e.message));
    // files chosen earlier but not yet transferred (paused, or the tab was closed): offer to resume them
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
      setPage({ ...more, folders: [...page.folders, ...more.folders], files: [...page.files, ...more.files],
                other_files_hidden: page.other_files_hidden + more.other_files_hidden });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load more');
    } finally {
      setLoading(false);
    }
  }

  function toggle(p: Picked) {
    setPicked((cur) => {
      const next = new Map(cur);
      if (next.has(p.id)) next.delete(p.id); else next.set(p.id, p);
      return next;
    });
    setPlan(null);
  }

  function selectionBody(items: Picked[]): SpSelection {
    return {
      drive_id: driveId,
      items: items.map((i) => ({ id: i.id, kind: i.kind })),
      recursive,
      include_possible_duplicates: includeDups,
      filters: {
        name_contains: nameContains.trim() || undefined,
        modified_after: modifiedAfter ? new Date(modifiedAfter).toISOString() : undefined,
      },
    };
  }

  const currentFolder: Picked = useMemo(() => {
    const last = crumbs[crumbs.length - 1];
    return { id: last.id ?? 'root', kind: 'folder', name: last.name };
  }, [crumbs]);

  const chosen = useMemo(() => Array.from(picked.values()), [picked]);

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

  // Move staged files in short steps until none remain (or Pause). Used by Import and by Resume.
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
    return <div className="rounded-xl border border-slate-200 bg-white p-6 text-[13px] text-slate-500">{error || 'Checking SharePoint connection…'}</div>;
  }
  if (!cfg.configured) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-[13.5px] text-amber-900 space-y-2">
        <div className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-4 w-4" /> SharePoint is not connected yet</div>
        <p>An admin needs to set the Azure app credentials and the SharePoint site on the server
          (<code>AZURE_TENANT_ID</code>, <code>AZURE_CLIENT_ID</code>, <code>AZURE_CLIENT_SECRET</code>, <code>SHAREPOINT_SITE_ID</code>).
          See <code>docs/SHAREPOINT.md</code>.</p>
      </div>
    );
  }

  const wantedItems = chosen.length > 0 ? chosen : null;

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-6 shadow-sm shadow-slate-100/50 space-y-5">
      <div className="flex items-center gap-2.5 border-b border-slate-100 pb-4">
        <Cloud className="h-5 w-5 text-blue-600" />
        <div>
          <h2 className="text-[16px] font-bold text-slate-900">Import from SharePoint</h2>
          <p className="text-[12px] text-slate-500">Pick decks or folders, preview what is new, then import. Nothing is imported until you confirm.</p>
        </div>
      </div>

      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</p>}

      {waiting > 0 && !running && (
        <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[13px] text-amber-900">
          <span>{waiting} deck(s) are chosen but not transferred yet.</span>
          <button type="button" onClick={() => void drain(undefined, waiting)}
            className="rounded-md bg-amber-600 px-3 py-1 font-semibold text-white hover:bg-amber-700 cursor-pointer">Resume</button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 text-[13px]">
        {drives.length > 1 && (
          <select value={driveId} onChange={(e) => { setDriveId(e.target.value); setPicked(new Map()); void open(e.target.value, [{ id: null, name: 'Library root' }]); }}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5" aria-label="Library">
            {drives.map((d) => <option key={d.id} value={d.id}>{d.name || d.id}</option>)}
          </select>
        )}
        <nav className="flex flex-wrap items-center gap-1 text-slate-600" aria-label="Folder path">
          {crumbs.map((c, i) => (
            <span key={`${c.id}-${i}`} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-slate-400" />}
              <button type="button" disabled={i === crumbs.length - 1}
                onClick={() => void open(driveId, crumbs.slice(0, i + 1))}
                className={i === crumbs.length - 1 ? 'font-semibold text-slate-900' : 'text-blue-700 hover:underline cursor-pointer'}>{c.name}</button>
            </span>
          ))}
        </nav>
        {crumbs.length > 1 && (
          <button type="button" onClick={() => void open(driveId, crumbs.slice(0, -1))}
            className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800 cursor-pointer"><ArrowLeft className="h-3.5 w-3.5" /> Up</button>
        )}
        <button type="button" onClick={() => void preview([currentFolder])} disabled={planning || running}
          className="ml-auto rounded-lg border border-blue-200 bg-blue-50 px-3 py-1.5 font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-50 cursor-pointer">
          Preview this whole folder
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-lg bg-slate-50 px-3.5 py-2.5 text-[12.5px] text-slate-700">
        <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={recursive} onChange={(e) => { setRecursive(e.target.checked); setPlan(null); }} /> Include sub-folders</label>
        <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={includeDups} onChange={(e) => { setIncludeDups(e.target.checked); setPlan(null); }} /> Include possible duplicates</label>
        <label className="flex items-center gap-1.5">Name contains
          <input value={nameContains} onChange={(e) => { setNameContains(e.target.value); setPlan(null); }} className="w-36 rounded border border-slate-200 px-2 py-1" placeholder="e.g. finals" /></label>
        <label className="flex items-center gap-1.5">Modified after
          <input type="date" value={modifiedAfter} onChange={(e) => { setModifiedAfter(e.target.value); setPlan(null); }} className="rounded border border-slate-200 px-2 py-1" /></label>
      </div>

      <div className="rounded-lg border border-slate-200">
        {loading && !page && <p className="p-4 text-[13px] text-slate-500 flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>}
        {page && (
          <ul className="divide-y divide-slate-100">
            {page.folders.map((f) => (
              <li key={f.id} className="flex items-center gap-3 px-3.5 py-2.5">
                <input type="checkbox" checked={picked.has(f.id)} onChange={() => toggle({ id: f.id, kind: 'folder', name: f.name || 'Folder' })} aria-label={`Select folder ${f.name}`} />
                <Folder className="h-4 w-4 text-amber-500" />
                <button type="button" onClick={() => void open(driveId, [...crumbs, { id: f.id, name: f.name || 'Folder' }])}
                  className="text-[13.5px] font-medium text-slate-800 hover:underline cursor-pointer">{f.name}</button>
                {typeof f.child_count === 'number' && <span className="text-[11.5px] text-slate-400">{f.child_count} items</span>}
              </li>
            ))}
            {page.files.map((f) => (
              <li key={f.id} className="flex items-center gap-3 px-3.5 py-2.5">
                <input type="checkbox" checked={picked.has(f.id)} onChange={() => toggle({ id: f.id, kind: 'file', name: f.name })} aria-label={`Select ${f.name}`} />
                <FileText className="h-4 w-4 text-slate-400" />
                <span className="min-w-0 flex-1 truncate text-[13.5px] text-slate-800">{f.name}</span>
                <span className="text-[11.5px] text-slate-400">{mb(f.size)}</span>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[f.status].cls}`}>{STATUS_STYLE[f.status].label}</span>
              </li>
            ))}
            {page.folders.length === 0 && page.files.length === 0 && <li className="p-4 text-[13px] text-slate-500">No folders or decks here.</li>}
          </ul>
        )}
        {page?.next_url && (
          <button type="button" onClick={loadMore} disabled={loading} className="w-full border-t border-slate-100 py-2 text-[13px] font-medium text-slate-700 hover:bg-slate-50 cursor-pointer">Load more</button>
        )}
        {page && page.other_files_hidden > 0 && (
          <p className="border-t border-slate-100 px-3.5 py-2 text-[11.5px] text-slate-400">{page.other_files_hidden} other file(s) hidden (only {cfg.supported_extensions.join(', ')} can be imported).</p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] text-slate-600">{chosen.length} selected</span>
        <button type="button" disabled={!wantedItems || planning || running} onClick={() => wantedItems && void preview(wantedItems)}
          className="rounded-lg bg-slate-900 px-3.5 py-2 text-[13px] font-semibold text-white hover:bg-slate-700 disabled:opacity-40 cursor-pointer">
          {planning ? 'Checking…' : 'Preview selected'}
        </button>
        {chosen.length > 0 && <button type="button" onClick={() => { setPicked(new Map()); setPlan(null); }} className="text-[13px] text-slate-500 hover:text-slate-800 cursor-pointer">Clear</button>}
      </div>

      {plan && (
        <div className="rounded-lg border border-blue-200 bg-blue-50/40 p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <span className="font-semibold text-slate-900">{plan.total_found} deck(s) found</span>
            {Object.entries(plan.counts).map(([k, n]) => (
              <span key={k} className={`rounded-full border px-2 py-0.5 font-semibold ${STATUS_STYLE[k as SpItemStatus]?.cls ?? ''}`}>{STATUS_STYLE[k as SpItemStatus]?.label ?? k}: {n}</span>
            ))}
          </div>
          {plan.truncated && <p className="text-[12.5px] text-amber-800">Large selection: only the first part was scanned. Import it, then preview again to continue.</p>}
          <ul className="max-h-56 overflow-y-auto divide-y divide-blue-100 text-[12.5px]">
            {plan.files.map((f) => (
              <li key={f.id} className="flex items-center gap-2 py-1.5">
                {f.will_import ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <span className="w-3.5" />}
                <span className="min-w-0 flex-1 truncate text-slate-800">{f.folder_path ? `${f.folder_path}/` : ''}{f.name}</span>
                <span className="text-slate-400">{mb(f.size)}</span>
                <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[f.status].cls}`}>{STATUS_STYLE[f.status].label}</span>
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-3">
            <button type="button" disabled={running || plan.to_import === 0}
              onClick={() => void runImport(wantedItems ?? [currentFolder])}
              className="rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-700 disabled:opacity-40 cursor-pointer">
              {running ? 'Importing…' : `Import ${plan.to_import} deck${plan.to_import === 1 ? '' : 's'} (${mb(plan.bytes_to_import)})`}
            </button>
            {plan.to_import === 0 && <span className="text-[12.5px] text-slate-500">Nothing new to import here.</span>}
          </div>
        </div>
      )}

      {progress && running && (
        <div className="space-y-1.5" aria-live="polite">
          <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
            <div className="h-full bg-blue-600 transition-all" style={{ width: `${progress.total ? ((progress.total - progress.remaining) / progress.total) * 100 : 0}%` }} />
          </div>
          <div className="flex items-center justify-between text-[12.5px] text-slate-600">
            <span>{progress.total - progress.remaining} of {progress.total} transferred</span>
            <button type="button" onClick={() => { cancelRef.current = true; }} className="text-slate-500 hover:text-slate-800 cursor-pointer">Pause</button>
          </div>
        </div>
      )}

      {result && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50/50 p-4 text-[13px] space-y-1.5">
          <p className="font-semibold text-emerald-800">Import step finished. Questions are now being read in the background; track them on the Uploads page.</p>
          <p className="text-slate-700">{Object.entries(result.counts).map(([k, n]) => `${k.toLowerCase().replace('_', ' ')}: ${n}`).join(' · ')}</p>
          {result.remaining > 0 && <p className="text-amber-800">{result.remaining} file(s) are still waiting (paused). Use Resume above to continue.</p>}
          {result.skipped_duplicates.map((s) => <p key={s.id} className="text-slate-600">Skipped {s.name}: {s.message}</p>)}
          {result.failed.map((f) => <p key={f.id} className="text-red-700">Failed {f.name}: {f.error}</p>)}
        </div>
      )}
    </div>
  );
}
