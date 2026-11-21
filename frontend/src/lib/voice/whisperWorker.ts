import { pipeline, env, AutomaticSpeechRecognitionPipeline } from '@huggingface/transformers';

// Configure transformers.js to load from self-hosted assets base URL (R2 or same-origin fallback)
const configuredBase = process.env.NEXT_PUBLIC_VOICE_ASSETS_BASE?.trim();
const normalizedBase = configuredBase ? configuredBase.replace(/\/+$/, '') : '';

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

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const options: Record<string, any> = {
        language: 'en',
        task: 'transcribe',
      };

      // Add vocabulary hint list via prompt_ids or initial_prompt capped at ~150 tokens
      if (prompt && typeof prompt === 'string' && prompt.trim().length > 0) {
        try {
          if (transcriber.tokenizer) {
            const tokenized = transcriber.tokenizer(prompt.trim());
            if (tokenized?.input_ids) {
              options.prompt_ids = tokenized.input_ids;
            }
          }
        } catch {
          // If prompt_ids tokenization fails, fallback to passing prompt directly
          options.initial_prompt = prompt.trim();
        }
      }

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const result: any = await (transcriber as any)(audio, options);
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
