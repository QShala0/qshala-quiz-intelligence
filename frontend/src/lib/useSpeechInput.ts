'use client';

import { useState, useEffect, useRef, useCallback, useId, useSyncExternalStore } from 'react';
import { countVocabMatches, correctTranscript, FIXED_VOCABULARY } from './voiceVocabulary';
import {
  getPreferredEngine,
  setPreferredEngine,
  isVoiceSupported,
} from './voice/engine';
import { VoiceRecorder } from './voice/recorder';
import { whisperClient } from './voice/whisperClient';

interface SpeechRecognitionAlternativeLike {
  readonly transcript: string;
  readonly confidence: number;
}

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  item(index: number): SpeechRecognitionAlternativeLike;
  [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  item(index: number): SpeechRecognitionResultLike;
  [index: number]: SpeechRecognitionResultLike;
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
  readonly message?: string;
}

interface SpeechRecognitionInstanceLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onstart: ((this: SpeechRecognitionInstanceLike, ev: Event) => void) | null;
  onend: ((this: SpeechRecognitionInstanceLike, ev: Event) => void) | null;
  onerror: ((this: SpeechRecognitionInstanceLike, ev: SpeechRecognitionErrorEventLike) => void) | null;
  onresult: ((this: SpeechRecognitionInstanceLike, ev: SpeechRecognitionEventLike) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstanceLike;

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null;
  const win = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return win.SpeechRecognition || win.webkitSpeechRecognition || null;
}

const emptySubscribe = () => () => {};

function getVoiceSupportedSnapshot(): boolean {
  return isVoiceSupported();
}

function getServerSupportedSnapshot(): boolean {
  return false;
}

export interface UseSpeechInputOptions {
  onFinal: (text: string) => void;
  onInterim?: (text: string) => void;
  onStart?: () => void;
  vocabulary?: readonly string[];
  disabled?: boolean;
}

export interface UseSpeechInputResult {
  supported: boolean;
  listening: boolean;
  interimText: string;
  error: string | null;
  preparing: boolean;
  transcribing: boolean;
  progress: number;
  start: () => void;
  stop: () => void;
}

