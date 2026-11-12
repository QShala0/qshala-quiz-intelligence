'use client';

import { useState, useEffect, useRef } from 'react';
import {
  UploadCloud,
  FileText,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Loader2,
  RotateCcw,
  Clock,
  X,
} from 'lucide-react';
import { api } from '../../lib/api';
import { DocumentItem, Slide } from '../../lib/types';
import SlideViewerModal from '../../components/SlideViewerModal';
import { useUpload } from '../../context/UploadContext';

export default function UploadPage() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSlide, setSelectedSlide] = useState<Slide | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    activeBatchId,
    items,
    summary,
    uploading,
    activeDocId,
    pipelineProgress,
    uploadDetail,
    cancelling,
    timing,
    showLog,
    setShowLog,
    version,
    handleMultiFileUpload,
    handleRetry,
    handleCancelBatch,
    handleCancelItem,
    handleCancelPending,
    handleCancel,
    dismissProgress,
    fmtMB,
    fmtDuration,
  } = useUpload();

  const cleanSteps = [
    'Uploading',
    'Extracting slides',
    'Identifying questions',
    'Indexing knowledge',
    'Ready',
  ];

  // P1-2: Load documents on initial mount, and on version bump only when an upload newly completes
  const prevItemsRef = useRef(items);
  const isInitialMount = useRef(true);

  useEffect(() => {
    let ignore = false;
    async function loadDocs() {
      try {
        const data = await api.listDocuments();
        if (!ignore) setDocuments(data);
      } catch (err) {
        console.error(err);
      } finally {
        if (!ignore) setLoading(false);
      }
    }

    if (isInitialMount.current) {
      isInitialMount.current = false;
      loadDocs();
      return () => {
        ignore = true;
      };
    }

    // Only refetch when an item in the upload queue newly transitions to 'COMPLETED'
    const hasNewlyCompleted = items.some(
      (item) => item.phase === 'COMPLETED' && !prevItemsRef.current.some((p) => p.clientItemId === item.clientItemId && p.phase === 'COMPLETED')
    );
    prevItemsRef.current = items;

    if (hasNewlyCompleted) {
      loadDocs();
    }

    return () => {
      ignore = true;
    };
  }, [version, items]);

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this historical document?')) return;
    try {
      await api.deleteDocument(id);
      // P1-2: Update local state immediately instead of refetching the entire list
      setDocuments((prev) => prev.filter((d) => d.id !== id));
    } catch (err) {
      console.error(err);
    }
  };

  const handleSelectedFiles = (files: File[]) => {
    const validFiles = files.filter((f) => {
      const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
      return ['.pptx', '.ppt', '.pdf'].includes(ext);
    });
    if (validFiles.length === 0) {
      alert('Please select PowerPoint (.pptx, .ppt) or PDF files.');
      return;
    }
    if (validFiles.length > 25) {
      alert('Only the first 25 files were accepted.');
    }
    handleMultiFileUpload(validFiles.slice(0, 25));
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleSelectedFiles(Array.from(e.dataTransfer.files));
    }
  };

  const hasQueuedItems = items.some((it) => it.phase === 'QUEUED');
  const hasActiveItems = items.some(
    (it) =>
      it.phase === 'WAITING' ||
      it.phase === 'UPLOADING' ||
      it.phase === 'QUEUED' ||
      it.phase === 'PROCESSING'
  );
  const isAllBatchDone =
    items.length > 0 &&
    items.every(
      (it) => it.phase === 'COMPLETED' || it.phase === 'FAILED' || it.phase === 'CANCELLED'
    );

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-6xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-[28px] sm:text-[32px] font-bold tracking-tight text-slate-900 leading-tight">
          Upload Center
        </h1>
        <p className="mt-1 text-[14px] text-slate-500 font-normal leading-relaxed">
          Upload up to 25 PPTX or PDF decks at once. Ingestion runs smoothly via a server-side queue.
        </p>
      </div>

      {/* Drag & Drop Upload Zone (supports multi-file select up to 25) */}
      <div
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-white p-10 text-center hover:border-blue-500 transition-colors cursor-pointer group shadow-sm shadow-slate-100/50"
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pptx,.ppt,.pdf"
          className="hidden"
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              handleSelectedFiles(Array.from(e.target.files));
            }
          }}
        />

        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-blue-50 text-blue-600 mb-3 group-hover:scale-105 transition-transform">
          <UploadCloud className="h-6 w-6" />
        </div>

        <h3 className="text-[16px] font-bold text-slate-900">Add quiz material (up to 25 files)</h3>
        <p className="mt-1 text-[13px] text-slate-500 max-w-sm font-normal leading-relaxed">
          Drag and drop multiple PowerPoint (<code className="font-mono text-slate-700">.pptx, .ppt</code>) or PDF files here
        </p>

        <div className="mt-4">
          <button
            type="button"
            className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-[13px] sm:text-[14px] font-semibold text-slate-700 hover:bg-slate-50 transition-colors shadow-sm cursor-pointer"
          >
            {uploading ? 'Sending files...' : 'Browse files'}
          </button>
        </div>
      </div>

      {/* Active Batch Summary & Queue Table */}
      {activeBatchId && items.length > 0 && (
        <div className="rounded-xl border border-slate-200/90 bg-white p-6 shadow-sm space-y-4 animate-in fade-in transition-all">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 pb-4">
            <div>
              <div className="flex items-center gap-2">
                <span
                  className={`flex h-2.5 w-2.5 rounded-full ${
                    isAllBatchDone
                      ? items.some((it) => it.phase === 'FAILED')
                        ? 'bg-rose-500'
                        : 'bg-emerald-500'
                      : 'bg-blue-600 animate-pulse'
                  }`}
                />
                <h3 className="text-[16px] font-bold text-slate-900">Batch Ingestion Queue</h3>
              </div>
              <p className="text-[13.5px] font-semibold text-slate-700 mt-1">{summary}</p>
            </div>

            <div className="flex items-center gap-2">
              {/* Cancel Entire Batch Button */}
              {hasActiveItems && (
                <button
                  type="button"
                  onClick={() => {
                    if (confirm('Cancel upload and processing for all unfinished decks in this batch?')) {
                      handleCancelBatch();
                    }
                  }}
                  disabled={cancelling}
                  className="rounded-lg border border-rose-300 bg-rose-50 px-3.5 py-1.5 text-[12.5px] font-semibold text-rose-700 hover:bg-rose-100 transition-colors cursor-pointer flex items-center gap-1.5"
                  title="Cancel all remaining uploads and background ingestion for this batch"
                >
                  {cancelling ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <X className="h-3.5 w-3.5" />
                  )}
                  <span>Cancel Batch</span>
                </button>
              )}

              {/* Cancel remaining queued decks only */}
              {hasQueuedItems && (
                <button
                  type="button"
                  onClick={handleCancelPending}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-600 hover:bg-slate-50 transition-colors cursor-pointer"
                  title="Cancel all queued decks in this batch that have not started processing yet"
                >
                  Cancel queued only
                </button>
              )}

              {/* Dismiss batch button when finished */}
              {isAllBatchDone && (
                <button
                  type="button"
                  onClick={dismissProgress}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-slate-600 hover:bg-slate-50 transition-colors cursor-pointer"
                >
                  Dismiss batch
                </button>
              )}
            </div>
          </div>

          {/* Scrollable file list */}
          <div className="max-h-96 overflow-y-auto divide-y divide-slate-100 rounded-lg border border-slate-100 bg-slate-50/50">
            {items.map((item, idx) => {
              const isItemCompleted = item.phase === 'COMPLETED';
              const isItemProcessing = item.phase === 'PROCESSING';
              const isItemUploading = item.phase === 'UPLOADING';
              const isItemQueued = item.phase === 'QUEUED';
              const isItemWaiting = item.phase === 'WAITING';
              const isItemFailed = item.phase === 'FAILED';
              const isItemCancelled = item.phase === 'CANCELLED';

              return (
                <div
                  key={item.clientItemId || idx}
                  className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 bg-white hover:bg-slate-50/70 transition-colors"
                >
                  {/* File info */}
                  <div className="flex items-start gap-3 min-w-0 flex-1">
                    <div className="mt-0.5 shrink-0">
                      {isItemCompleted ? (
                        <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                      ) : isItemFailed ? (
                        <AlertCircle className="h-5 w-5 text-rose-600" />
                      ) : isItemCancelled ? (
                        <X className="h-5 w-5 text-slate-400" />
                      ) : isItemProcessing ? (
                        <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
                      ) : isItemUploading ? (
                        <UploadCloud className="h-5 w-5 text-indigo-600 animate-pulse" />
                      ) : isItemQueued ? (
                        <Clock className="h-5 w-5 text-amber-600" />
                      ) : (
                        <FileText className="h-5 w-5 text-slate-400" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-semibold text-slate-900 truncate">
                          {item.filename}
                        </span>
                        {item.size > 0 && (
                          <span className="text-[11.5px] text-slate-400 font-normal">
                            ({fmtMB(item.size)} MB)
                          </span>
                        )}
                        {/* Status Badge */}
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold ${
                            isItemCompleted
                              ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                              : isItemProcessing
                              ? 'bg-blue-50 text-blue-700 border border-blue-200 animate-pulse'
                              : isItemUploading
                              ? 'bg-indigo-50 text-indigo-700 border border-indigo-200'
                              : isItemQueued
                              ? 'bg-amber-50 text-amber-800 border border-amber-200'
                              : isItemFailed
                              ? 'bg-rose-50 text-rose-700 border border-rose-200'
                              : isItemCancelled
                              ? 'bg-slate-100 text-slate-600 border border-slate-200'
                              : 'bg-slate-100 text-slate-600 border border-slate-200'
                          }`}
                        >
                          {isItemQueued
                            ? item.queuePosition !== null && item.queuePosition !== undefined
                              ? item.queuePosition === 0
                                ? 'Next in server queue'
                                : `#${item.queuePosition + 1} in server queue`
                              : 'In server queue'
                            : isItemUploading
                            ? 'Uploading bytes'
                            : isItemProcessing
                            ? 'Processing on server'
                            : isItemCompleted
                            ? 'Complete'
                            : isItemFailed
                            ? 'Failed'
                            : isItemCancelled
                            ? 'Cancelled'
                            : 'Waiting to upload'}
                        </span>
                      </div>

                      {/* Detailed sub-status or step */}
                      <p className="text-[12px] text-slate-600 truncate mt-1">
                        {isItemCompleted
                          ? `✓ Ingestion complete — ${item.slides || 0} slides processed · ${item.questions || 0} questions extracted & indexed`
                          : isItemFailed
                          ? `Error: ${item.error || 'Ingestion encountered an error'}`
                          : isItemProcessing
                          ? `${item.step || 'Processing presentation...'}${
                              item.slides ? ` (${item.slides} slides` : ''
                            }${
                              item.questions ? `, ${item.questions} questions found)` : item.slides ? ')' : ''
                            }`
                          : isItemUploading
                          ? item.step || 'Uploading file bytes to server...'
                          : isItemQueued
                          ? `Uploaded to server · Waiting for worker (${
                              item.queuePosition !== null && item.queuePosition !== undefined
                                ? item.queuePosition === 0
                                  ? 'next deck up to process'
                                  : `${item.queuePosition} deck(s) ahead in queue`
                                : 'in queue'
                            })`
                          : isItemCancelled
                          ? 'Cancelled by user'
                          : `Waiting in browser queue — next to upload (${fmtMB(item.size)} MB)`}
                      </p>

                      {/* Detailed Progress bar for in-flight uploading or processing */}
                      {(isItemProcessing || isItemUploading) && (
                        <div className="mt-2 space-y-1">
                          <div className="flex justify-between text-[11px] text-slate-500 font-medium">
                            <span>
                              {isItemUploading ? 'Transferring file bytes' : 'AI Ingestion Pipeline'}
                            </span>
                            <span>{Math.round(item.percentage)}%</span>
                          </div>
                          <div className="h-1.5 w-full max-w-md overflow-hidden rounded-full bg-slate-100">
                            <div
                              className={`h-full transition-all duration-300 ${
                                isItemUploading ? 'bg-indigo-500' : 'bg-blue-600'
                              }`}
                              style={{ width: `${Math.max(item.percentage, 5)}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Actions column */}
                  <div className="shrink-0 flex items-center gap-2">
                    {isItemFailed && (item.jobId || item.file) && (
                      <button
                        type="button"
                        onClick={() => handleRetry(item.jobId || item.clientItemId)}
                        className="inline-flex items-center gap-1 px-3 py-1 text-[12px] font-semibold text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors cursor-pointer"
                      >
                        <RotateCcw className="h-3 w-3" />
                        <span>Retry</span>
                      </button>
                    )}
                    {(isItemWaiting || isItemQueued || isItemUploading) && (
                      <button
                        type="button"
                        onClick={() => handleCancelItem(item.clientItemId)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 text-[11.5px] font-medium text-slate-500 hover:text-rose-600 hover:bg-rose-50 border border-slate-200 hover:border-rose-200 rounded-lg transition-colors cursor-pointer"
                        title={isItemWaiting ? 'Remove from upload queue' : 'Cancel this deck'}
                      >
                        <X className="h-3 w-3" />
                        <span>Cancel</span>
                      </button>
                    )}
                    {isItemCompleted && (
                      <span className="text-[12px] font-semibold text-emerald-600">Done</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Real-time Ingestion Progress for currently processing deck (step detail + activity log) */}
      {pipelineProgress && activeDocId && (
        <div
          className={`rounded-xl border p-6 shadow-sm space-y-4 animate-in fade-in transition-all ${
            pipelineProgress.status === 'COMPLETED' || pipelineProgress.percentage >= 100
              ? 'border-emerald-200 bg-emerald-50/25'
              : pipelineProgress.status === 'FAILED'
              ? 'border-rose-200 bg-rose-50/25'
              : pipelineProgress.status === 'CANCELLED'
              ? 'border-amber-200 bg-amber-50/30'
              : 'border-slate-200 bg-white'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              {pipelineProgress.status === 'COMPLETED' || pipelineProgress.percentage >= 100 ? (
                <CheckCircle2 className="h-5 w-5 text-emerald-600 shrink-0" />
              ) : pipelineProgress.status === 'FAILED' ? (
                <AlertCircle className="h-5 w-5 text-rose-600 shrink-0" />
              ) : pipelineProgress.status === 'CANCELLED' ? (
                <X className="h-5 w-5 text-amber-600 shrink-0" />
              ) : (
                <Loader2 className="h-5 w-5 animate-spin text-blue-600 shrink-0" />
              )}
              <div>
                <h4 className="text-[13px] font-semibold text-slate-900">Active Deck Processing</h4>
                <p className="text-[12px] text-slate-500 font-normal">{pipelineProgress.step}</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <span
                className={`text-[14px] font-bold ${
                  pipelineProgress.status === 'COMPLETED' || pipelineProgress.percentage >= 100
                    ? 'text-emerald-600'
                    : pipelineProgress.status === 'FAILED'
                    ? 'text-rose-600'
                    : 'text-blue-600'
                }`}
              >
                {pipelineProgress.status === 'CANCELLED' ? '' : `${pipelineProgress.percentage}%`}
              </span>

              {pipelineProgress.status === 'PROCESSING' && (uploading || activeDocId) && (
                <button
                  type="button"
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-rose-600 hover:bg-rose-50 transition-colors disabled:opacity-60 cursor-pointer"
                  title="Stop this upload and remove everything it created on the server"
                >
                  <X className="h-3.5 w-3.5" />
                  {cancelling ? 'Cancelling...' : 'Cancel deck'}
                </button>
              )}
            </div>
          </div>

          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className={`h-full transition-all duration-300 ${
                pipelineProgress.status === 'COMPLETED' || pipelineProgress.percentage >= 100
                  ? 'bg-emerald-600'
                  : pipelineProgress.status === 'FAILED'
                  ? 'bg-rose-600'
                  : pipelineProgress.status === 'CANCELLED'
                  ? 'bg-amber-500'
                  : 'bg-blue-600'
              }`}
              style={{ width: `${pipelineProgress.percentage}%` }}
            />
          </div>

          {/* Live upload detail: which slice is going up and how many MB have been sent */}
          {uploadDetail && pipelineProgress.status === 'PROCESSING' && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[12px]">
                <span className="font-semibold text-slate-800">
                  {uploadDetail.totalChunks > 1
                    ? `Slice ${uploadDetail.chunkIndex} of ${uploadDetail.totalChunks}`
                    : 'Uploading in one piece'}
                </span>
                <span className="text-slate-500 font-normal tabular-nums">
                  {fmtMB(uploadDetail.bytesSent)} / {fmtMB(uploadDetail.totalBytes)} MB
                  {uploadDetail.totalChunks > 1 && ` · ${fmtMB(uploadDetail.chunkSize)} MB per slice`}
                </span>
              </div>
              <div className="h-1 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full bg-blue-400 transition-all duration-200"
                  style={{
                    width: `${uploadDetail.totalBytes > 0 ? Math.round((uploadDetail.bytesSent / uploadDetail.totalBytes) * 100) : 0}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* Stopwatch */}
          {timing && timing.finishedAt && (
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[12px]">
              <span className="font-semibold text-slate-800 tabular-nums">
                {pipelineProgress.status === 'COMPLETED'
                  ? `Finished in ${fmtDuration(timing.finishedAt - timing.startedAt)}`
                  : `Stopped after ${fmtDuration(timing.finishedAt - timing.startedAt)}`}
              </span>
              <span className="text-slate-500 font-normal tabular-nums">
                {`Upload ${fmtDuration((timing.uploadEndedAt ?? timing.finishedAt) - timing.startedAt)}`}
                {timing.uploadEndedAt &&
                  ` · Extract, tag & index ${fmtDuration(timing.finishedAt - timing.uploadEndedAt)}`}
              </span>
            </div>
          )}

          {/* Activity log and memory info */}
          {pipelineProgress.status === 'PROCESSING' &&
            (pipelineProgress.detail || (pipelineProgress.events || []).length > 0) && (
              <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 space-y-2">
                <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
                  <p className="text-[12.5px] font-semibold text-slate-800 leading-snug">
                    {pipelineProgress.detail || pipelineProgress.step}
                  </p>
                  {pipelineProgress.memory?.cur != null && (() => {
                    const cur = pipelineProgress.memory!.cur as number;
                    const limit = pipelineProgress.memory!.limit || 2048;
                    const ratio = limit ? cur / limit : 0;
                    const cls =
                      ratio > 0.92
                        ? 'bg-red-100 text-red-700'
                        : ratio > 0.8
                        ? 'bg-amber-100 text-amber-800'
                        : 'bg-slate-200 text-slate-700';
                    return (
                      <span
                        className={`rounded px-2 py-0.5 text-[11px] font-semibold tabular-nums ${cls}`}
                      >
                        Server memory {Math.round(cur)} MB
                        {limit ? ` of ${Math.round(limit)} MB` : ''}
                      </span>
                    );
                  })()}
                </div>
                {(pipelineProgress.events || []).length > 0 && (
                  <div>
                    <button
                      type="button"
                      onClick={() => setShowLog((v) => !v)}
                      className="text-[11.5px] font-semibold text-blue-600 hover:text-blue-700 cursor-pointer"
                    >
                      {showLog
                        ? 'Hide activity log'
                        : `Show activity log (${(pipelineProgress.events || []).length})`}
                    </button>
                    {showLog && (
                      <ul className="mt-1.5 max-h-44 space-y-0.5 overflow-y-auto font-mono text-[11px] text-slate-600">
                        {[...(pipelineProgress.events || [])].reverse().map((e, i) => (
                          <li key={i} className="flex gap-2">
                            <span className="shrink-0 text-slate-400 tabular-nums">
                              {new Date(e.ts * 1000).toLocaleTimeString()}
                            </span>
                            <span className="break-words">{e.msg}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            )}

          {/* 5 Clean Steps */}
          <div
            className={`grid grid-cols-5 gap-2 pt-2 text-center text-xs ${
              pipelineProgress.status === 'CANCELLED' ? 'hidden' : ''
            }`}
          >
            {cleanSteps.map((step, idx) => {
              const isFinished =
                pipelineProgress.status === 'COMPLETED' || pipelineProgress.percentage >= 100;
              const isPast = pipelineProgress.percentage >= ((idx + 1) / 5) * 100;
              const isCurrent = pipelineProgress.percentage >= (idx / 5) * 100 && !isPast;

              return (
                <div key={step} className="space-y-1">
                  <div
                    className={`h-1 rounded-full ${
                      isFinished || isPast
                        ? 'bg-emerald-500'
                        : isCurrent
                        ? 'bg-blue-600'
                        : 'bg-slate-200'
                    }`}
                  />
                  <span
                    className={`text-[12px] ${
                      isFinished
                        ? 'text-emerald-700 font-medium'
                        : isCurrent
                        ? 'font-bold text-blue-600'
                        : isPast
                        ? 'text-slate-700 font-medium'
                        : 'text-slate-400'
                    }`}
                  >
                    {step}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Ingested Decks Table */}
      <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
        <div className="border-b border-slate-100 px-6 py-4 flex items-center justify-between">
          <h2 className="text-[16px] font-semibold tracking-tight text-slate-900">Recent uploads</h2>
          <span className="text-[12px] text-slate-500 font-normal">
            {documents.length} presentations
          </span>
        </div>

        <div className="divide-y divide-slate-100">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center justify-between px-6 py-4 hover:bg-slate-50/60 transition-colors"
            >
              <div className="flex items-center gap-3.5 min-w-0">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
                  <FileText className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <h4
                    className="truncate text-[14px] font-medium text-slate-900"
                    title={doc.filename}
                  >
                    {doc.filename}
                  </h4>
                  <p className="text-[12px] text-slate-400 font-normal">
                    {doc.slide_count} slides · {doc.question_count} questions extracted · Year:{' '}
                    {doc.year || '2024'}
                    {doc.title && doc.title !== doc.filename ? ` · ${doc.title}` : ''}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5 text-[12px]">
                  <span
                    className={`h-2 w-2 rounded-full ${
                      doc.processing_status === 'COMPLETED'
                        ? 'bg-emerald-500'
                        : doc.processing_status === 'FAILED'
                        ? 'bg-red-500'
                        : 'bg-blue-500 animate-pulse'
                    }`}
                  />
                  <span className="text-[12px] font-medium text-slate-600">
                    {doc.processing_status === 'COMPLETED' ? 'Completed' : doc.processing_status}
                  </span>
                </div>

                <button
                  onClick={() => handleDelete(doc.id)}
                  className="rounded-lg p-1.5 text-slate-400 hover:text-red-600 transition-colors cursor-pointer"
                  title="Delete Document"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ))}

          {documents.length === 0 && !loading && (
            <div className="py-12 text-center text-[13px] text-slate-400 font-normal">
              No documents uploaded yet.
            </div>
          )}
        </div>
      </div>

      <SlideViewerModal
        isOpen={Boolean(selectedSlide)}
        onClose={() => setSelectedSlide(null)}
        slide={selectedSlide}
      />
    </div>
  );
}
