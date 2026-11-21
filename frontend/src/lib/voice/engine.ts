export type VoiceEngine = 'browser' | 'server';

export const VOICE_ENGINE_STORAGE_KEY = 'qshala-voice-engine';

export interface VoiceEnvironment {
  hasSpeechRecognition: boolean;
  hasGetUserMedia: boolean;
  hasMediaRecorder: boolean;
  isBrave: boolean;
  brands?: readonly string[];
  savedPreference?: string | null;
}

/**
 * Pure helper to evaluate engine capabilities and user preference from an environment descriptor.
 * Allows deterministic unit-testing across Chrome, Edge, Brave, Safari, and Firefox.
 */
export function evaluateVoiceEngine(env: VoiceEnvironment): {
  browserSupported: boolean;
  serverSupported: boolean;
  voiceSupported: boolean;
  preferredEngine: VoiceEngine;
} {
  const isChromeOrEdge = Boolean(
    env.brands &&
      (env.brands.includes('Google Chrome') || env.brands.includes('Microsoft Edge'))
  );

  // Browser engine requires genuine Chrome or Edge, Web Speech support, and NOT Brave
  const browserSupported =
    env.hasSpeechRecognition && isChromeOrEdge && !env.isBrave;

  // Server engine (our own voice service) needs only getUserMedia and MediaRecorder
  const serverSupported = env.hasGetUserMedia && env.hasMediaRecorder;

  const voiceSupported = browserSupported || serverSupported;

  let preferredEngine: VoiceEngine = 'browser';

  // 'whisper' is what older versions stored for the on-device engine; it now means the server engine.
  if ((env.savedPreference === 'server' || env.savedPreference === 'whisper') && serverSupported) {
    preferredEngine = 'server';
  } else if (env.savedPreference === 'browser' && browserSupported) {
    preferredEngine = 'browser';
  } else if (browserSupported) {
    preferredEngine = 'browser';
  } else if (serverSupported) {
    preferredEngine = 'server';
  }

  return {
    browserSupported,
    serverSupported,
    voiceSupported,
    preferredEngine,
  };
}

/**
 * Inspects the current browser environment to construct a VoiceEnvironment snapshot.
 */
export function getBrowserVoiceEnvironment(): VoiceEnvironment {
  if (typeof window === 'undefined') {
    return {
      hasSpeechRecognition: false,
      hasGetUserMedia: false,
      hasMediaRecorder: false,
      isBrave: false,
      brands: [],
      savedPreference: null,
    };
  }

  const hasSpeech =
    'SpeechRecognition' in window || 'webkitSpeechRecognition' in window;

  const hasGUM =
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function';

  const hasMR = typeof MediaRecorder !== 'undefined';

  const nav = window.navigator as unknown as {
    brave?: { isBrave?: () => Promise<boolean> };
    userAgentData?: {
      brands?: Array<{ brand: string; version: string }>;
    };
  };

  const isBrave = Boolean('brave' in nav || (nav && nav.brave));

  let brands: string[] = [];
  if (nav?.userAgentData && Array.isArray(nav.userAgentData.brands)) {
    brands = nav.userAgentData.brands.map((b) => b.brand);
  }

  let savedPreference: string | null = null;
  try {
    savedPreference = window.localStorage.getItem(VOICE_ENGINE_STORAGE_KEY);
  } catch {
    // Ignore localStorage access errors (e.g. strict security contexts)
  }

  return {
    hasSpeechRecognition: hasSpeech,
    hasGetUserMedia: hasGUM,
    hasMediaRecorder: hasMR,
    isBrave,
    brands,
    savedPreference,
  };
}

/**
 * Genuine Chrome or Edge on desktop/Android:
 * - navigator.userAgentData.brands includes "Google Chrome" or "Microsoft Edge"
 * - Web Speech API exists
 * - Not Brave (navigator.brave is absent)
 */
export function isBrowserSpeechSupported(): boolean {
  return evaluateVoiceEngine(getBrowserVoiceEnvironment()).browserSupported;
}

/**
 * Server engine supported when getUserMedia and MediaRecorder are available
 */
export function isServerVoiceSupported(): boolean {
  return evaluateVoiceEngine(getBrowserVoiceEnvironment()).serverSupported;
}

/**
 * Show the mic button whenever either engine can run. Hide only if neither can.
 */
export function isVoiceSupported(): boolean {
  return evaluateVoiceEngine(getBrowserVoiceEnvironment()).voiceSupported;
}

/**
 * Current preferred voice engine (respecting runtime fallback stored in localStorage)
 */
export function getPreferredEngine(): VoiceEngine {
  return evaluateVoiceEngine(getBrowserVoiceEnvironment()).preferredEngine;
}

/**
 * Stores preferred engine in localStorage safely with try/catch
 */
export function setPreferredEngine(engine: VoiceEngine): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(VOICE_ENGINE_STORAGE_KEY, engine);
  } catch {
    // Ignore storage errors
  }
}
