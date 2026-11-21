import { pipeline, env, AutomaticSpeechRecognitionPipeline } from '@huggingface/transformers';

// Configure transformers.js to load from self-hosted assets base URL (R2 or same-origin fallback)
// The default is the public R2 address of the uploaded voice files, so voice works even when the Vercel project has no
// NEXT_PUBLIC_VOICE_ASSETS_BASE set (without it, the files were looked up on our own site, where they do not exist).
const DEFAULT_VOICE_ASSETS_BASE = 'https://pub-e6b1fda94a4149298de10be6794417fc.r2.dev/voice/v1';
const configuredBase = process.env.NEXT_PUBLIC_VOICE_ASSETS_BASE?.trim() || DEFAULT_VOICE_ASSETS_BASE;
const normalizedBase = configuredBase.replace(/\/+$/, '');

if (typeof self !== 'undefined' && self.location) {
  const origin = self.location.origin;
  const modelsHost = normalizedBase ? `${normalizedBase}/models/` : `${origin}/models/`;
  const wasmPath = normalizedBase ? `${normalizedBase}/wasm/` : `${origin}/wasm/`;

  env.allowRemoteModels = true;
  env.allowLocalModels = false;
  env.remoteHost = modelsHost;
  env.remotePathTemplate = '{model}/';
  if (env.backends?.onnx?.wasm) {
    env.backends.onnx.wasm.wasmPaths = wasmPath;
  }
  env.useBrowserCache = true;
}

let pipelinePromise: Promise<AutomaticSpeechRecognitionPipeline> | null = null;

async function getTranscriber(): Promise<AutomaticSpeechRecognitionPipeline> {
  if (pipelinePromise) {
    return pipelinePromise;
  }

  const modelId = 'whisper-base.en';

  const downloadProgress: Record<string, number> = {};

  const handleProgress = (progressInfo: {
    status?: string;
    file?: string;
    progress?: number;
    loaded?: number;
    total?: number;
  }) => {
    if (progressInfo.file && typeof progressInfo.progress === 'number') {
      downloadProgress[progressInfo.file] = progressInfo.progress;
      const values = Object.values(downloadProgress);
      const avg = values.reduce((sum, v) => sum + v, 0) / Math.max(1, values.length);
      self.postMessage({
        type: 'download-progress',
        progress: Math.min(100, Math.max(0, Math.round(avg))),
        file: progressInfo.file,
      });
    }
  };

  pipelinePromise = (async () => {
    const hasWebGPU =
      typeof navigator !== 'undefined' &&
      'gpu' in navigator &&
      Boolean(navigator.gpu);

    let transcriber: AutomaticSpeechRecognitionPipeline | null = null;

    if (hasWebGPU) {
      try {
        transcriber = (await pipeline('automatic-speech-recognition', modelId, {
          device: 'webgpu',
          dtype: 'q8',
          progress_callback: handleProgress,
        })) as unknown as AutomaticSpeechRecognitionPipeline;
      } catch (err) {
        // Fall back to WASM if WebGPU initialization fails
        console.warn('WebGPU init failed, falling back to WASM:', err);
      }
    }

    if (!transcriber) {
      transcriber = (await pipeline('automatic-speech-recognition', modelId, {
        device: 'wasm',
        dtype: 'q8',
        progress_callback: handleProgress,
      })) as unknown as AutomaticSpeechRecognitionPipeline;
    }

    self.postMessage({ type: 'ready' });
    return transcriber;
  })();

  return pipelinePromise;
}

self.onmessage = async (event: MessageEvent) => {
  const data = event.data;
  if (!data || !data.type) return;

  if (data.type === 'init') {
    try {
      await getTranscriber();
      self.postMessage({ type: 'init-success' });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      self.postMessage({ type: 'init-error', error: message });
    }
    return;
  }

  if (data.type === 'transcribe') {
    const { id, audio, prompt } = data;
    try {
      const transcriber = await getTranscriber();

      // whisper-base.en is English-only: passing `language` or `task` makes transformers.js throw ("Cannot specify
      // `task` or `language` for an English-only model"), which surfaced as "Didn't catch that" on every recording.
      // This transformers.js version has no vocabulary prompt support either, so deck and series names are fixed
      // afterwards by correctTranscript. `prompt` is accepted and ignored.
      void prompt;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const result: any = await (transcriber as any)(audio, { chunk_length_s: 30 });
      const text = (result?.text || '').trim();

      self.postMessage({
        type: 'transcribe-success',
        id,
        text,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      self.postMessage({
        type: 'transcribe-error',
        id,
        error: message,
      });
    }
  }
};
