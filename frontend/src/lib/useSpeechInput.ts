'use client';

import { useState, useEffect, useRef, useCallback, useId, useSyncExternalStore } from 'react';
import { countVocabMatches, correctTranscript, FIXED_VOCABULARY } from './voiceVocabulary';

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

function getSpeechSupportedSnapshot(): boolean {
  return getSpeechRecognitionConstructor() !== null;
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
    getSpeechSupportedSnapshot,
    getServerSupportedSnapshot
  );

  const [listening, setListening] = useState<boolean>(false);
  const [interimText, setInterimText] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<SpeechRecognitionInstanceLike | null>(null);
  const autoStopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isListeningRef = useRef<boolean>(false);
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
    if (recognitionRef.current && isListeningRef.current) {
      try {
        recognitionRef.current.stop();
      } catch {
        // Safe to ignore if already stopped
      }
    }
    isListeningRef.current = false;
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
      if (customEv.detail?.id !== instanceIdRef.current && isListeningRef.current) {
        stop();
      }
    };

    window.addEventListener('qshala-voice-recording-start', handleOtherStarted);
    return () => {
      window.removeEventListener('qshala-voice-recording-start', handleOtherStarted);
    };
  }, [stop]);

  const start = useCallback(() => {
    if (disabledRef.current) return;
    const SpeechConstructor = getSpeechRecognitionConstructor();
    if (!SpeechConstructor) return;

    // Never start a second recognition while one is already running
    if (isListeningRef.current) return;

    // Notify other speech instances on the page to stop
    window.dispatchEvent(
      new CustomEvent('qshala-voice-recording-start', {
        detail: { id: instanceIdRef.current },
      })
    );

    setError(null);
    setInterimText('');

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
            // When alternatives exist, pick the one that contains the most known vocabulary words
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
            // Interim result
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
        setListening(false);
        setInterimText('');

        if (event.error === 'not-allowed') {
          setError('Allow microphone access in your browser');
        } else if (event.error === 'no-speech') {
          setError("Didn’t catch that, try again");
        } else if (event.error === 'audio-capture') {
          setError('No microphone found');
        } else if (event.error === 'network') {
          setError('Network error occurred. Retry?');
        } else if (event.error === 'aborted') {
          // Ignored per spec
          setError(null);
        } else {
          setError('Could not recognize speech, try again');
        }
      };

      recognition.onend = () => {
        clearAutoStop();
        isListeningRef.current = false;
        setListening(false);
        setInterimText('');
      };

      recognitionRef.current = recognition;
      recognition.start();
    } catch {
      setError('Could not access microphone');
      isListeningRef.current = false;
      setListening(false);
    }
  }, [clearAutoStop, stop]);

  // Cleanup and release microphone on unmount
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
    };
  }, [clearAutoStop]);

  return {
    supported,
    listening,
    interimText,
    error,
    start,
    stop,
  };
}
