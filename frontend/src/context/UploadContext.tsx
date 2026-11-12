'use client';

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
} from 'react';
import { getClientId } from '../lib/clientId';
import { api, isAbortError, UPLOAD_CHUNK_SIZE, VERCEL_SAFE_CHUNK_SIZE, UploadProgressInfo } from '../lib/api';
import {
  DocumentItem,
  BatchLocalItem,
  BatchTotals,
  BatchItemPhase,
} from '../lib/types';

export interface PipelineProgress {
  step: string;
  percentage: number;
  slides: number;
  questions: number;
  status: string; // 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  detail?: string | null;
  events?: { ts: number; msg: string }[];
  memory?: { cur?: number | null; peak?: number | null; limit?: number | null };
  error?: string | null;
}

export interface UploadTiming {
  startedAt: number;
  uploadEndedAt: number | null;
  finishedAt: number | null;
}

interface UploadContextType {
  // Batch state
  activeBatchId: string | null;
  items: BatchLocalItem[];
  batchTotals: BatchTotals | null;
  summary: string;
  // Detail / processing item state
  uploading: boolean;
  activeDocId: string | null;
  activeFileName: string | null;
  pipelineProgress: PipelineProgress | null;
  uploadDetail: UploadProgressInfo | null;
  cancelling: boolean;
  timing: UploadTiming | null;
  showLog: boolean;
  setShowLog: React.Dispatch<React.SetStateAction<boolean>>;
  version: number;
  // Actions
  handleFileUpload: (file: File) => Promise<void>;
  handleMultiFileUpload: (files: File[]) => Promise<void>;
  handleRetry: (jobId: string) => Promise<void>;
  handleCancelBatch: () => Promise<void>;
  handleCancelItem: (clientItemId: string) => Promise<void>;
  handleCancelPending: () => Promise<void>;
  handleCancel: () => Promise<void>;
  dismissProgress: () => void;
  fmtMB: (bytes: number) => string;
  fmtDuration: (ms: number) => string;
}

const UploadContext = createContext<UploadContextType | null>(null);

const STORAGE_BATCH_KEY = 'qshala_active_upload_batch';
const STORAGE_ACTIVE_KEY = 'qshala_active_upload';

function generateClientItemId(prefix = 'deck'): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `${prefix}_${crypto.randomUUID().slice(0, 8)}`;
  }
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
}

