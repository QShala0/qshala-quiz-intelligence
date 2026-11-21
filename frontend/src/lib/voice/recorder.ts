export interface RecordedAudio {
  audio: Float32Array;
  durationSec: number;
}

/**
 * Supported MIME type selection for MediaRecorder across Chrome, Edge, Brave, Safari, and Firefox.
 */
export function getSupportedMimeType(): string {
  if (typeof MediaRecorder === 'undefined') {
    return '';
  }
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/aac',
    'audio/ogg;codecs=opus',
  ];
  for (const mime of candidates) {
    if (typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported(mime)) {
      return mime;
    }
  }
  return '';
}

/**
 * Resamples any decoded AudioBuffer to a 16 kHz mono Float32Array using OfflineAudioContext.
 */
export async function resampleTo16kMono(audioBuffer: AudioBuffer): Promise<Float32Array> {
  const targetSampleRate = 16000;
  const numFrames = Math.max(1, Math.ceil(audioBuffer.duration * targetSampleRate));

  const OfflineCtxClass =
    window.OfflineAudioContext ||
    (window as unknown as { webkitOfflineAudioContext: typeof OfflineAudioContext })
      .webkitOfflineAudioContext;

  if (!OfflineCtxClass) {
    // Fallback: simple linear resampling if OfflineAudioContext is unavailable
    return linearResampleMono(audioBuffer, targetSampleRate);
  }

  const offlineCtx = new OfflineCtxClass(1, numFrames, targetSampleRate);
  const source = offlineCtx.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(offlineCtx.destination);
  source.start(0);

  const rendered = await offlineCtx.startRendering();
  return rendered.getChannelData(0);
}

/**
 * Pure fallback linear resampler for mono channel
 */
export function linearResampleMono(audioBuffer: AudioBuffer, targetSampleRate: number): Float32Array {
  const origRate = audioBuffer.sampleRate;
  const input = audioBuffer.getChannelData(0);
  if (origRate === targetSampleRate) {
    return new Float32Array(input);
  }

  const duration = audioBuffer.duration;
  const outputLength = Math.max(1, Math.round(duration * targetSampleRate));
  const output = new Float32Array(outputLength);
  const ratio = origRate / targetSampleRate;

  for (let i = 0; i < outputLength; i++) {
    const origIndex = i * ratio;
    const i1 = Math.floor(origIndex);
    const i2 = Math.min(i1 + 1, input.length - 1);
    const fraction = origIndex - i1;
    output[i] = input[i1] * (1 - fraction) + input[i2] * fraction;
  }

  return output;
}

export class VoiceRecorder {
  private mediaStream: MediaStream | null = null;
  private mediaRecorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private startTime: number = 0;
  private autoStopTimer: ReturnType<typeof setTimeout> | null = null;
  private onAutoStopCallback?: () => void;

  public isRecording(): boolean {
    return Boolean(this.mediaRecorder && this.mediaRecorder.state === 'recording');
  }

  public async start(onAutoStop?: () => void): Promise<void> {
    this.abort();
    this.onAutoStopCallback = onAutoStop;
    this.chunks = [];

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });

    this.mediaStream = stream;

    const mimeType = getSupportedMimeType();
    const options: MediaRecorderOptions = mimeType ? { mimeType } : {};
    const recorder = new MediaRecorder(stream, options);

    recorder.ondataavailable = (e: BlobEvent) => {
      if (e.data && e.data.size > 0) {
        this.chunks.push(e.data);
      }
    };

    recorder.start(100);
    this.mediaRecorder = recorder;
    this.startTime = Date.now();

    // Auto-stop automatically at 60 seconds
    this.autoStopTimer = setTimeout(() => {
      if (this.isRecording()) {
        this.onAutoStopCallback?.();
      }
    }, 60000);
  }

  public async stop(): Promise<RecordedAudio | null> {
    if (this.autoStopTimer) {
      clearTimeout(this.autoStopTimer);
      this.autoStopTimer = null;
    }

    const durationSec = (Date.now() - this.startTime) / 1000;
    const recorder = this.mediaRecorder;
    const stream = this.mediaStream;

    this.mediaRecorder = null;
    this.mediaStream = null;

    if (!recorder) {
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
      return null;
    }

    const stoppedBlobPromise = new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        const mime = recorder.mimeType || 'audio/webm';
        const blob = new Blob(this.chunks, { type: mime });
        resolve(blob);
      };
    });

    try {
      if (recorder.state !== 'inactive') {
        recorder.stop();
      }
    } catch {
      // Safe to ignore if already stopped
    } finally {
      // Release microphone tracks every time
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
      }
    }

    const recordedBlob = await stoppedBlobPromise;

    // Ignore recordings under 0.4 seconds
    if (durationSec < 0.4 || recordedBlob.size === 0) {
      return null;
    }

    // Decode audio and resample to 16 kHz mono Float32
    const arrayBuffer = await recordedBlob.arrayBuffer();
    const AudioCtxClass =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;

    if (!AudioCtxClass) {
      throw new Error('AudioContext unavailable in this browser');
    }

    const audioCtx = new AudioCtxClass();
    try {
      const decodedBuffer = await new Promise<AudioBuffer>((resolve, reject) => {
        audioCtx.decodeAudioData(arrayBuffer, resolve, reject);
      });

      const float32Data = await resampleTo16kMono(decodedBuffer);
      return {
        audio: float32Data,
        durationSec,
      };
    } finally {
      await audioCtx.close().catch(() => {});
    }
  }

  public abort(): void {
    if (this.autoStopTimer) {
      clearTimeout(this.autoStopTimer);
      this.autoStopTimer = null;
    }
    if (this.mediaRecorder) {
      try {
        if (this.mediaRecorder.state !== 'inactive') {
          this.mediaRecorder.stop();
        }
      } catch {
        // Ignore
      }
      this.mediaRecorder = null;
    }
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop());
      this.mediaStream = null;
    }
    this.chunks = [];
  }
}
