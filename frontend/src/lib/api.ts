import {
  DashboardStats,
  DocumentItem,
  Slide,
  HistoricalQuestion,
  Quiz,
  GeneratedQuestion,
  IngestionStatus,
  TopicItem,
  TopicSummary,
  TagItem,
  TagPreviewResponse,
  SharePointSyncStatus,
  SharePointFileItem,
  IngestionJobItem,
  SharePointSyncResponse,
  RequeueResponse,
  JobActionResponse,
  JobRetryResponse,
  QuestionDetail,
  TopicPath,
  RepeatEntry,
  EditHistoryItem,
  TaxonomyV6,
  DeckMeta,
  BatchRegisterPayload,
  BatchRegisterResponse,
  BatchStatusResponse,
  ActiveBatchResponse,
  CancelPendingResponse,
} from './types';

// Module-scoped fetch shadow, applied to every existing fetch() call site in this file with no
// per-call changes needed. Does two things:
//  1. Attaches X-API-Key automatically once NEXT_PUBLIC_API_KEY is set (matches the optional
//     backend auth gate -- see API_KEY_SECRET in config.py). A no-op while that env var is unset.
//  2. Retries GET requests up to 2 extra times with exponential backoff (300ms, 900ms) on a
//     network failure or a 429/5xx response. Only GET is retried -- retrying a POST/PUT/DELETE
//     here could duplicate a write (e.g. double-create), so those still fail on the first error
//     and are handled by their own call sites (chunked upload already has its own retry loop).
const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);
const RETRY_DELAYS_MS = [300, 900];