export function UploadProvider({ children }: { children: React.ReactNode }) {
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [items, setItems] = useState<BatchLocalItem[]>([]);
  const itemsRef = useRef<BatchLocalItem[]>(items);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  const [batchTotals, setBatchTotals] = useState<BatchTotals | null>(null);

  const [activeDocId, setActiveDocId] = useState<string | null>(null);
  const [activeFileName, setActiveFileName] = useState<string | null>(null);
  const [pipelineProgress, setPipelineProgress] = useState<PipelineProgress | null>(null);
  const [uploadDetail, setUploadDetail] = useState<UploadProgressInfo | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [version, setVersion] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const uploadIdRef = useRef<string | null>(null);
  const isUploadingRef = useRef(false);
  const [triggerNextUpload, setTriggerNextUpload] = useState(0);

  const [timing, setTiming] = useState<UploadTiming | null>(null);
  const [, setTick] = useState(0);

  // Send batch heartbeat every 20s while activeBatchId is active to prevent abandoned status
  // It stops once every item is finished (the batch id stays set for the summary screen, but nothing is left to keep alive).
  const batchHasWork = items.some(
    (it) => it.phase === 'UPLOADING' || it.phase === 'WAITING' || it.phase === 'PROCESSING' || it.phase === 'QUEUED'
  );
  useEffect(() => {
    if (!activeBatchId || !batchHasWork) return;
    const send = () => {
      api.sendBatchHeartbeat(activeBatchId).catch(() => {});
    };
    send();
    const interval = setInterval(send, 20000);
    return () => clearInterval(interval);
  }, [activeBatchId, batchHasWork]);

  // Formatting helpers
  const fmtMB = useCallback((bytes: number) => (bytes / (1024 * 1024)).toFixed(1), []);
  const fmtDuration = useCallback((ms: number) => {
    const total = Math.max(0, Math.round(ms / 1000));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return h > 0
      ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
      : `${m}:${String(s).padStart(2, '0')}`;
  }, []);

  const stopClock = useCallback(() => {
    setTiming((t) => (t && !t.finishedAt ? { ...t, finishedAt: Date.now() } : t));
  }, []);

  // Live stopwatch tick
  useEffect(() => {
    if (!timing || timing.finishedAt) return;
    const id = setInterval(() => setTick((t) => t + 1), 500);
    return () => clearInterval(id);
  }, [timing]);

  // Tab unload warning: ONLY while an item has phase === 'UPLOADING' (bytes in flight)
  const uploading = useMemo(() => items.some((it) => it.phase === 'UPLOADING'), [items]);
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (uploading) {
        e.preventDefault();
        e.returnValue = 'An upload is currently sending bytes to the server. Leaving will cancel the upload.';
        return e.returnValue;
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [uploading]);

  // Clear, accurate summary string:
  // e.g. "3 of 18 done · 1 processing · 4 queued · 1 uploading · 9 waiting to upload"
  const summary = useMemo(() => {
    const total = items.length;
    if (total === 0) return 'No active batch';
    const completed = items.filter((it) => it.phase === 'COMPLETED').length;
    const processing = items.filter((it) => it.phase === 'PROCESSING').length;
    const queued = items.filter((it) => it.phase === 'QUEUED').length;
    const uploadingCount = items.filter((it) => it.phase === 'UPLOADING').length;
    const waitingCount = items.filter((it) => it.phase === 'WAITING').length;
    const failed = items.filter((it) => it.phase === 'FAILED').length;
    const cancelled = items.filter((it) => it.phase === 'CANCELLED').length;

    const parts = [`${completed} of ${total} done`];
    if (processing > 0) parts.push(`${processing} processing`);
    if (queued > 0) parts.push(`${queued} queued`);
    if (uploadingCount > 0) parts.push(`${uploadingCount} uploading bytes`);
    if (waitingCount > 0) parts.push(`${waitingCount} waiting to upload`);
    if (failed > 0) parts.push(`${failed} failed`);
    if (cancelled > 0) parts.push(`${cancelled} cancelled`);

    return parts.join(' · ');
  }, [items]);

  // Auto-reconnect to any active batch on initial load or reload
  useEffect(() => {
    let isMounted = true;
    const resumeOngoing = async () => {
      try {
        let savedBatchId: string | null = null;
        try {
          const raw = localStorage.getItem(STORAGE_BATCH_KEY);
          if (raw) savedBatchId = JSON.parse(raw).batchId;
        } catch {
          // ignore localStorage parsing errors
        }

        const activeRes = await api.getActiveUploadBatch();
        const batchIdToAdopt = activeRes.batch_id || savedBatchId;

        if (batchIdToAdopt && isMounted) {
          const status = await api.getUploadBatch(batchIdToAdopt);
          if (!isMounted) return;
          setActiveBatchId(status.batch_id);
          setBatchTotals(status.totals);
          const mappedItems: BatchLocalItem[] = status.items.map((it) => ({
            clientItemId: it.client_item_id,
            filename: it.filename,
            size: it.size_bytes || 0,
            phase: it.status as BatchItemPhase,
            docId: it.document_id,
            jobId: it.job_id,
            queuePosition: it.queue_position,
            slides: it.slides,
            questions: it.questions,
            step: it.step,
            percentage: it.percentage,
            error: it.error,
          }));
          setItems(mappedItems);

          const procItem = status.items.find((it) => it.status === 'PROCESSING');
          if (procItem && procItem.document_id) {
            setActiveDocId(procItem.document_id);
            setActiveFileName(procItem.filename);
            setPipelineProgress({
              step: procItem.step || 'Processing presentation...',
              percentage: procItem.percentage || 25,
              slides: procItem.slides || 0,
              questions: procItem.questions || 0,
              status: 'PROCESSING',
              error: procItem.error,
            });
            setTiming((t) => t || { startedAt: Date.now() - 15000, uploadEndedAt: Date.now() - 15000, finishedAt: null });
          }
        }
      } catch (err) {
        console.warn('Could not auto-resume background batch upload:', err);
      }
    };

    resumeOngoing();
    return () => {
      isMounted = false;
    };
  }, []);

  // Poll batch status using ref for items so poll interval is not torn down by doc polling (F7 fix)
  useEffect(() => {
    if (!activeBatchId) return;

    let isMounted = true;
    let timerId: ReturnType<typeof setTimeout> | null = null;

    const pollBatch = async () => {
      const currentItems = itemsRef.current;
      const isAllTerminal =
        currentItems.length > 0 &&
        currentItems.every(
          (it) => it.phase === 'COMPLETED' || it.phase === 'FAILED' || it.phase === 'CANCELLED'
        );
      if (isAllTerminal) return;

      // A hidden tab does not need live progress; check again later instead of sending requests nobody sees.
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        if (isMounted) timerId = setTimeout(pollBatch, 8000);
        return;
      }

      try {
        const batchStatus = await api.getUploadBatch(activeBatchId);
        if (!isMounted) return;
        setBatchTotals(batchStatus.totals);

        setItems((prev) => {
          const serverMap = new Map(batchStatus.items.map((s) => [s.client_item_id, s]));
          return prev.map((local) => {
            const server = serverMap.get(local.clientItemId);
            if (!server) return local;

            // Preserve local UPLOADING phase & percentage while file bytes are in-flight
            if (local.phase === 'UPLOADING') return local;

            // Prevent a delayed server WAITING status from clobbering an item whose bytes
            // have already finished uploading (it is at least QUEUED)
            if (server.status === 'WAITING' && (local.docId || local.phase === 'QUEUED' || local.phase === 'PROCESSING')) {
              return local;
            }

            // Don't overwrite local CANCELLED state if user just cancelled locally
            if (local.phase === 'CANCELLED' && server.status === 'WAITING') {
              return local;
            }

            return {
              ...local,
              filename: server.filename || local.filename,
              phase: (server.status === 'CANCELLING' ? 'CANCELLED' : server.status) as BatchItemPhase,
              docId: server.document_id || local.docId,
              jobId: server.job_id || local.jobId,
              queuePosition: server.queue_position,
              slides: server.slides,
              questions: server.questions,
              step: server.step || local.step,
              percentage:
                server.status === 'PROCESSING'
                  ? Math.max(local.percentage, server.percentage || 0)
                  : server.status === 'COMPLETED'
                  ? 100
                  : server.percentage || 0,
              error: server.error || local.error,
            };
          });
        });

        const proc = batchStatus.items.find((it) => it.status === 'PROCESSING');
        if (proc && proc.document_id) {
          setActiveDocId(proc.document_id);
          setActiveFileName(proc.filename);
        } else {
          setActiveDocId(null);
        }

        const allDone = batchStatus.items.every(
          (it) => it.status === 'COMPLETED' || it.status === 'FAILED' || it.status === 'CANCELLED'
        );
        if (allDone) {
          setVersion((v) => v + 1);
          stopClock();
        }
      } catch (err) {
        console.warn('Batch polling error:', err);
      }

      if (isMounted) {
        const nextItems = itemsRef.current;
        const anyActive = nextItems.some(
          (it) => it.phase === 'UPLOADING' || it.phase === 'WAITING' || it.phase === 'PROCESSING' || it.phase === 'QUEUED'
        );
        const delay = anyActive ? 3000 : 8000;
        timerId = setTimeout(pollBatch, delay);
      }
    };

    timerId = setTimeout(pollBatch, 1000);

    return () => {
      isMounted = false;
      if (timerId) clearTimeout(timerId);
    };
  }, [activeBatchId, stopClock]);

  // Poll single active document every 1.2s while PROCESSING to feed smooth progress and activity log
  useEffect(() => {
    if (!activeDocId) return;

    // A chained setTimeout (not setInterval) so a slow response can never overlap the next request. The wait is
    // 1.2 s normally, 3 s after an error, and a hidden tab skips the request altogether.
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (cancelled) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        timer = setTimeout(tick, 3000);
        return;
      }
      let nextDelay = 1200;
      try {
        const status = await api.getIngestionStatus(activeDocId);
        if (cancelled) return;
        setPipelineProgress({
          step: status.current_step,
          percentage: status.progress_percentage,
          slides: status.slides_processed,
          questions: status.questions_extracted,
          status: status.status,
          detail: status.detail,
          events: status.events || [],
          memory: { cur: status.memory_mb, peak: status.peak_memory_mb, limit: status.memory_limit_mb },
          error: status.error,
        });

        setItems((prev) =>
          prev.map((it) =>
            it.docId === activeDocId
              ? {
                  ...it,
                  phase:
                    status.status === 'COMPLETED'
                      ? ('COMPLETED' as BatchItemPhase)
                      : status.status === 'FAILED'
                      ? ('FAILED' as BatchItemPhase)
                      : status.status === 'CANCELLED'
                      ? ('CANCELLED' as BatchItemPhase)
                      : ('PROCESSING' as BatchItemPhase),
                  percentage: status.progress_percentage,
                  step: status.current_step,
                  slides: status.slides_processed,
                  questions: status.questions_extracted,
                  error: status.error || it.error,
                }
              : it
          )
        );

        if (status.status === 'COMPLETED' || status.status === 'FAILED' || status.status === 'CANCELLED') {
          setActiveDocId(null);
          setVersion((v) => v + 1);
          return;
        }
      } catch (e) {
        console.error('Document ingestion polling error:', e);
        nextDelay = 3000;
      }
      if (!cancelled) timer = setTimeout(tick, nextDelay);
    };

    timer = setTimeout(tick, 1200);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeDocId]);

  // Serial client uploader: upload one file at a time from items queue
  useEffect(() => {
    if (!activeBatchId) return;
    if (isUploadingRef.current) return;

    const nextItem = items.find((it) => it.phase === 'WAITING' && it.file);
    if (!nextItem || !nextItem.file) return;

    const uploadNextFile = async (item: BatchLocalItem) => {
      isUploadingRef.current = true;
      const file = item.file!;
      const controller = new AbortController();
      abortRef.current = controller;
      uploadIdRef.current = null;

      setItems((prev) =>
        prev.map((it) =>
          it.clientItemId === item.clientItemId
            ? { ...it, phase: 'UPLOADING' as BatchItemPhase, percentage: 5, step: 'Starting upload...' }
            : it
        )
      );

      const isLocalhost =
        typeof window !== 'undefined' &&
        (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');
      const hasDirectUpload = Boolean(process.env.NEXT_PUBLIC_UPLOAD_API_URL);
      const effectiveChunkSize = isLocalhost || hasDirectUpload ? UPLOAD_CHUNK_SIZE : VERCEL_SAFE_CHUNK_SIZE;
      const directUploadLimit = isLocalhost ? 100 * 1024 * 1024 : VERCEL_SAFE_CHUNK_SIZE;

      try {
        let doc: DocumentItem;
        if (file.size > directUploadLimit) {
          doc = await api.uploadDocumentChunked(
            file,
            (pct, _cur, _tot, info) => {
              setUploadDetail(info);
              setItems((prev) =>
                prev.map((it) =>
                  it.clientItemId === item.clientItemId
                    ? {
                        ...it,
                        percentage: Math.min(Math.round(5 + pct * 0.9), 98),
                        step: `Uploading file bytes (${fmtMB(info.bytesSent)} / ${fmtMB(info.totalBytes)} MB - ${pct}%)...`,
                      }
                    : it
                )
              );
            },
            undefined,
            undefined,
            effectiveChunkSize,
            {
              signal: controller.signal,
              batchId: activeBatchId,
              clientItemId: item.clientItemId,
              onUploadId: (id) => {
                uploadIdRef.current = id;
              },
            }
          );
        } else {
          doc = await api.uploadDocument(file, undefined, undefined, {
            signal: controller.signal,
            batchId: activeBatchId,
            clientItemId: item.clientItemId,
            onProgress: (info) => {
              setUploadDetail(info);
              const pct = info.totalBytes > 0 ? Math.round((info.bytesSent / info.totalBytes) * 100) : 50;
              setItems((prev) =>
                prev.map((it) =>
                  it.clientItemId === item.clientItemId
                    ? {
                        ...it,
                        percentage: Math.min(pct, 98),
                        step: `Uploading file bytes (${fmtMB(info.bytesSent)} / ${fmtMB(info.totalBytes)} MB - ${pct}%)...`,
                      }
                    : it
                )
              );
            },
          });
        }

        // Upload complete for this file -> now queued on server for AI ingestion
        setItems((prev) =>
          prev.map((it) =>
            it.clientItemId === item.clientItemId
              ? {
                  ...it,
                  phase: 'QUEUED' as BatchItemPhase,
                  docId: doc.id,
                  percentage: 0,
                  step: 'Uploaded to server. Queued for AI processing.',
                  file: undefined,
                }
              : it
          )
        );
        setVersion((v) => v + 1);
      } catch (err: unknown) {
        if (isAbortError(err)) {
          setItems((prev) =>
            prev.map((it) =>
              it.clientItemId === item.clientItemId
                ? { ...it, phase: 'CANCELLED' as BatchItemPhase, step: 'Upload cancelled by user', file: undefined }
                : it
            )
          );
        } else {
          const errMsg = err instanceof Error ? err.message : 'Upload failed';
          console.error(`Upload failed for ${file.name}:`, err);
          setItems((prev) =>
            prev.map((it) =>
              it.clientItemId === item.clientItemId
                ? { ...it, phase: 'FAILED' as BatchItemPhase, error: errMsg, file: it.file || file } // Retain file for retry (F8)
                : it
            )
          );
        }
      } finally {
        setUploadDetail(null);
        abortRef.current = null;
        uploadIdRef.current = null;
        isUploadingRef.current = false;
        setTriggerNextUpload((n) => n + 1);
      }
    };

    uploadNextFile(nextItem);
  }, [activeBatchId, items, triggerNextUpload, fmtMB]);

  // Multi-file upload entry point
  const handleMultiFileUpload = async (files: File[]) => {
    if (!files || files.length === 0) return;
    const cappedFiles = files.slice(0, 25);

    const initialItems: BatchLocalItem[] = cappedFiles.map((file) => ({
      clientItemId: generateClientItemId('deck'),
      file,
      filename: file.name,
      size: file.size,
      phase: 'WAITING',
      percentage: 0,
      slides: 0,
      questions: 0,
    }));

    setTiming({ startedAt: Date.now(), uploadEndedAt: null, finishedAt: null });
    setCancelling(false);

    try {
      const regPayload = {
        files: initialItems.map((it) => ({
          client_item_id: it.clientItemId,
          filename: it.filename,
          size_bytes: it.size,
        })),
        client_id: getClientId(),
      };

      const res = await api.createUploadBatch(regPayload);
      setActiveBatchId(res.batch_id);
      setItems(initialItems);
      setBatchTotals({
        total: initialItems.length,
        completed: 0,
        processing: 0,
        queued: 0,
        waiting: initialItems.length,
        failed: 0,
        cancelled: 0,
      });

      try {
        localStorage.setItem(STORAGE_BATCH_KEY, JSON.stringify({ batchId: res.batch_id }));
      } catch {
        // ignore
      }

      setTriggerNextUpload((n) => n + 1);
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : 'Failed to register upload batch';
      alert(errMsg);
      console.error('Failed to create upload batch:', err);
    }
  };

  // Single-file backward compatibility wrapper
  const handleFileUpload = async (file: File) => {
    await handleMultiFileUpload([file]);
  };

  // Retry a failed item (client-side upload failure or server-side ingestion failure)
  const handleRetry = async (id: string) => {
    const item = itemsRef.current.find((it) => it.clientItemId === id || it.jobId === id);
    if (!item) return;

    if (item.file) {
      // Client-side retry with retained File object
      setItems((prev) =>
        prev.map((it) =>
          it.clientItemId === item.clientItemId
            ? { ...it, phase: 'WAITING' as BatchItemPhase, error: null, percentage: 0, step: 'Retrying upload...' }
            : it
        )
      );
      setTriggerNextUpload((n) => n + 1);
      return;
    }

    if (item.jobId) {
      try {
        await api.retryJob(item.jobId);
        setItems((prev) =>
          prev.map((it) =>
            it.clientItemId === item.clientItemId
              ? { ...it, phase: 'QUEUED' as BatchItemPhase, error: null, percentage: 0, step: 'Queued for retry' }
              : it
          )
        );
        setVersion((v) => v + 1);
      } catch (err) {
        console.error('Failed to retry job:', err);
      }
    }
  };

  // Cancel remaining QUEUED jobs in the batch
  const handleCancelPending = async () => {
    if (!activeBatchId) return;
    try {
      await api.cancelPendingBatch(activeBatchId);
      setItems((prev) =>
        prev.map((it) =>
          it.phase === 'QUEUED'
            ? { ...it, phase: 'CANCELLED' as BatchItemPhase, step: 'Cancelled by user' }
            : it
        )
      );
      setVersion((v) => v + 1);
    } catch (err) {
      console.error('Failed to cancel remaining jobs:', err);
    }
  };

  // Cancel entire batch (active byte upload, active processing document, all queued jobs, and waiting items)
  const handleCancelBatch = async () => {
    if (cancelling) return;
    setCancelling(true);
    try {
      // 1. Abort active byte upload if any
      if (abortRef.current) {
        abortRef.current.abort();
        const id = uploadIdRef.current;
        if (id) {
          try {
            await api.cancelChunkedUpload(id);
          } catch {
            // ignore
          }
        }
      }

      // 2. Cancel active processing document if any
      if (activeDocId) {
        try {
          await api.cancelIngestion(activeDocId);
        } catch (e) {
          console.warn('Failed to cancel active ingestion:', e);
        }
        setActiveDocId(null);
      }

      // 3. Cancel all jobs on server for this batch
      if (activeBatchId) {
        try {
          await api.cancelBatch(activeBatchId);
        } catch (e) {
          console.warn('Failed to cancel batch on server:', e);
        }
      }

      // 4. Mark all non-terminal items as CANCELLED in local state
      setItems((prev) =>
        prev.map((it) =>
          it.phase === 'COMPLETED' || it.phase === 'FAILED'
            ? it
            : { ...it, phase: 'CANCELLED' as BatchItemPhase, step: 'Cancelled by user', file: undefined }
        )
      );

      setVersion((v) => v + 1);
      stopClock();
    } finally {
      setCancelling(false);
    }
  };

  // Cancel a single item in the batch (waiting, uploading, or queued)
  const handleCancelItem = async (clientItemId: string) => {
    const target = items.find((it) => it.clientItemId === clientItemId);
    if (!target) return;

    if (target.phase === 'UPLOADING') {
      if (abortRef.current) abortRef.current.abort();
      const id = uploadIdRef.current;
      if (id) {
        api.cancelChunkedUpload(id).catch(() => {});
      }
      return;
    }

    if (target.phase === 'PROCESSING' && target.docId) {
      try {
        await api.cancelIngestion(target.docId);
      } catch (e) {
        console.warn('Failed to cancel document ingestion:', e);
      }
      setActiveDocId(null);
    }

    if (target.phase === 'QUEUED' && target.jobId) {
      try {
        await api.cancelJob(target.jobId);
      } catch (e) {
        console.warn('Failed to cancel job:', e);
      }
    }

    setItems((prev) =>
      prev.map((it) =>
        it.clientItemId === clientItemId
          ? { ...it, phase: 'CANCELLED' as BatchItemPhase, step: 'Cancelled by user', file: undefined }
          : it
      )
    );
    setVersion((v) => v + 1);
  };

  // Cancel active byte upload or processing deck
  const handleCancel = async () => {
    if (cancelling) return;
    setCancelling(true);
    try {
      if (abortRef.current) {
        abortRef.current.abort();
        const id = uploadIdRef.current;
        if (id) await api.cancelChunkedUpload(id);
      } else if (activeDocId) {
        const res = await api.cancelIngestion(activeDocId);
        if (res.status === 'CANCELLED') {
          setActiveDocId(null);
          setVersion((v) => v + 1);
        }
      }
    } catch (e) {
      console.error('Cancel upload error:', e);
    } finally {
      setCancelling(false);
    }
  };

  const dismissProgress = () => {
    setPipelineProgress(null);
    setTiming(null);
    setActiveFileName(null);
    setActiveBatchId(null);
    setItems([]);
    setBatchTotals(null);
    try {
      localStorage.removeItem(STORAGE_BATCH_KEY);
      localStorage.removeItem(STORAGE_ACTIVE_KEY);
    } catch {
      // ignore
    }
  };

  return (
    <UploadContext.Provider
      value={{
        activeBatchId,
        items,
        batchTotals,
        summary,
        uploading,
        activeDocId,
        activeFileName,
        pipelineProgress,
        uploadDetail,
        cancelling,
        timing,
        showLog,
        setShowLog,
        version,
        handleFileUpload,
        handleMultiFileUpload,
        handleRetry,
        handleCancelBatch,
        handleCancelItem,
        handleCancelPending,
        handleCancel,
        dismissProgress,
        fmtMB,
        fmtDuration,
      }}
    >
      {children}
    </UploadContext.Provider>
  );
}

export function useUpload() {
  const ctx = useContext(UploadContext);
  if (!ctx) {
    throw new Error('useUpload must be used within an UploadProvider');
  }
  return ctx;
}