export function useSpeechInput({
  onFinal,
  onInterim,
  onStart,
  vocabulary = FIXED_VOCABULARY,
  disabled = false,
}: UseSpeechInputOptions): UseSpeechInputResult {
  const supported = useSyncExternalStore(
    emptySubscribe,
    getVoiceSupportedSnapshot,
    getServerSupportedSnapshot
  );

  const [listening, setListening] = useState<boolean>(false);
  const [interimText, setInterimText] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState<boolean>(false);
  const [transcribing, setTranscribing] = useState<boolean>(false);
  const [progress, setProgress] = useState<number>(0);

  const recognitionRef = useRef<SpeechRecognitionInstanceLike | null>(null);
  const recorderRef = useRef<VoiceRecorder | null>(null);
  const autoStopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isListeningRef = useRef<boolean>(false);
  const isBusyRef = useRef<boolean>(false);
  const rawId = useId();
  const instanceIdRef = useRef<string>(`speech-${rawId}`);

  const onFinalRef = useRef(onFinal);
  const onInterimRef = useRef(onInterim);
  const onStartRef = useRef(onStart);
  const vocabRef = useRef(vocabulary);
  const disabledRef = useRef(disabled);

  // Keep callback refs updated without re-binding listeners
  useEffect(() => {
    onFinalRef.current = onFinal;
    onInterimRef.current = onInterim;
    onStartRef.current = onStart;
    vocabRef.current = vocabulary;
    disabledRef.current = disabled;
  }, [onFinal, onInterim, onStart, vocabulary, disabled]);

  const clearAutoStop = useCallback(() => {
    if (autoStopTimeoutRef.current) {
      clearTimeout(autoStopTimeoutRef.current);
      autoStopTimeoutRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    clearAutoStop();

    // 1. Stop Web Speech if active
    if (recognitionRef.current && isListeningRef.current) {
      try {
        recognitionRef.current.stop();
      } catch {
        // Safe to ignore if already stopped
      }
    }

    // 2. Stop VoiceRecorder if active (Whisper path)
    if (recorderRef.current && recorderRef.current.isRecording()) {
      isListeningRef.current = false;
      setListening(false);
      setTranscribing(true);

      const recorder = recorderRef.current;
      recorder
        .stop()
        .then(async (recorded) => {
          if (!recorded || !recorded.audio || recorded.audio.length === 0) {
            setTranscribing(false);
            isBusyRef.current = false;
            return;
          }

          try {
            const rawText = await whisperClient.transcribe(
              recorded.audio,
              vocabRef.current
            );
            const cleanText = rawText.trim();
            if (!cleanText) {
              setError("Didn't catch that");
            } else {
              const corrected = correctTranscript(cleanText, vocabRef.current);
              onFinalRef.current(corrected);
            }
          } catch (txErr) {
            const msg = txErr instanceof Error ? txErr.message : String(txErr);
            if (msg.includes('download') || msg.includes('fetch')) {
              setError('Voice download failed, tap to retry');
            } else {
              setError("Didn't catch that");
            }
          } finally {
            setTranscribing(false);
            isBusyRef.current = false;
          }
        })
        .catch(() => {
          setTranscribing(false);
          isBusyRef.current = false;
        });
      return;
    }

    isListeningRef.current = false;
    isBusyRef.current = false;
    setListening(false);
  }, [clearAutoStop]);

  // Stop recording if disabled turns true
  useEffect(() => {
    if (disabled && isListeningRef.current) {
      stop();
    }
  }, [disabled, stop]);

  // Enforce single active recording across the entire page
  useEffect(() => {
    const handleOtherStarted = (e: Event) => {
      const customEv = e as CustomEvent<{ id: string }>;
      if (customEv.detail?.id !== instanceIdRef.current && (isListeningRef.current || isBusyRef.current)) {
        stop();
      }
    };

    window.addEventListener('qshala-voice-recording-start', handleOtherStarted);
    return () => {
      window.removeEventListener('qshala-voice-recording-start', handleOtherStarted);
    };
  }, [stop]);

  const start = useCallback(async () => {
    if (disabledRef.current) return;
    if (isListeningRef.current || isBusyRef.current) return;

    // Notify other speech instances on the page to stop
    window.dispatchEvent(
      new CustomEvent('qshala-voice-recording-start', {
        detail: { id: instanceIdRef.current },
      })
    );

    setError(null);
    setInterimText('');
    isBusyRef.current = true;

    const engine = getPreferredEngine();

    // --- BROWSER ENGINE (WEB SPEECH) ---
    if (engine === 'browser') {
      const SpeechConstructor = getSpeechRecognitionConstructor();
      if (!SpeechConstructor) {
        // Fallback to Whisper immediately if constructor is unexpectedly missing
        setPreferredEngine('whisper');
        setError('Switched to offline voice, tap the mic again.');
        isBusyRef.current = false;
        return;
      }

      try {
        const recognition = new SpeechConstructor();
        recognition.lang = 'en-IN';
        recognition.continuous = false;
        recognition.interimResults = true;
        recognition.maxAlternatives = 3;

        recognition.onstart = () => {
          isListeningRef.current = true;
          setListening(true);
          setError(null);
          onStartRef.current?.();

          // Auto-stop after 30 seconds
          clearAutoStop();
          autoStopTimeoutRef.current = setTimeout(() => {
            stop();
          }, 30000);
        };

        recognition.onresult = (event: SpeechRecognitionEventLike) => {
          let interimCombined = '';
          let finalBest = '';

          for (let i = event.resultIndex; i < event.results.length; i++) {
            const result = event.results[i];
            if (!result) continue;

            if (result.isFinal) {
              let chosenText = '';
              let maxScore = -1;

              for (let a = 0; a < result.length; a++) {
                const alt = result[a];
                if (!alt || !alt.transcript) continue;
                const score = countVocabMatches(alt.transcript, vocabRef.current);
                if (score > maxScore) {
                  maxScore = score;
                  chosenText = alt.transcript;
                }
              }

              if (!chosenText && result[0]) {
                chosenText = result[0].transcript;
              }

              finalBest = chosenText;
            } else {
              if (result[0]?.transcript) {
                interimCombined += result[0].transcript;
              }
            }
          }

          if (interimCombined) {
            setInterimText(interimCombined);
            onInterimRef.current?.(interimCombined);
          }

          if (finalBest) {
            const corrected = correctTranscript(finalBest, vocabRef.current);
            setInterimText('');
            onFinalRef.current(corrected);
          }
        };

        recognition.onerror = (event: SpeechRecognitionErrorEventLike) => {
          clearAutoStop();
          isListeningRef.current = false;
          isBusyRef.current = false;
          setListening(false);
          setInterimText('');

          if (
            event.error === 'network' ||
            event.error === 'service-not-allowed' ||
            event.error === 'language-not-supported'
          ) {
            // Runtime fallback to on-device Whisper, persisted in localStorage
            setPreferredEngine('whisper');
            setError('Switched to offline voice, tap the mic again.');
          } else if (event.error === 'not-allowed') {
            setError('Allow microphone access in your browser');
          } else if (event.error === 'audio-capture') {
            setError('No microphone found');
          } else if (event.error === 'no-speech') {
            setError("Didn't catch that");
          } else if (event.error === 'aborted') {
            setError(null);
          } else {
            setError("Didn't catch that");
          }
        };

        recognition.onend = () => {
          clearAutoStop();
          isListeningRef.current = false;
          isBusyRef.current = false;
          setListening(false);
          setInterimText('');
        };

        recognitionRef.current = recognition;
        recognition.start();
      } catch {
        // Fallback to Whisper
        setPreferredEngine('whisper');
        setError('Switched to offline voice, tap the mic again.');
        isListeningRef.current = false;
        isBusyRef.current = false;
        setListening(false);
      }
      return;
    }

    // --- WHISPER OFFLINE ENGINE ---
    setPreparing(true);
    setProgress(0);

    try {
      // 1. Ensure worker and model are initialized
      await whisperClient.init((percent) => {
        setProgress(percent);
      });
      setPreparing(false);

      // 2. Start audio recorder
      if (!recorderRef.current) {
        recorderRef.current = new VoiceRecorder();
      }

      await recorderRef.current.start(() => {
        // Auto-stop at 60s
        stop();
      });

      isListeningRef.current = true;
      setListening(true);
      setError(null);
      onStartRef.current?.();
    } catch (err: unknown) {
      setPreparing(false);
      isListeningRef.current = false;
      isBusyRef.current = false;
      setListening(false);

      const errObj = err as { name?: string; message?: string };
      const errName = errObj?.name || '';
      const errMsg = errObj?.message || '';

      if (
        errName === 'NotAllowedError' ||
        errName === 'PermissionDeniedError' ||
        errMsg.includes('Permission') ||
        errMsg.includes('not allowed')
      ) {
        setError('Allow microphone access in your browser');
      } else if (
        errName === 'NotFoundError' ||
        errName === 'DevicesNotFoundError' ||
        errMsg.includes('not found')
      ) {
        setError('No microphone found');
      } else {
        // Model download failed or worker initialization failed
        whisperClient.terminate();
        setError('Voice download failed, tap to retry');
      }
    }
  }, [clearAutoStop, stop]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearAutoStop();
      if (recognitionRef.current) {
        try {
          recognitionRef.current.abort();
        } catch {
          // Safe to ignore
        }
        recognitionRef.current = null;
      }
      if (recorderRef.current) {
        recorderRef.current.abort();
        recorderRef.current = null;
      }
    };
  }, [clearAutoStop]);

  return {
    supported,
    listening,
    interimText,
    error,
    preparing,
    transcribing,
    progress,
    start,
    stop,
  };
}