const fetch = async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
  const apiKey = process.env.NEXT_PUBLIC_API_KEY;
  let finalInit = init;
  if (apiKey) {
    const headers = new Headers(init.headers || {});
    headers.set('X-API-Key', apiKey);
    finalInit = { ...init, headers };
  }

  const method = (finalInit.method || 'GET').toUpperCase();
  if (method !== 'GET') {
    return globalThis.fetch(input, finalInit);
  }

  let lastError: unknown;
  for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt++) {
    try {
      const response = await globalThis.fetch(input, finalInit);
      if (!RETRYABLE_STATUSES.has(response.status) || attempt === RETRY_DELAYS_MS.length) {
        return response;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (err) {
      // An intentional abort (e.g. a stale search superseded by a newer filter change) is not a
      // transient failure -- retrying it would just refire a request the caller already gave up
      // on, racing the request that replaced it.
      if (err && typeof err === 'object' && (err as { name?: string }).name === 'AbortError') {
        throw err;
      }
      lastError = err;
      if (attempt === RETRY_DELAYS_MS.length) {
        throw err;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
  }
  throw lastError;
};

export function getApiBase(): string {
  // In the browser on Vercel deployments, always use the colocated Vercel backend at /api/v1
  // via Vercel rewrites, avoiding stale external backends unless explicitly forced.
  if (typeof window !== 'undefined') {
    if (window.location.hostname.includes('vercel.app')) {
      return '/api/v1';
    }
  }
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL;
  }
  if (typeof window !== 'undefined') {
    return '/api/v1';
  }
  if (process.env.VERCEL_URL) {
    return `https://${process.env.VERCEL_URL}/api/v1`;
  }
  return 'http://localhost:8000/api/v1';
}

export function resolveMediaUrl(url: string | null | undefined): string {
  if (!url) return '';
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:') || url.startsWith('blob:')) {
    return url;
  }
  const cleanPath = url.startsWith('/') ? url : `/${url}`;
  
  // Return static edge path directly so Next.js Edge CDN serves assets with 0ms cold start
  return cleanPath;
}

export function getFallbackMediaUrl(currentUrl: string): string | null {
  if (!currentUrl) return null;
  // Layer 1 -> Layer 2: If Edge CDN (/static/...) fails, fallback to backend API service /api/v1/static/...
  if (currentUrl.startsWith('/static/')) {
    return `/api/v1${currentUrl}`;
  }
  // Layer 2 -> Layer 3: If backend API (/api/v1/static/...) fails, fallback to internal Next.js media streamer /api/media/...
  if (currentUrl.startsWith('/api/v1/static/')) {
    const rel = currentUrl.replace('/api/v1/static/', '');
    return `/api/media/${rel.replace(/^\/+/, '')}`;
  }
  if (currentUrl.includes('/static/') && !currentUrl.includes('/api/v1/static/') && !currentUrl.includes('/api/media/')) {
    return currentUrl.replace('/static/', '/api/v1/static/');
  }
  return null;
}

// Vercel rejects any request body over ~4.5 MB with a 413 before it ever reaches the backend, so
// slices cannot travel through the /api/v1 rewrite. Set NEXT_PUBLIC_UPLOAD_API_URL to the Cloud Run
// API base (e.g. https://<service>.run.app/api/v1) to send uploads straight to the backend; the
// backend's CORS regex already allows the Vercel origins. Unset = previous behaviour.
export function getUploadApiBase(): string {
  return process.env.NEXT_PUBLIC_UPLOAD_API_URL || getApiBase();
}

// Size of each slice sent by uploadDocumentChunked. Exported so the UI can show the same number.
// 24 MB: Cloud Run enforces a hard 32 MiB request-body cap on HTTP/1 (no limit only if the service
// speaks HTTP/2 end-to-end, which the backend doesn't). A 50 MB slice was fine on Render -- which
// has no such cap -- but silently gets dropped mid-request on Cloud Run, which looks like the
// upload resetting rather than a clean error. 24 MB leaves comfortable headroom under the 32 MiB
// ceiling (multipart overhead, any proxy in front, etc) while keeping the chunk count lower than
// a more conservative 16 MB would. The real cost of a smaller slice is only that a failed slice
// has to resend less.
export const UPLOAD_CHUNK_SIZE = 24 * 1024 * 1024;

/** Live numbers the Upload Center shows while a file is going up. */
export interface UploadProgressInfo {
  bytesSent: number;
  totalBytes: number;
  chunkIndex: number; // 1-based slice currently in flight
  totalChunks: number;
  chunkSize: number;
}

function makeAbortError(): Error {
  const e = new Error('Upload cancelled');
  e.name = 'AbortError';
  return e;
}

export function isAbortError(e: unknown): boolean {
  return !!e && typeof e === 'object' && (e as { name?: string }).name === 'AbortError';
}

// fetch() cannot report upload progress, XMLHttpRequest can. Resolves with a fetch-style
// Response so callers keep using handleResponse(). Aborting the signal really closes the
// connection (xhr.abort()), it does not just stop listening.
function xhrPost(
  url: string,
  body: FormData,
  opts?: { signal?: AbortSignal; onUploadProgress?: (loaded: number) => void }
): Promise<Response> {
  return new Promise((resolve, reject) => {
    if (opts?.signal?.aborted) {
      reject(makeAbortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'text';

    const onAbort = () => xhr.abort();
    opts?.signal?.addEventListener('abort', onAbort, { once: true });
    const cleanup = () => opts?.signal?.removeEventListener('abort', onAbort);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts?.onUploadProgress?.(e.loaded);
    };
    xhr.onload = () => {
      cleanup();
      resolve(new Response(xhr.responseText, { status: xhr.status, statusText: xhr.statusText }));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error('Network error during upload'));
    };
    xhr.onabort = () => {
      cleanup();
      reject(makeAbortError());
    };
    xhr.send(body);
  });
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errorText = await res.text();
    try {
      const errJson = JSON.parse(errorText);
      throw new Error(errJson.detail || errJson.message || 'API request failed');
    } catch {
      throw new Error(errorText || `HTTP ${res.status}: ${res.statusText}`);
    }
  }
  return res.json();
}

