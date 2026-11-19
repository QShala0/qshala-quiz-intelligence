'use client';

import React, { useEffect, useState } from 'react';
import { Mic, MicOff } from 'lucide-react';
import { useSpeechInput } from '../lib/useSpeechInput';
import { formatTranscriptForMode } from '../lib/voiceVocabulary';

export interface VoiceInputButtonProps {
  onText: (text: string) => void;
  onInterim?: (interim: string) => void;
  onStart?: () => void;
  mode?: 'prompt' | 'search';
  disabled?: boolean;
  vocabulary?: readonly string[];
  className?: string;
  buttonSize?: 'sm' | 'md';
}

export default function VoiceInputButton({
  onText,
  onInterim,
  onStart,
  mode = 'prompt',
  disabled = false,
  vocabulary,
  className = '',
  buttonSize = 'sm',
}: VoiceInputButtonProps) {
  const [dismissedError, setDismissedError] = useState<string | null>(null);

  const handleFinal = (text: string) => {
    const formatted = formatTranscriptForMode(text, mode);
    if (formatted) {
      onText(formatted);
    }
  };

  const handleInterim = (interim: string) => {
    if (onInterim) {
      const formatted = formatTranscriptForMode(interim, mode);
      onInterim(formatted);
    }
  };

  const { supported, listening, error, start, stop } = useSpeechInput({
    onFinal: handleFinal,
    onInterim: handleInterim,
    onStart,
    vocabulary,
    disabled,
  });

  // Automatically dismiss error toast after 4 seconds without calling setState synchronously in effect
  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => {
      setDismissedError(error);
    }, 4000);
    return () => clearTimeout(timer);
  }, [error]);

  const hasVisibleError = Boolean(error && error !== dismissedError);

  if (!supported) {
    return null;
  }

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (disabled) return;

    if (listening) {
      stop();
    } else {
      start();
    }
  };

  const sizeClasses =
    buttonSize === 'md'
      ? 'h-8 w-8'
      : 'h-6.5 w-6.5';

  const iconSize =
    buttonSize === 'md'
      ? 'h-4 w-4'
      : 'h-3.5 w-3.5';

  return (
    <div className={`relative inline-flex items-center ${className}`}>
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        aria-label={listening ? 'Stop listening' : `Voice input for ${mode}`}
        aria-pressed={listening}
        title={listening ? 'Listening... click to stop' : `Voice input (${mode})`}
        className={`relative flex items-center justify-center rounded-full transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-offset-1 disabled:opacity-40 disabled:cursor-not-allowed ${sizeClasses} ${
          listening
            ? 'bg-rose-50 text-rose-600 ring-2 ring-rose-500/80 focus:ring-rose-500 animate-pulse'
            : 'text-slate-400 hover:text-slate-700 hover:bg-slate-100/90 focus:ring-blue-500/60'
        }`}
      >
        {listening ? (
          <>
            <Mic className={`${iconSize} text-rose-600`} />
            {/* Pulsing red dot badge */}
            <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-rose-600"></span>
            </span>
          </>
        ) : disabled ? (
          <MicOff className={`${iconSize} opacity-50`} />
        ) : (
          <Mic className={iconSize} />
        )}
      </button>

      {/* Screen reader live announcements */}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {listening ? 'Listening. Speak into your microphone.' : error || ''}
      </div>

      {/* Visual error message pill */}
      {hasVisibleError && error && (
        <div
          role="alert"
          className="absolute bottom-full right-0 mb-2 z-50 whitespace-nowrap rounded-lg bg-slate-900/95 px-2.5 py-1 text-[11px] font-medium text-white shadow-lg pointer-events-none transition-opacity"
        >
          {error}
        </div>
      )}
    </div>
  );
}
