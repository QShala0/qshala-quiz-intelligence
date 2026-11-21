import { getPreferredEngine } from './engine';

/**
 * Client for the QShala voice service (faster-whisper on Cloud Run, see /voice_service).
 * The service scales to zero, so the first request after a quiet period can wait for a cold start.
 */

const WARM_INTERVAL_MS = 4 * 60 * 1000; // do not wake the service more than once every 4 minutes
const SLOW_AFTER_MS = 4000; // tell the user we are waking the service after this long
const RETRY_UNTIL_MS = 40_000; // keep retrying a failing upload for this long
const OVERALL_TIMEOUT_MS = 45_000; // hard client-side limit for one transcription
const RETRY_DELAY_MS = 1500;

let lastWarmAt = 0;

export const VOICE_NOT_CONFIGURED = 'Voice service not configured';

function voiceBaseUrl(): string | null {
  const raw = process.env.NEXT_PUBLIC_VOICE_API_URL;
  const trimmed = raw ? raw.trim().replace(/\/+$/, '') : '';
  return trimmed || null;
}

/** Milliseconds since the last warm-up ping (Infinity when none was sent in this page session). */
export function msSinceLastWarm(): number {
  return lastWarmAt === 0 ? Infinity : Date.now() - lastWarmAt;
}

/**
 * Pings GET /health so a cold service starts and loads its model. Never throws and sets no state.
 * Does nothing when the service is not configured or was pinged less than 4 minutes ago.
 */
export function warmVoiceService(): void {
  const base = voiceBaseUrl();
  if (!base) return;
  if (msSinceLastWarm() < WARM_INTERVAL_MS) return;
  lastWarmAt = Date.now();
  try {
    fetch(`${base}/health`, { method: 'GET', cache: 'no-store', mode: 'cors' }).catch(() => {
      // A failed warm-up is harmless: the real request retries on its own.
    });
  } catch {
    // Ignore
  }
}

/**
 * Wake-up used by the Create quiz and Question bank pages. Genuine Chrome and Edge use the browser
 * engine and never need the server, so they skip it.
 */
export function warmVoiceServiceIfNeeded(): void {
  if (getPreferredEngine() === 'browser') return;
  warmVoiceService();
}

export interface TranscribeOptions {
  /** Called once if the server has not answered after 4 seconds (it is probably waking up). */
  onSlow?: () => void;
}

class VoiceHttpError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function extensionFor(mime: string): string {
  if (mime.includes('mp4') || mime.includes('aac')) return 'mp4';
  if (mime.includes('ogg')) return 'ogg';
  return 'webm';
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch {
    // Not JSON
  }
  return `Voice service error (${res.status})`;
}

/**
 * Uploads a recorded clip and returns the transcript. Retries network errors and 5xx/429 answers
 * for up to 40 seconds (a cold start), and gives up after 45 seconds overall.
 */
export async function transcribeOnServer(
  blob: Blob,
  vocabulary: readonly string[] = [],
  options: TranscribeOptions = {}
): Promise<string> {
  const base = voiceBaseUrl();
  if (!base) throw new Error(VOICE_NOT_CONFIGURED);

  const controller = new AbortController();
  const overall = setTimeout(() => controller.abort(), OVERALL_TIMEOUT_MS);
  const slow = options.onSlow ? setTimeout(options.onSlow, SLOW_AFTER_MS) : null;
  const started = Date.now();
  lastWarmAt = Date.now(); // a real request also wakes the service

  try {
    for (;;) {
      try {
        const form = new FormData();
        form.append('audio', blob, `clip.${extensionFor(blob.type)}`);
        if (vocabulary.length > 0) form.append('vocabulary', vocabulary.join(', '));
        const res = await fetch(`${base}/transcribe`, {
          method: 'POST',
          body: form,
          signal: controller.signal,
          mode: 'cors',
        });
        if (res.ok) {
          const data = (await res.json()) as { text?: string };
          return (data.text ?? '').trim();
        }
        const message = await readError(res);
        if (res.status !== 429 && res.status < 500) throw new VoiceHttpError(message, res.status);
        if (Date.now() - started + RETRY_DELAY_MS > RETRY_UNTIL_MS) throw new VoiceHttpError(message, res.status);
      } catch (err) {
        if (err instanceof VoiceHttpError && err.status < 500 && err.status !== 429) throw err;
        if (controller.signal.aborted) throw new Error('Voice service timed out');
        if (err instanceof VoiceHttpError) throw err;
        // Network error (server cold or unreachable): retry until the window closes.
        if (Date.now() - started + RETRY_DELAY_MS > RETRY_UNTIL_MS) {
          throw new Error('Could not reach the voice service');
        }
      }
      await sleep(RETRY_DELAY_MS);
    }
  } finally {
    clearTimeout(overall);
    if (slow) clearTimeout(slow);
  }
}