export const api = {
  // Stats
  getStats: async (): Promise<DashboardStats> => {
    const res = await fetch(`${getApiBase()}/stats`, { cache: 'no-store' });
    return handleResponse<DashboardStats>(res);
  },

  // Documents
  listDocuments: async (): Promise<DocumentItem[]> => {
    const res = await fetch(`${getApiBase()}/documents`, { cache: 'no-store' });
    return handleResponse<DocumentItem[]>(res);
  },

  getDocument: async (id: string): Promise<DocumentItem> => {
    const res = await fetch(`${getApiBase()}/documents/${id}`, { cache: 'no-store' });
    return handleResponse<DocumentItem>(res);
  },

  getDocumentSlides: async (id: string): Promise<Slide[]> => {
    const res = await fetch(`${getApiBase()}/documents/${id}/slides`, { cache: 'no-store' });
    return handleResponse<Slide[]>(res);
  },

  getIngestionStatus: async (id: string): Promise<IngestionStatus> => {
    const res = await fetch(`${getApiBase()}/documents/${id}/status`, { cache: 'no-store' });
    return handleResponse<IngestionStatus>(res);
  },

  uploadDocument: async (
    file: File,
    title?: string,
    year?: number,
    options?: {
      signal?: AbortSignal;
      onProgress?: (info: UploadProgressInfo) => void;
      batchId?: string;
      clientItemId?: string;
    }
  ): Promise<DocumentItem> => {
    const formData = new FormData();
    formData.append('file', file);
    if (title) formData.append('title', title);
    if (year) formData.append('year', year.toString());
    if (options?.batchId) formData.append('batch_id', options.batchId);
    if (options?.clientItemId) formData.append('client_item_id', options.clientItemId);

    const res = await xhrPost(`${getApiBase()}/documents/upload`, formData, {
      signal: options?.signal,
      onUploadProgress: (loaded) =>
        options?.onProgress?.({
          bytesSent: Math.min(loaded, file.size),
          totalBytes: file.size,
          chunkIndex: 1,
          totalChunks: 1,
          chunkSize: file.size,
        }),
    });
    return handleResponse<DocumentItem>(res);
  },

  uploadDocumentChunked: async (
    file: File,
    onProgress?: (
      percentage: number,
      currentChunk: number,
      totalChunks: number,
      info: UploadProgressInfo
    ) => void,
    title?: string,
    year?: number,
    chunkSize: number = UPLOAD_CHUNK_SIZE, // chunking is for resilience (a dropped connection only costs
    // one slice) AND, on Cloud Run specifically, a real payload ceiling -- see UPLOAD_CHUNK_SIZE above
    options?: {
      signal?: AbortSignal;
      onUploadId?: (uploadId: string) => void;
      batchId?: string;
      clientItemId?: string;
    }
  ): Promise<DocumentItem> => {
    const signal = options?.signal;
    const uploadId =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : `upl_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    options?.onUploadId?.(uploadId);
    const totalChunks = Math.ceil(file.size / chunkSize);

    const report = (bytesSent: number, chunkIndex: number) => {
      if (!onProgress) return;
      const sent = Math.min(bytesSent, file.size);
      const pct = file.size > 0 ? Math.round((sent / file.size) * 100) : 0;
      onProgress(pct, chunkIndex + 1, totalChunks, {
        bytesSent: sent,
        totalBytes: file.size,
        chunkIndex: chunkIndex + 1,
        totalChunks,
        chunkSize,
      });
    };

    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
      if (signal?.aborted) throw makeAbortError();

      const start = chunkIndex * chunkSize;
      const end = Math.min(start + chunkSize, file.size);
      const chunkBlob = file.slice(start, end);
      report(start, chunkIndex);

      const formData = new FormData();
      formData.append('chunk', chunkBlob, file.name);
      formData.append('upload_id', uploadId);
      formData.append('chunk_index', chunkIndex.toString());
      formData.append('total_chunks', totalChunks.toString());
      formData.append('filename', file.name);
      if (title) formData.append('title', title);
      if (year) formData.append('year', year.toString());
      if (options?.batchId) formData.append('batch_id', options.batchId);
      if (options?.clientItemId) formData.append('client_item_id', options.clientItemId);

      // Attempt upload with exponential backoff (0.8s, 2s, 4s, 8s, 15s, 30s) up to 6 attempts.
      // A true cancellation (abort signal, or 409 {"code": "upload_cancelled"}) is never retried.
      // 425 ("slice_not_ready") or 5xx/network errors retry with backoff.
      const BACKOFF_DELAYS = [800, 2000, 4000, 8000, 15000, 30000];
      let res: Response | null = null;
      let lastError: any = null;
      for (let attempt = 0; attempt < BACKOFF_DELAYS.length; attempt++) {
        try {
          res = await xhrPost(`${getUploadApiBase()}/documents/upload-chunk`, formData, {
            signal,
            onUploadProgress: (loaded) => report(start + Math.min(loaded, end - start), chunkIndex),
          });
          if (res.ok) break;
          const errText = await res.text();
          if (res.status === 409) {
            let isCancelled = false;
            try {
              const parsed = JSON.parse(errText);
              if (parsed.code === 'upload_cancelled' || (parsed.detail && (parsed.detail.code === 'upload_cancelled' || parsed.detail === 'Upload was cancelled.'))) {
                isCancelled = true;
              }
            } catch {
              if (errText.includes('upload_cancelled') || errText.includes('cancelled')) isCancelled = true;
            }
            if (isCancelled) throw makeAbortError();
          }
          lastError = new Error(errText || `HTTP ${res.status}`);
        } catch (netErr) {
          if (isAbortError(netErr)) throw netErr;
          lastError = netErr;
        }
        await new Promise((r) => setTimeout(r, BACKOFF_DELAYS[attempt]));
        if (signal?.aborted) throw makeAbortError();
      }

      if (!res || !res.ok) {
        throw lastError || new Error(`Failed to upload chunk ${chunkIndex + 1} after ${BACKOFF_DELAYS.length} attempts`);
      }

      const data = await res.json();
      report(end, chunkIndex);

      if (chunkIndex === totalChunks - 1) {
        if (data.document) {
          return data.document as DocumentItem;
        }
        return data as DocumentItem;
      }
    }

    throw new Error('Upload completed without receiving document acknowledgment');
  },

  /** Cancel a chunked upload that is still sending slices: the server deletes what it already received. */
  cancelChunkedUpload: async (uploadId: string): Promise<{ status: string }> => {
    const res = await fetch(`${getUploadApiBase()}/documents/upload-chunk/${encodeURIComponent(uploadId)}`, {
      method: 'DELETE',
      keepalive: true,
    });
    return handleResponse<{ status: string }>(res);
  },

  /** Cancel ingestion of an uploaded deck: the pipeline stops, then everything it created is removed. */
  cancelIngestion: async (id: string): Promise<{ status: string }> => {
    const res = await fetch(`${getApiBase()}/documents/${id}/cancel`, { method: 'POST' });
    return handleResponse<{ status: string }>(res);
  },

  deleteDocument: async (id: string): Promise<{ message: string }> => {
    const res = await fetch(`${getApiBase()}/documents/${id}`, {
      method: 'DELETE',
    });
    return handleResponse<{ message: string }>(res);
  },

  // Questions / Knowledge Base Vault
  getTopics: async (): Promise<TopicItem[]> => {
    const res = await fetch(`${getApiBase()}/questions/topics`, { cache: 'no-store' });
    return handleResponse<TopicItem[]>(res);
  },

  getTopicSummary: async (topic: string): Promise<TopicSummary> => {
    const res = await fetch(`${getApiBase()}/questions/topics/${encodeURIComponent(topic)}/summary`, { cache: 'no-store' });
    return handleResponse<TopicSummary>(res);
  },

  searchQuestions: async (params: {
    query?: string;
    topic?: string;
    subtopic?: string;
    tag?: string;
    grade_min?: number;
    grade_max?: number;
    difficulty?: string;
    document_id?: string;
    document_title?: string;
    duplicate_status?: string;
    question_format?: string;
    format?: string;
    slot?: string;
    play_mode?: string;
    sort_by?: string;
    sort_order?: 'asc' | 'desc';
    limit?: number;
    offset?: number;
    signal?: AbortSignal;
  }): Promise<HistoricalQuestion[]> => {
    const searchParams = new URLSearchParams();
    if (params.query) searchParams.append('query', params.query);
    if (params.topic) searchParams.append('topic', params.topic);
    if (params.subtopic) searchParams.append('subtopic', params.subtopic);
    if (params.tag) searchParams.append('tag', params.tag);
    if (params.grade_min) searchParams.append('grade_min', params.grade_min.toString());
    if (params.grade_max) searchParams.append('grade_max', params.grade_max.toString());
    if (params.difficulty) searchParams.append('difficulty', params.difficulty);
    if (params.document_id) searchParams.append('document_id', params.document_id);
    if (params.document_title) searchParams.append('document_title', params.document_title);
    if (params.duplicate_status) searchParams.append('duplicate_status', params.duplicate_status);
    if (params.question_format || params.format) searchParams.append('question_format', (params.question_format || params.format)!);
    if (params.slot) searchParams.append('slot', params.slot);
    if (params.play_mode) searchParams.append('play_mode', params.play_mode);
    if (params.sort_by) searchParams.append('sort_by', params.sort_by);
    if (params.sort_order) searchParams.append('sort_order', params.sort_order);
    if (params.limit) searchParams.append('limit', params.limit.toString());
    if (params.offset) searchParams.append('offset', params.offset.toString());

    const res = await fetch(`${getApiBase()}/questions?${searchParams.toString()}`, {
      cache: 'no-store',
      signal: params.signal,
    });
    return handleResponse<HistoricalQuestion[]>(res);
  },

  getDuplicateCandidates: async (): Promise<HistoricalQuestion[]> => {
    const res = await fetch(`${getApiBase()}/questions/duplicates`, { cache: 'no-store' });
    return handleResponse<HistoricalQuestion[]>(res);
  },

  getDuplicateCount: async (): Promise<{ count: number }> => {
    const res = await fetch(`${getApiBase()}/questions/duplicates/count`, { cache: 'no-store' });
    return handleResponse<{ count: number }>(res);
  },

  getReviewCandidates: async (signal?: AbortSignal): Promise<HistoricalQuestion[]> => {
    const res = await fetch(`${getApiBase()}/questions/needs-review`, { cache: 'no-store', signal });
    return handleResponse<HistoricalQuestion[]>(res);
  },

  getReviewCount: async (): Promise<number> => {
    const res = await fetch(`${getApiBase()}/questions/needs-review/count`, { cache: 'no-store' });
    const data = await handleResponse<{ count: number }>(res);
    return data.count;
  },

  // Arrange the Order: enter (or, with [], clear) the correct order of the four item labels by hand.
  setArrangeOrder: async (questionId: string, correctOrder: number[]): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/arrange-order`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ correct_order: correctOrder }),
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  // A person has checked a flagged format reading: it leaves the review queue.
  acceptFormatReview: async (questionId: string): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/accept-format-review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  acceptReview: async (questionId: string): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/accept-review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  resolveDuplicate: async (
    questionId: string,
    action: 'CONFIRM_DUPLICATE' | 'DISMISS_UNIQUE' | 'MERGE',
    notes?: string
  ): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/resolve-duplicate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, notes }),
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  // SharePoint & Job Queue Integration
  getSharePointStatus: async (): Promise<SharePointSyncStatus> => {
    const res = await fetch(`${getApiBase()}/sharepoint/status`, { cache: 'no-store' });
    return handleResponse<SharePointSyncStatus>(res);
  },

  triggerSharePointSync: async (driveId?: string): Promise<SharePointSyncResponse> => {
    const url = driveId ? `${getApiBase()}/sharepoint/sync?drive_id=${encodeURIComponent(driveId)}` : `${getApiBase()}/sharepoint/sync`;
    const res = await fetch(url, {
      method: 'POST',
    });
    return handleResponse<SharePointSyncResponse>(res);
  },

  listSharePointFiles: async (sync_status?: string): Promise<{ total: number; files: SharePointFileItem[] }> => {
    const url = sync_status ? `${getApiBase()}/sharepoint/files?sync_status=${encodeURIComponent(sync_status)}` : `${getApiBase()}/sharepoint/files`;
    const res = await fetch(url, { cache: 'no-store' });
    return handleResponse<{ total: number; files: SharePointFileItem[] }>(res);
  },

  requeueSharePointFile: async (fileId: string): Promise<RequeueResponse> => {
    const res = await fetch(`${getApiBase()}/sharepoint/files/${fileId}/re-queue`, {
      method: 'POST',
    });
    return handleResponse<RequeueResponse>(res);
  },

  listJobs: async (status?: string): Promise<{ total: number; jobs: IngestionJobItem[] }> => {
    const url = status ? `${getApiBase()}/jobs?status=${encodeURIComponent(status)}` : `${getApiBase()}/jobs`;
    const res = await fetch(url, { cache: 'no-store' });
    return handleResponse<{ total: number; jobs: IngestionJobItem[] }>(res);
  },

  processNextJob: async (): Promise<JobActionResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/process-next`, {
      method: 'POST',
    });
    return handleResponse<JobActionResponse>(res);
  },

  retryJob: async (jobId: string, options?: { signal?: AbortSignal }): Promise<JobRetryResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/${jobId}/retry`, {
      method: 'POST',
      signal: options?.signal,
    });
    return handleResponse<JobRetryResponse>(res);
  },

  createUploadBatch: async (
    payload: BatchRegisterPayload,
    options?: { signal?: AbortSignal }
  ): Promise<BatchRegisterResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/upload-batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: options?.signal,
    });
    return handleResponse<BatchRegisterResponse>(res);
  },

  getUploadBatch: async (
    batchId: string,
    options?: { signal?: AbortSignal }
  ): Promise<BatchStatusResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/upload-batch/${batchId}`, {
      signal: options?.signal,
    });
    return handleResponse<BatchStatusResponse>(res);
  },

  getActiveUploadBatch: async (
    options?: { signal?: AbortSignal }
  ): Promise<ActiveBatchResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/upload-batches/active`, {
      signal: options?.signal,
    });
    return handleResponse<ActiveBatchResponse>(res);
  },

  sendBatchHeartbeat: async (batchId: string): Promise<{ status: string }> => {
    const res = await fetch(`${getApiBase()}/jobs/upload-batch/${encodeURIComponent(batchId)}/heartbeat`, {
      method: 'POST',
    });
    return handleResponse<{ status: string }>(res);
  },

  cancelPendingBatch: async (
    batchId: string,
    options?: { signal?: AbortSignal }
  ): Promise<CancelPendingResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/upload-batch/${batchId}/cancel-pending`, {
      method: 'POST',
      signal: options?.signal,
    });
    return handleResponse<CancelPendingResponse>(res);
  },

  cancelBatch: async (
    batchId: string,
    options?: { signal?: AbortSignal }
  ): Promise<CancelPendingResponse> => {
    const res = await fetch(`${getApiBase()}/jobs/upload-batch/${batchId}/cancel`, {
      method: 'POST',
      signal: options?.signal,
    });
    return handleResponse<CancelPendingResponse>(res);
  },

  cancelJob: async (
    jobId: string,
    options?: { signal?: AbortSignal }
  ): Promise<{ message: string }> => {
    const res = await fetch(`${getApiBase()}/jobs/${jobId}/cancel`, {
      method: 'POST',
      signal: options?.signal,
    });
    return handleResponse<{ message: string }>(res);
  },

  updateQuestionTags: async (
    questionId: string,
    tags: string[],
    topics?: string[],
    topic?: string,
    subtopic?: string
  ): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags, topics, topic, subtopic }),
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  // ---- Edit Tags (v6)
  getQuestionDetail: async (questionId: string): Promise<QuestionDetail> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/detail`, { cache: 'no-store' });
    return handleResponse<QuestionDetail>(res);
  },

  updateQuestionMeta: async (questionId: string, changes: Record<string, any>): Promise<QuestionDetail> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/meta`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes, actor: 'web' }),
    });
    return handleResponse<QuestionDetail>(res);
  },

  updateTopicPaths: async (questionId: string, paths: TopicPath[]): Promise<QuestionDetail> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/topic-paths`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        paths: paths.map((p) => ({ l1: p.l1, l2: p.l2 || null, keywords: p.keywords })),
        actor: 'web',
      }),
    });
    return handleResponse<QuestionDetail>(res);
  },

  updateHashtags: async (questionId: string, hashtags: string[]): Promise<QuestionDetail> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/hashtags`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hashtags, actor: 'web' }),
    });
    return handleResponse<QuestionDetail>(res);
  },

  patchRepeat: async (
    questionId: string,
    repeatId: string,
    body: { quiz_name?: string; round_number?: number; question_in_round?: number; question_number?: number }
  ): Promise<RepeatEntry> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/repeats/${repeatId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...body, actor: 'web' }),
    });
    return handleResponse<RepeatEntry>(res);
  },

  deleteRepeat: async (questionId: string, repeatId: string): Promise<{ repeated: RepeatEntry[] }> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/repeats/${repeatId}?actor=web`, {
      method: 'DELETE',
    });
    return handleResponse<{ repeated: RepeatEntry[] }>(res);
  },

  getQuestionHistory: async (questionId: string): Promise<EditHistoryItem[]> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/history`, { cache: 'no-store' });
    return handleResponse<EditHistoryItem[]>(res);
  },

  retagQuestion: async (questionId: string, force = false): Promise<QuestionDetail> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/retag?force=${force}`, { method: 'POST' });
    return handleResponse<QuestionDetail>(res);
  },

  getTaxonomyV6: async (): Promise<TaxonomyV6> => {
    const res = await fetch(`${getApiBase()}/taxonomy`, { cache: 'no-store' });
    return handleResponse<TaxonomyV6>(res);
  },

  updateDeckMeta: async (documentId: string, changes: Record<string, any>): Promise<DeckMeta> => {
    const res = await fetch(`${getApiBase()}/documents/${documentId}/deck-meta`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes, actor: 'web' }),
    });
    return handleResponse<DeckMeta>(res);
  },

  updateQuestionContent: async (
    questionId: string,
    body: { difficulty?: string; visual_clues?: string }
  ): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  addQuestionTag: async (questionId: string, tag: string): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/tags?tag=${encodeURIComponent(tag)}`, {
      method: 'POST',
    });
    return handleResponse<HistoricalQuestion>(res);
  },

  deleteQuestionTag: async (questionId: string, tagName: string): Promise<HistoricalQuestion> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}/tags/${encodeURIComponent(tagName)}`, {
      method: 'DELETE',
    });
    return handleResponse<HistoricalQuestion>(res);
  },


  getVaultTags: async (topic?: string): Promise<TagItem[]> => {
    const q = topic ? `?topic=${encodeURIComponent(topic)}` : '';
    const res = await fetch(`${getApiBase()}/questions/tags${q}`, { cache: 'no-store' });
    return handleResponse<TagItem[]>(res);
  },

  getTagPreview: async (topic?: string, tags?: string[]): Promise<TagPreviewResponse> => {
    const params = new URLSearchParams();
    if (topic) params.append('topic', topic);
    if (tags && tags.length > 0) params.append('tags', tags.join(','));
    const res = await fetch(`${getApiBase()}/questions/tag-preview?${params.toString()}`, { cache: 'no-store' });
    return handleResponse<TagPreviewResponse>(res);
  },

  // Quizzes
  generateQuiz: async (payload: {
    topic: string;
    subtopic?: string;
    quiz_format?: string;
    audience_type?: string;
    grades?: number[];
    age_range?: string;
    grade_min?: number;
    grade_max?: number;
    difficulty?: string;
    difficulty_distribution?: Record<string, number>;
    question_count: number;
    question_types: string[];
    generation_mode: string;
    tags?: string[];
    personality?: string;
    style?: string;
    raw_prompt?: string;
  }): Promise<Quiz> => {
    const res = await fetch(`${getApiBase()}/quizzes/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return handleResponse<Quiz>(res);
  },

  listQuizzes: async (): Promise<Quiz[]> => {
    const res = await fetch(`${getApiBase()}/quizzes`, { cache: 'no-store' });
    return handleResponse<Quiz[]>(res);
  },

  getQuiz: async (id: string): Promise<Quiz> => {
    const res = await fetch(`${getApiBase()}/quizzes/${id}`, { cache: 'no-store' });
    return handleResponse<Quiz>(res);
  },

  deleteQuiz: async (id: string): Promise<{ message: string }> => {
    const res = await fetch(`${getApiBase()}/quizzes/${id}`, {
      method: 'DELETE',
    });
    return handleResponse<{ message: string }>(res);
  },

  updateQuestion: async (
    questionId: string,
    data: Partial<GeneratedQuestion>
  ): Promise<GeneratedQuestion> => {
    const res = await fetch(`${getApiBase()}/quizzes/questions/${questionId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return handleResponse<GeneratedQuestion>(res);
  },

  executeQuestionAction: async (
    questionId: string,
    action: 'regenerate' | 'make_easier' | 'make_harder' | 'generate_similar',
    customInstruction?: string
  ): Promise<GeneratedQuestion> => {
    const res = await fetch(`${getApiBase()}/quizzes/questions/${questionId}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, custom_instruction: customInstruction }),
    });
    return handleResponse<GeneratedQuestion>(res);
  },

  deleteQuestion: async (questionId: string): Promise<{ message: string }> => {
    const res = await fetch(`${getApiBase()}/quizzes/questions/${questionId}`, {
      method: 'DELETE',
    });
    return handleResponse<{ message: string }>(res);
  },

  deleteQuestionFromBank: async (questionId: string): Promise<{ status: string; message: string }> => {
    const res = await fetch(`${getApiBase()}/questions/${questionId}`, {
      method: 'DELETE',
    });
    return handleResponse<{ status: string; message: string }>(res);
  },

  clearQuestionBank: async (documentId?: string): Promise<{ status: string; deleted_count: number; message: string }> => {
    const url = documentId
      ? `${getApiBase()}/questions?document_id=${encodeURIComponent(documentId)}`
      : `${getApiBase()}/questions?all=true`;
    const res = await fetch(url, {
      method: 'DELETE',
    });
    return handleResponse<{ status: string; deleted_count: number; message: string }>(res);
  },

  exportQuizUrl: (quizId: string, format: 'json' | 'csv' | 'pptx', options?: ExportOptions): string => {
    let url = `${getApiBase()}/quizzes/${quizId}/export?format=${format}`;
    if (format === 'pptx') {
      const quizType = options?.quizType || (options?.theme === 'corporate' ? 'corporate' : 'school');
      url += `&quiz_type=${encodeURIComponent(quizType)}`;
      if (options?.campusEdition) url += `&campus_edition=${encodeURIComponent(options.campusEdition)}`;
      if (options?.prizeMoney) url += `&prize_money=${encodeURIComponent(options.prizeMoney)}`;
      if (options?.customTitle) url += `&custom_title=${encodeURIComponent(options.customTitle)}`;
      if (options?.customSubtitle) url += `&custom_subtitle=${encodeURIComponent(options.customSubtitle)}`;
      if (options?.includeSocialSlides !== undefined) url += `&include_social_slides=${options.includeSocialSlides}`;
      if (options?.showAnswerDivider !== undefined) url += `&show_answer_divider=${options.showAnswerDivider}`;
    }
    return url;
  },

  exportQuizDirect: async (
    quiz: Quiz,
    format: 'json' | 'csv' | 'pptx' = 'pptx',
    options?: ExportOptions
  ): Promise<Blob> => {
    const params = new URLSearchParams({ format });
    if (format === 'pptx') {
      const quizType = options?.quizType || (options?.theme === 'corporate' ? 'corporate' : 'school');
      params.append('quiz_type', quizType);
      if (options?.campusEdition) params.append('campus_edition', options.campusEdition);
      if (options?.prizeMoney) params.append('prize_money', options.prizeMoney);
      if (options?.customTitle) params.append('custom_title', options.customTitle);
      if (options?.customSubtitle) params.append('custom_subtitle', options.customSubtitle);
      if (options?.includeSocialSlides !== undefined) params.append('include_social_slides', String(options.includeSocialSlides));
      if (options?.showAnswerDivider !== undefined) params.append('show_answer_divider', String(options.showAnswerDivider));
    }
    const queryStr = params.toString();

    // 1. Try GET /quizzes/{id}/export first
    try {
      const getRes = await fetch(`${getApiBase()}/quizzes/${quiz.id}/export?${queryStr}`);
      if (getRes.ok) {
        return await getRes.blob();
      }
    } catch {
      // Fallback to POST
    }

    // 2. Fallback to POST /quizzes/export with full quiz payload
    const postRes = await fetch(`${getApiBase()}/quizzes/export?${queryStr}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(quiz),
    });
    if (!postRes.ok) {
      throw new Error(`Export request failed (${postRes.status}): ${postRes.statusText}`);
    }
    return await postRes.blob();
  },
};

