export interface WhisperClientProgressCallback {
  (percentage: number): void;
}

class WhisperClient {
  private worker: Worker | null = null;
  private idleTimeout: ReturnType<typeof setTimeout> | null = null;
  private pendingInitResolvers: Array<{
    resolve: () => void;
    reject: (err: Error) => void;
    onProgress?: WhisperClientProgressCallback;
  }> = [];
  private pendingTranscriptions = new Map<
    string,
    {
      resolve: (text: string) => void;
      reject: (err: Error) => void;
    }
  >();
  private isReady = false;
  private initPromise: Promise<void> | null = null;
  private nextId = 1;

  private resetIdleTimer() {
    if (this.idleTimeout) {
      clearTimeout(this.idleTimeout);
      this.idleTimeout = null;
    }
    // Terminate worker after 5 minutes of idle time to free memory
    this.idleTimeout = setTimeout(() => {
      this.terminate();
    }, 5 * 60 * 1000);
  }

  private getOrCreateWorker(): Worker {
    if (this.worker) {
      return this.worker;
    }

    const worker = new Worker(new URL('./whisperWorker.ts', import.meta.url), {
      type: 'module',
    });

    worker.onmessage = (event: MessageEvent) => {
      const data = event.data;
      if (!data || !data.type) return;

      this.resetIdleTimer();

      if (data.type === 'download-progress') {
        const percent = typeof data.progress === 'number' ? data.progress : 0;
        for (const p of this.pendingInitResolvers) {
          p.onProgress?.(percent);
        }
      } else if (data.type === 'init-success' || data.type === 'ready') {
        this.isReady = true;
        const resolvers = [...this.pendingInitResolvers];
        this.pendingInitResolvers = [];
        for (const r of resolvers) {
          r.resolve();
        }
      } else if (data.type === 'init-error') {
        this.isReady = false;
        this.initPromise = null;
        const resolvers = [...this.pendingInitResolvers];
        this.pendingInitResolvers = [];
        const err = new Error(data.error || 'Voice download failed, tap to retry');
        for (const r of resolvers) {
          r.reject(err);
        }
      } else if (data.type === 'transcribe-success') {
        const item = this.pendingTranscriptions.get(data.id);
        if (item) {
          this.pendingTranscriptions.delete(data.id);
          item.resolve(data.text || '');
        }
      } else if (data.type === 'transcribe-error') {
        const item = this.pendingTranscriptions.get(data.id);
        if (item) {
          this.pendingTranscriptions.delete(data.id);
          item.reject(new Error(data.error || 'Transcription failed'));
        }
      }
    };

    worker.onerror = (err) => {
      this.resetIdleTimer();
      const message = err.message || 'Worker runtime error';
      for (const item of this.pendingTranscriptions.values()) {
        item.reject(new Error(message));
      }
      this.pendingTranscriptions.clear();
      for (const item of this.pendingInitResolvers) {
        item.reject(new Error(message));
      }
      this.pendingInitResolvers = [];
      this.terminate();
    };

    this.worker = worker;
    this.resetIdleTimer();
    return worker;
  }

  public async init(onProgress?: WhisperClientProgressCallback): Promise<void> {
    if (this.isReady && this.worker) {
      this.resetIdleTimer();
      return;
    }

    if (this.initPromise) {
      if (onProgress) {
        this.pendingInitResolvers.push({
          resolve: () => {},
          reject: () => {},
          onProgress,
        });
      }
      return this.initPromise;
    }

    const worker = this.getOrCreateWorker();
    this.resetIdleTimer();

    this.initPromise = new Promise<void>((resolve, reject) => {
      this.pendingInitResolvers.push({
        resolve: () => {
          this.initPromise = null;
          resolve();
        },
        reject: (err) => {
          this.initPromise = null;
          reject(err);
        },
        onProgress,
      });

      worker.postMessage({ type: 'init' });
    });

    return this.initPromise;
  }

  public async transcribe(
    audio: Float32Array,
    vocabulary?: readonly string[],
    onProgress?: WhisperClientProgressCallback
  ): Promise<string> {
    await this.init(onProgress);
    const worker = this.getOrCreateWorker();
    this.resetIdleTimer();

    const id = `tx-${this.nextId++}`;

    // Construct short hint list capped at ~150 tokens (~30-40 words)
    let promptHint: string | undefined;
    if (vocabulary && vocabulary.length > 0) {
      const topWords = vocabulary.slice(0, 35);
      promptHint = topWords.join(', ');
    }

    return new Promise<string>((resolve, reject) => {
      this.pendingTranscriptions.set(id, { resolve, reject });
      worker.postMessage({
        type: 'transcribe',
        id,
        audio,
        prompt: promptHint,
      });
    });
  }

  public terminate(): void {
    if (this.idleTimeout) {
      clearTimeout(this.idleTimeout);
      this.idleTimeout = null;
    }
    if (this.worker) {
      try {
        this.worker.terminate();
      } catch {
        // Safe to ignore
      }
      this.worker = null;
    }
    this.isReady = false;
    this.initPromise = null;
    this.pendingInitResolvers = [];
    this.pendingTranscriptions.clear();
  }
}

// Single shared client instance for the entire page
export const whisperClient = new WhisperClient();
