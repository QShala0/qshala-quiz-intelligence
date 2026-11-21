export interface RecordedAudio {
  blob: Blob;
  durationSec: number;
}

// Clips shorter than this are ignored (the server ignores anything under 0.3 s anyway).
const MIN_CLIP_SECONDS = 0.3;
// The recorder stops by itself at this length; the server accepts up to 90 s.
const MAX_CLIP_MS = 60000;

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
        autoGainControl: true,
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

    // Auto-stop automatically at 60 seconds (see MAX_CLIP_MS)
    this.autoStopTimer = setTimeout(() => {
      if (this.isRecording()) {
        this.onAutoStopCallback?.();
      }
    }, MAX_CLIP_MS);
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

    // Ignore very short or empty recordings
    if (durationSec < MIN_CLIP_SECONDS || recordedBlob.size === 0) {
      return null;
    }

    return { blob: recordedBlob, durationSec };
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