export interface ExportOptions {
  quizType?: 'school' | 'corporate' | 'minimalist' | 'qshala_school';
  templateCategory?: 'qshala' | 'zsv';
  theme?: string;
  campusEdition?: string;
  prizeMoney?: string;
  customTitle?: string;
  customSubtitle?: string;
  includeSocialSlides?: boolean;
  showAnswerDivider?: boolean;
}

export async function downloadQuizFile(
  quiz: Quiz,
  format: 'json' | 'csv' | 'pptx' = 'pptx',
  options?: ExportOptions
): Promise<void> {
  const blob = await api.exportQuizDirect(quiz, format, options);
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const ext = format === 'pptx' ? 'pptx' : format;
  const cleanTitle = (quiz.title || 'qshala_quiz')
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');
  a.download = `${cleanTitle || 'qshala_quiz'}.${ext}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => window.URL.revokeObjectURL(url), 5000);
}


// ---------------------------------------------------------------- QShala tagging
// Thin wrappers over backend/app/routers/qshala_tagging.py.
import type { QshalaTag, QshalaTagHistory, QshalaHumanTagInput, QshalaTaxonomy, QshalaSearchResponse } from './types';

export async function runQshalaTagger(questionId: string, actor = 'web'): Promise<QshalaTag> {
  const base = getApiBase();
  const r = await fetch(`${base}/qshala/tag/${encodeURIComponent(questionId)}?actor=${encodeURIComponent(actor)}`, { method: 'POST' });
  if (!r.ok) throw new Error(`Tagger failed (${r.status})`);
  return r.json();
}

export async function getQshalaTagHistory(questionId: string): Promise<QshalaTagHistory> {
  const base = getApiBase();
  const r = await fetch(`${base}/qshala/tag/${encodeURIComponent(questionId)}`);
  if (!r.ok) throw new Error(`Failed to load tag history (${r.status})`);
  return r.json();
}

export async function submitQshalaHumanTag(questionId: string, body: QshalaHumanTagInput): Promise<QshalaTag> {
  const base = getApiBase();
  const r = await fetch(`${base}/qshala/tag/${encodeURIComponent(questionId)}/human`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`Save failed (${r.status})`);
  return r.json();
}

export async function getQshalaReviewQueue(limit = 50): Promise<QshalaTag[]> {
  const base = getApiBase();
  const r = await fetch(`${base}/qshala/review-queue?limit=${limit}`);
  if (!r.ok) throw new Error(`Review queue load failed (${r.status})`);
  return r.json();
}


export async function getQshalaTaxonomy(): Promise<QshalaTaxonomy> {
  const base = getApiBase();
  const r = await fetch(`${base}/qshala/taxonomy`);
  if (!r.ok) throw new Error(`Failed to load taxonomy (${r.status})`);
  return r.json();
}

export async function searchQshalaQuestions(params: Record<string, any> = {}): Promise<QshalaSearchResponse> {
  const base = getApiBase();
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      qs.append(k, String(v));
    }
  }
  const r = await fetch(`${base}/qshala/questions?${qs.toString()}`);
  if (!r.ok) throw new Error(`Search failed (${r.status})`);
  return r.json();
}
