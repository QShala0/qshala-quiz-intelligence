'use client';

/**
 * StagePresenter — broadcast-ready, full-screen live presenter for QShala quizzes.
 *
 * One file, no extra dependencies. Reads the real `format_payload` shapes via the type
 * guards in lib/formatPayloads.ts and drives a per-question reveal state machine with
 * keyboard control, a Web Audio countdown timer, and an auto-hiding HUD.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import {
  X,
  ChevronLeft,
  ChevronRight,
  Eye,
  Play,
  Pause,
  RotateCcw,
  Plus,
  Minus,
  Volume2,
  VolumeX,
  Maximize,
  Minimize,
  Keyboard,
  ZoomIn,
  ImageOff,
  BookOpen,
} from 'lucide-react';
import { resolveMediaUrl as defaultResolveMediaUrl, getFallbackMediaUrl } from '../lib/api';
import {
  formatStyle,
  ladderText,
  isStepped,
  isConnect,
  isImageReveal,
  isArrange,
  isMcq,
  isListIt,
  type SteppedCluePayload,
  type ConnectPayload,
  type ImageRevealPayload,
  type ArrangePayload,
  type McqPayload,
  type ListItPayload,
  type RevealPosition,
} from '../lib/formatPayloads';

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export interface PresenterQuestion {
  id: string;
  question_text: string;
  answer: string;
  explanation?: string;
  question_format?: string;
  difficulty?: string;
  points_correct?: number;
  points_wrong?: number;
  question_images?: string[];
  answer_images?: string[];
  image_refs?: string[];
  options?: string[];
  format_payload?: unknown;
  format_data?: unknown;
}

export interface StagePresenterProps {
  isOpen: boolean;
  onClose: () => void;
  questions: PresenterQuestion[];
  quizTitle?: string;
  initialQuestionIndex?: number;
  resolveMediaUrl?: (url: string | null | undefined) => string;
}

type StructuredPayload =
  | SteppedCluePayload
  | ConnectPayload
  | ImageRevealPayload
  | ArrangePayload
  | McqPayload
  | ListItPayload;

type ResolveMedia = (url: string | null | undefined) => string;

const BRAND_YELLOW = '#FDB913';

// ---------------------------------------------------------------------------
// Payload helpers
// ---------------------------------------------------------------------------

function structuredPayload(q: PresenterQuestion): StructuredPayload | null {
  const p = q.format_payload ?? q.format_data;
  if (isStepped(p)) return p;
  if (isConnect(p)) return p;
  if (isImageReveal(p)) return p;
  if (isArrange(p)) return p;
  if (isMcq(p)) return p;
  if (isListIt(p)) return p;
  return null;
}

/** Progressive reveals available before the answer is shown (0 = single reveal). */
function progressiveSteps(p: StructuredPayload | null): number {
  if (isStepped(p)) return p.clues.length;
  if (isConnect(p)) return p.items.length;
  if (isImageReveal(p)) return p.reveal_steps.length;
  return 0;
}

/** Stepped clues start with the first clue already on screen; everything else starts covered. */
function initialStep(p: StructuredPayload | null): number {
  if (isStepped(p)) return Math.min(1, p.clues.length);
  return 0;
}

function defaultDuration(p: StructuredPayload | null): number {
  if (isListIt(p) && p.timer_seconds) return p.timer_seconds;
  if (isImageReveal(p) && p.timer_seconds) return p.timer_seconds;
  if (isArrange(p) && p.timer_seconds) return p.timer_seconds;
  return 30;
}

function formatKeyOf(q: PresenterQuestion): string {
  const p = structuredPayload(q);
  if (p) return p.type;
  return q.question_format || 'STANDARD_QA';
}

function questionImages(q: PresenterQuestion): string[] {
  if (q.question_images && q.question_images.length) return q.question_images;
  return q.image_refs || [];
}

function answerImages(q: PresenterQuestion): string[] {
  if (q.answer_images && q.answer_images.length) return q.answer_images;
  return questionImages(q);
}

function hasStory(q: PresenterQuestion): boolean {
  const e = (q.explanation || '').trim();
  if (e.length < 4) return false;
  return e.toLowerCase() !== (q.answer || '').trim().toLowerCase();
}

// ---------------------------------------------------------------------------
// Media image with fallback chain + placeholder
// ---------------------------------------------------------------------------

function StageImage({
  url,
  resolveMedia,
  alt,
  className,
  onZoom,
}: {
  url: string;
  resolveMedia: ResolveMedia;
  alt: string;
  className?: string;
  onZoom?: () => void;
}) {
  const base = resolveMedia(url);
  // Reset the fallback chain when the url changes, using the render-phase reset pattern
  // (https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes)
  // so there is no setState inside an effect.
  const [st, setSt] = useState({ url, src: base, failed: false });
  if (st.url !== url) setSt({ url, src: base, failed: false });
  const src = st.url === url ? st.src : base;
  const failed = st.url === url ? st.failed : false;

  if (failed || !src) {
    return (
      <div
        className={`flex items-center justify-center rounded-xl border border-white/10 bg-white/5 text-white/30 ${className || ''}`}
        aria-label="Image unavailable"
      >
        <ImageOff className="h-10 w-10" />
      </div>
    );
  }

  const img = (
    // eslint-disable-next-line @next/next/no-img-element -- quiz media is user-uploaded/remote; next/image adds no value on a projector stage and would need remote-pattern config
    <img
      src={src}
      alt={alt}
      className={className}
      draggable={false}
      onError={() => {
        const fb = getFallbackMediaUrl(src);
        if (fb && fb !== src) setSt({ url, src: fb, failed: false });
        else setSt({ url, src, failed: true });
      }}
    />
  );

  if (!onZoom) return img;
  return (
    <button
      type="button"
      onClick={onZoom}
      aria-label={`Zoom image: ${alt}`}
      className="group relative cursor-zoom-in focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60 rounded-xl"
    >
      {img}
      <span className="pointer-events-none absolute right-2 top-2 rounded-md bg-black/50 p-1.5 text-white/80 opacity-0 transition-opacity group-hover:opacity-100">
        <ZoomIn className="h-4 w-4" />
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Error boundary (per question)
// ---------------------------------------------------------------------------

class QuestionErrorBoundary extends React.Component<
  { children: ReactNode; fallback: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode; fallback: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  componentDidCatch() {
    /* swallow — a malformed question must never crash the stage */
  }
  render() {
    return this.state.hasError ? this.props.fallback : this.props.children;
  }
}

// ---------------------------------------------------------------------------
// Engine views
// ---------------------------------------------------------------------------

interface EngineProps {
  q: PresenterQuestion;
  step: number;
  revealed: boolean;
  resolveMedia: ResolveMedia;
  reduceMotion: boolean;
  onZoom: (url: string) => void;
}

const QUESTION_TEXT_STYLE: React.CSSProperties = { fontSize: 'clamp(1.75rem, 3.4vw, 3.75rem)' };
const PROMPT_TEXT_STYLE: React.CSSProperties = { fontSize: 'clamp(1.4rem, 2.6vw, 2.6rem)' };

function SteppedView({ payload, step, resolveMedia, onZoom }: { payload: SteppedCluePayload } & EngineProps) {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
      {payload.clues.map((c, i) => {
        const open = i < step;
        const isCurrent = i === step - 1;
        const points = ladderText(c.correct, c.wrong);
        return (
          <div
            key={c.step}
            className={`rounded-2xl border px-6 py-5 transition-all duration-300 ${
              open
                ? isCurrent
                  ? 'border-amber-300/70 bg-amber-400/10 shadow-lg shadow-amber-500/10'
                  : 'border-white/10 bg-white/5 opacity-60'
                : 'border-dashed border-white/15 bg-white/[0.02]'
            }`}
          >
            <div className="mb-2 flex items-center gap-3">
              <span className="rounded-lg bg-amber-400/20 px-3 py-1 font-mono text-sm font-bold text-amber-200">
                Clue {c.step}
              </span>
              {points && (
                <span className="rounded-lg bg-white/10 px-3 py-1 font-mono text-sm font-semibold text-white/80">
                  {points}
                </span>
              )}
            </div>
            {open ? (
              <div className="flex flex-wrap items-center gap-5">
                <p className="min-w-0 flex-1 text-white" style={PROMPT_TEXT_STYLE}>
                  {c.text || <span className="text-white/40">(no clue text)</span>}
                </p>
                {c.images.slice(0, 2).map((img) => (
                  <StageImage
                    key={img}
                    url={img}
                    resolveMedia={resolveMedia}
                    alt={`Clue ${c.step}`}
                    onZoom={() => onZoom(img)}
                    className="max-h-[34vh] w-auto rounded-xl object-contain"
                  />
                ))}
              </div>
            ) : (
              <p className="text-xl font-medium text-white/30">Hidden — press Next to reveal</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

const QUADRANT: Record<string, React.CSSProperties> = {
  'top-left': { top: 0, left: 0 },
  'top-right': { top: 0, right: 0 },
  'bottom-left': { bottom: 0, left: 0 },
  'bottom-right': { bottom: 0, right: 0 },
};

function ImageRevealView({ payload, step, resolveMedia, onZoom }: { payload: ImageRevealPayload } & EngineProps) {
  const steps = useMemo(() => [...payload.reveal_steps].sort((a, b) => a.step - b.step), [payload.reveal_steps]);
  const uncovered = new Set(steps.slice(0, step).map((s) => s.position));
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col items-center gap-5">
      {payload.prompt && (
        <p className="text-center font-semibold text-white" style={PROMPT_TEXT_STYLE}>
          {payload.prompt}
        </p>
      )}
      <div className="relative aspect-video w-full overflow-hidden rounded-2xl border border-white/10 bg-black/40">
        {payload.full_image ? (
          <StageImage
            url={payload.full_image}
            resolveMedia={resolveMedia}
            alt="Reveal image"
            onZoom={() => payload.full_image && onZoom(payload.full_image)}
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-white/30">
            <ImageOff className="h-12 w-12" />
          </div>
        )}
        {steps.map((s) => {
          const open = uncovered.has(s.position);
          const pos = (QUADRANT[s.position as RevealPosition] as React.CSSProperties) || QUADRANT['top-left'];
          const points = ladderText(s.correct, s.wrong);
          return (
            <div
              key={`${s.position}-${s.step}`}
              style={{ ...pos, width: '50%', height: '50%' }}
              className={`absolute flex flex-col items-center justify-center gap-2 transition-opacity duration-500 ${
                open ? 'pointer-events-none opacity-0' : 'opacity-100'
              }`}
            >
              <div className="flex h-full w-full flex-col items-center justify-center gap-2 border border-slate-900/40 bg-gradient-to-br from-indigo-900 to-slate-900 text-white/90">
                <span className="text-3xl font-black">{s.step}</span>
                {points && <span className="font-mono text-sm text-white/70">{points}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConnectView({ payload, step, revealed, resolveMedia, onZoom }: { payload: ConnectPayload } & EngineProps) {
  const items = useMemo(() => [...payload.items].sort((a, b) => a.order - b.order), [payload.items]);
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col items-center gap-5">
      {payload.text_hint && (
        <p className="text-center font-semibold text-white/90" style={PROMPT_TEXT_STYLE}>
          {payload.text_hint}
        </p>
      )}
      <div className="grid w-full grid-cols-2 gap-4">
        {items.map((it, i) => {
          const open = i < step;
          const label = payload.item_labels?.[i];
          return (
            <div
              key={`${it.order}-${i}`}
              className="relative aspect-video overflow-hidden rounded-2xl border border-white/10 bg-black/40"
            >
              {open ? (
                <>
                  <StageImage
                    url={it.image}
                    resolveMedia={resolveMedia}
                    alt={label || `Clue ${i + 1}`}
                    onZoom={() => onZoom(it.image)}
                    className="h-full w-full object-contain"
                  />
                  {label && (
                    <span className="absolute bottom-2 left-2 rounded-md bg-black/60 px-2 py-1 text-sm font-semibold text-white">
                      {label}
                    </span>
                  )}
                </>
              ) : (
                <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-indigo-900 to-slate-900 text-5xl font-black text-white/70">
                  {i + 1}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {revealed && (
        <p className="text-center text-2xl font-semibold text-sky-300">The connect links all four clues.</p>
      )}
    </div>
  );
}

function ArrangeView({ payload, revealed, resolveMedia, reduceMotion, onZoom }: { payload: ArrangePayload } & EngineProps) {
  const ordered = useMemo(() => {
    if (!revealed || !payload.correct_order?.length) return payload.items;
    const byLabel = new Map(payload.items.map((it) => [it.label, it]));
    const seq = payload.correct_order.map((l) => byLabel.get(l)).filter(Boolean) as ArrangePayload['items'];
    return seq.length ? seq : payload.items;
  }, [payload.items, payload.correct_order, revealed]);

  const orderKnown = payload.order_source && payload.order_source !== 'none' && payload.correct_order?.length;

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
      {payload.prompt && (
        <p className="text-center font-semibold text-white" style={PROMPT_TEXT_STYLE}>
          {payload.prompt}
          {payload.direction && (
            <span className="ml-2 text-lg font-normal text-white/50">
              ({payload.direction === 'asc' ? 'ascending' : 'descending'})
            </span>
          )}
        </p>
      )}
      <div className="flex flex-col gap-3">
        {ordered.map((it, i) => (
          <div
            key={it.label}
            style={reduceMotion ? undefined : { transitionDelay: `${i * 60}ms` }}
            className={`flex items-center gap-4 rounded-2xl border px-5 py-4 transition-all duration-300 ${
              revealed ? 'border-emerald-300/50 bg-emerald-400/10' : 'border-white/10 bg-white/5'
            }`}
          >
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-lg font-black ${
                revealed ? 'bg-emerald-400 text-slate-900' : 'bg-white/10 text-white/70'
              }`}
            >
              {revealed && orderKnown ? i + 1 : String.fromCharCode(65 + i)}
            </span>
            {it.image && (
              <StageImage
                url={it.image}
                resolveMedia={resolveMedia}
                alt={it.text}
                onZoom={() => it.image && onZoom(it.image)}
                className="h-16 w-24 shrink-0 rounded-lg object-contain"
              />
            )}
            <span className="min-w-0 flex-1 text-2xl font-semibold text-white">{it.text}</span>
            {revealed && it.value && (
              <span className="shrink-0 rounded-lg bg-white/10 px-3 py-1 font-mono text-lg text-sky-200">
                {it.value}
              </span>
            )}
          </div>
        ))}
      </div>
      {revealed && !orderKnown && (
        <p className="text-center text-base text-amber-300/80">No stored order — see the answer below.</p>
      )}
    </div>
  );
}

function OptionGrid({
  options,
  correctIndexes,
  revealed,
}: {
  options: { letter?: string; text: string; correct?: boolean | null }[];
  correctIndexes: Set<number>;
  revealed: boolean;
}) {
  return (
    <div className="mx-auto grid w-full max-w-4xl grid-cols-1 gap-3 sm:grid-cols-2">
      {options.map((o, i) => {
        const hit = revealed && correctIndexes.has(i);
        return (
          <div
            key={i}
            className={`flex items-center gap-4 rounded-2xl border px-5 py-4 text-left transition-all duration-300 ${
              hit ? 'border-emerald-300 bg-emerald-400/15 shadow-lg shadow-emerald-500/10' : 'border-white/10 bg-white/5'
            }`}
          >
            <span
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-lg font-black ${
                hit ? 'bg-emerald-400 text-slate-900' : 'bg-white/10 text-white/70'
              }`}
            >
              {o.letter || String.fromCharCode(65 + i)}
            </span>
            <span className="text-2xl font-semibold text-white">{o.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function McqView({ payload, revealed }: { payload: McqPayload } & EngineProps) {
  const correct = new Set<number>();
  if (payload.correct_letter) {
    payload.options.forEach((o, i) => {
      if (o.letter === payload.correct_letter) correct.add(i);
    });
  }
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      {payload.prompt && (
        <h2 className="text-center font-bold text-white" style={QUESTION_TEXT_STYLE}>
          {payload.prompt}
        </h2>
      )}
      <OptionGrid options={payload.options} correctIndexes={correct} revealed={revealed} />
    </div>
  );
}

function ListItView({ payload, revealed }: { payload: ListItPayload } & EngineProps) {
  const correct = new Set<number>();
  payload.options.forEach((o, i) => {
    if (o.correct) correct.add(i);
  });
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      {payload.prompt && (
        <h2 className="text-center font-bold text-white" style={QUESTION_TEXT_STYLE}>
          {payload.prompt}
        </h2>
      )}
      {payload.max_picks > 0 && (
        <p className="text-center text-lg text-white/50">Pick up to {payload.max_picks}</p>
      )}
      <OptionGrid options={payload.options} correctIndexes={correct} revealed={revealed} />
    </div>
  );
}

function StandardView({ q, resolveMedia, onZoom }: EngineProps) {
  const imgs = questionImages(q);
  const hasImg = imgs.length > 0;
  return (
    <div
      className={`mx-auto flex w-full max-w-6xl items-center gap-8 ${hasImg ? 'flex-col lg:flex-row' : 'flex-col justify-center'}`}
    >
      <div className={`flex flex-col gap-5 ${hasImg ? 'flex-1' : 'max-w-4xl text-center'}`}>
        <h2 className="font-bold leading-tight text-white" style={QUESTION_TEXT_STYLE}>
          {q.question_text}
        </h2>
        {q.options && q.options.length > 0 && (
          <div className="flex flex-col gap-3">
            {q.options.map((opt, i) => (
              <div
                key={i}
                className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-xl font-medium text-white/90"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 text-sm font-bold text-white/70">
                  {String.fromCharCode(65 + i)}
                </span>
                {opt}
              </div>
            ))}
          </div>
        )}
      </div>
      {hasImg && (
        <div className="flex flex-1 items-center justify-center">
          <StageImage
            url={imgs[0]}
            resolveMedia={resolveMedia}
            alt="Question media"
            onZoom={() => onZoom(imgs[0])}
            className="max-h-[60vh] w-auto rounded-2xl object-contain shadow-2xl"
          />
        </div>
      )}
    </div>
  );
}

function EngineView(props: EngineProps) {
  const payload = structuredPayload(props.q);
  if (isStepped(payload)) return <SteppedView {...props} payload={payload} />;
  if (isImageReveal(payload)) return <ImageRevealView {...props} payload={payload} />;
  if (isConnect(payload)) return <ConnectView {...props} payload={payload} />;
  if (isArrange(payload)) return <ArrangeView {...props} payload={payload} />;
  if (isMcq(payload)) return <McqView {...props} payload={payload} />;
  if (isListIt(payload)) return <ListItView {...props} payload={payload} />;
  return <StandardView {...props} />;
}

// ---------------------------------------------------------------------------
// Timer ring
// ---------------------------------------------------------------------------

function TimerRing({ seconds, duration, warning }: { seconds: number; duration: number; warning: boolean }) {
  const r = 26;
  const circ = 2 * Math.PI * r;
  const frac = duration > 0 ? Math.max(0, Math.min(1, seconds / duration)) : 0;
  return (
    <div className="relative h-16 w-16">
      <svg viewBox="0 0 64 64" className="h-16 w-16 -rotate-90">
        <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="6" />
        <circle
          cx="32"
          cy="32"
          r={r}
          fill="none"
          stroke={warning ? '#f87171' : BRAND_YELLOW}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={circ * (1 - frac)}
          style={{ transition: 'stroke-dashoffset 1s linear' }}
        />
      </svg>
      <span
        className={`absolute inset-0 flex items-center justify-center text-xl font-black tabular-nums ${
          warning ? 'text-red-400' : 'text-white'
        }`}
      >
        {seconds}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function StagePresenter({
  isOpen,
  onClose,
  questions,
  quizTitle,
  initialQuestionIndex = 0,
  resolveMediaUrl,
}: StagePresenterProps) {
  const resolveMedia: ResolveMedia = resolveMediaUrl || defaultResolveMediaUrl;

  const [index, setIndex] = useState(initialQuestionIndex);
  const [qKey, setQKey] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [storyOpen, setStoryOpen] = useState(false);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);

  // Timer state
  const [duration, setDuration] = useState(30);
  const [secondsLeft, setSecondsLeft] = useState(30);
  const [timerRunning, setTimerRunning] = useState(false);
  const [muted, setMuted] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<Element | null>(null);
  const secRef = useRef(30);
  const mutedRef = useRef(false);
  const audioRef = useRef<AudioContext | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reduceMotion = useMemo(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    [],
  );

  const total = questions.length;
  const current = questions[index];
  const payload = current ? structuredPayload(current) : null;
  const maxSteps = progressiveSteps(payload);

  // Render-phase resets (https://react.dev/learn/you-might-not-need-an-effect) — no setState in effects.
  // Keep `index` in range when the questions list shrinks.
  if (total > 0 && index > total - 1) setIndex(total - 1);
  // Reset per-question state (and the timer default) whenever the current question changes.
  const curId = current?.id ?? null;
  if (curId !== qKey) {
    setQKey(curId);
    const p0 = current ? structuredPayload(current) : null;
    setStep(initialStep(p0));
    setRevealed(false);
    setStoryOpen(false);
    const d = defaultDuration(p0);
    setDuration(d);
    setSecondsLeft(d);
    setTimerRunning(false);
  }

  useEffect(() => {
    mutedRef.current = muted;
  }, [muted]);
  // Mirror the live countdown into a ref so the 1s interval reads a fresh value without re-subscribing.
  useEffect(() => {
    secRef.current = secondsLeft;
  }, [secondsLeft]);

  // ---- audio ----
  const ensureAudio = useCallback(() => {
    if (typeof window === 'undefined') return null;
    try {
      if (!audioRef.current) {
        const Ctor =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
        if (Ctor) audioRef.current = new Ctor();
      }
      if (audioRef.current?.state === 'suspended') void audioRef.current.resume();
    } catch {
      /* audio unavailable — stay silent */
    }
    return audioRef.current;
  }, []);

  const beep = useCallback(
    (freq: number, ms: number) => {
      if (mutedRef.current) return;
      const ctx = audioRef.current;
      if (!ctx) return;
      try {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.0001, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + ms / 1000);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + ms / 1000);
      } catch {
        /* ignore */
      }
    },
    [],
  );

  // ---- timer engine ----
  useEffect(() => {
    if (!timerRunning) return;
    const id = setInterval(() => {
      const next = secRef.current - 1;
      secRef.current = next;
      setSecondsLeft(next);
      if (next > 0 && next <= 5) beep(880, 120);
      else if (next <= 0) {
        beep(523, 500);
        setTimerRunning(false);
      }
    }, 1000);
    return () => clearInterval(id);
  }, [timerRunning, beep]);

  const applyDuration = useCallback((d: number) => {
    const safe = Math.max(5, Math.min(600, d));
    secRef.current = safe;
    setDuration(safe);
    setSecondsLeft(safe);
    setTimerRunning(false);
  }, []);

  const startPauseTimer = useCallback(() => {
    ensureAudio();
    if (timerRunning) {
      setTimerRunning(false);
      return;
    }
    if (secRef.current <= 0) setSecondsLeft(duration);
    setTimerRunning(true);
  }, [duration, ensureAudio, timerRunning]);

  const resetTimer = useCallback(() => {
    secRef.current = duration;
    setSecondsLeft(duration);
    setTimerRunning(false);
  }, [duration]);

  // ---- navigation ----
  const goNext = useCallback(() => {
    if (step < maxSteps) {
      setStep((s) => s + 1);
    } else if (!revealed) {
      setRevealed(true);
    } else if (index < total - 1) {
      setIndex((i) => i + 1);
    }
  }, [step, maxSteps, revealed, index, total]);

  const goPrev = useCallback(() => {
    if (revealed) {
      setRevealed(false);
      setStoryOpen(false);
    } else if (step > initialStep(payload)) {
      setStep((s) => s - 1);
    } else if (index > 0) {
      setIndex((i) => i - 1);
    }
  }, [revealed, step, payload, index]);

  const resetQuestion = useCallback(() => {
    setStep(initialStep(payload));
    setRevealed(false);
    setStoryOpen(false);
    resetTimer();
  }, [payload, resetTimer]);

  const revealAnswer = useCallback(() => {
    setStep(maxSteps);
    setRevealed(true);
  }, [maxSteps]);

  // ---- fullscreen ----
  const toggleFullscreen = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (!document.fullscreenElement) {
      el.requestFullscreen?.().catch(() => {
        /* denied — stay windowed */
      });
    } else {
      document.exitFullscreen?.().catch(() => {});
    }
  }, []);

  // ---- auto-hide controls in fullscreen ----
  const pokeControls = useCallback(() => {
    setControlsVisible(true);
    if (hideTimer.current) clearTimeout(hideTimer.current);
    if (document.fullscreenElement) {
      hideTimer.current = setTimeout(() => setControlsVisible(false), 3000);
    }
  }, []);

  useEffect(() => {
    const onFs = () => {
      const fs = !!document.fullscreenElement;
      setIsFullscreen(fs);
      if (fs) {
        pokeControls();
      } else {
        setControlsVisible(true);
        if (hideTimer.current) clearTimeout(hideTimer.current);
      }
    };
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, [pokeControls]);

  // ---- keyboard ----
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return;
      const onControl = tag === 'BUTTON' || tag === 'A' || t?.getAttribute('role') === 'button';

      switch (e.key) {
        case 'Escape':
          e.preventDefault();
          if (lightbox) setLightbox(null);
          else if (showHelp) setShowHelp(false);
          else onClose();
          break;
        case 'ArrowRight':
          e.preventDefault();
          goNext();
          break;
        case ' ':
        case 'Spacebar':
          if (onControl) return;
          e.preventDefault();
          goNext();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          goPrev();
          break;
        case 'f':
        case 'F':
          toggleFullscreen();
          break;
        case 't':
        case 'T':
          startPauseTimer();
          break;
        case 'a':
        case 'A':
          revealAnswer();
          break;
        case 'r':
        case 'R':
          resetQuestion();
          break;
        case '1':
          applyDuration(30);
          break;
        case '2':
          applyDuration(60);
          break;
        case '?':
          setShowHelp((v) => !v);
          break;
        default:
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [
    isOpen,
    lightbox,
    showHelp,
    goNext,
    goPrev,
    toggleFullscreen,
    startPauseTimer,
    revealAnswer,
    resetQuestion,
    applyDuration,
    onClose,
  ]);

  // ---- body scroll lock + focus management ----
  useEffect(() => {
    if (!isOpen) return;
    previouslyFocused.current = document.activeElement;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const id = window.setTimeout(() => containerRef.current?.focus(), 0);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.clearTimeout(id);
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
      const prev = previouslyFocused.current as HTMLElement | null;
      prev?.focus?.();
    };
  }, [isOpen]);

  // ---- close audio context on unmount ----
  useEffect(() => {
    return () => {
      audioRef.current?.close().catch(() => {});
      audioRef.current = null;
    };
  }, []);

  // ---- preload next question's images ----
  useEffect(() => {
    const nxt = questions[index + 1];
    if (!nxt || typeof window === 'undefined') return;
    [...questionImages(nxt), ...answerImages(nxt)].slice(0, 4).forEach((u) => {
      const src = resolveMedia(u);
      if (src) {
        const img = new window.Image();
        img.src = src;
      }
    });
  }, [index, questions, resolveMedia]);

  if (!isOpen || typeof document === 'undefined') return null;

  const fmtKey = current ? formatKeyOf(current) : 'STANDARD_QA';
  const fmt = formatStyle(fmtKey);
  const warning = timerRunning && secondsLeft <= 5 && secondsLeft > 0;

  const hudHidden = isFullscreen && !controlsVisible;

  const body = (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label={`${quizTitle || 'Quiz'} presenter`}
      tabIndex={-1}
      onMouseMove={isFullscreen ? pokeControls : undefined}
      className="fixed inset-0 z-[100] flex flex-col overflow-hidden bg-gradient-to-br from-slate-950 via-indigo-950 to-slate-950 text-white outline-none"
      style={{ cursor: hudHidden ? 'none' : 'default' }}
    >
      {!current ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-6">
          <p className="text-2xl font-semibold text-white/70">No approved questions to present.</p>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-white/10 px-6 py-3 font-semibold hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
          >
            Close
          </button>
        </div>
      ) : (
        <>
          {/* ---- Top HUD ---- */}
          <header
            className={`flex items-center justify-between gap-4 px-6 py-4 transition-all duration-300 ${
              hudHidden ? '-translate-y-full opacity-0' : 'translate-y-0 opacity-100'
            }`}
          >
            <div className="flex min-w-0 items-center gap-3">
              <span className="truncate text-lg font-bold" style={{ color: BRAND_YELLOW }}>
                {quizTitle || 'QShala Quiz'}
              </span>
              <span className="shrink-0 rounded-lg bg-white/10 px-3 py-1 text-sm font-semibold tabular-nums">
                Q {index + 1} / {total}
              </span>
              <span className={`shrink-0 rounded-lg border px-3 py-1 text-sm font-semibold ${fmt.chip}`}>
                {fmt.label}
              </span>
              {current.difficulty && (
                <span className="hidden shrink-0 rounded-lg bg-white/10 px-3 py-1 text-sm font-medium text-white/70 sm:inline">
                  {current.difficulty}
                </span>
              )}
              {(current.points_correct != null || current.points_wrong != null) && (
                <span className="hidden shrink-0 rounded-lg bg-white/10 px-3 py-1 font-mono text-sm sm:inline">
                  {current.points_correct != null && (
                    <span className="text-emerald-300">+{current.points_correct}</span>
                  )}
                  {current.points_wrong != null && (
                    <span className="ml-2 text-red-300">-{Math.abs(current.points_wrong)}</span>
                  )}
                </span>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setShowHelp((v) => !v)}
                aria-label="Keyboard shortcuts"
                className="rounded-lg p-2 text-white/70 hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
              >
                <Keyboard className="h-5 w-5" />
              </button>
              <button
                type="button"
                onClick={toggleFullscreen}
                aria-label={isFullscreen ? 'Exit full screen' : 'Enter full screen'}
                className="rounded-lg p-2 text-white/70 hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
              >
                {isFullscreen ? <Minimize className="h-5 w-5" /> : <Maximize className="h-5 w-5" />}
              </button>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close presenter"
                className="rounded-lg p-2 text-white/70 hover:bg-red-500/20 hover:text-red-200 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </header>

          {/* ---- Stage ---- */}
          <main className="flex flex-1 items-center justify-center overflow-y-auto px-6 py-4">
            <QuestionErrorBoundary
              key={current.id || index}
              fallback={
                <div className="flex flex-col items-center gap-4 text-center">
                  <h2 className="font-bold text-white" style={QUESTION_TEXT_STYLE}>
                    {current.question_text}
                  </h2>
                  <p className="text-white/50">This question&apos;s rich view could not be shown.</p>
                </div>
              }
            >
              <EngineView
                q={current}
                step={step}
                revealed={revealed}
                resolveMedia={resolveMedia}
                reduceMotion={reduceMotion}
                onZoom={(u) => setLightbox(u)}
              />
            </QuestionErrorBoundary>
          </main>

          {/* ---- Answer drawer ---- */}
          {revealed && (
            <section
              aria-live="polite"
              className="mx-auto w-full max-w-6xl shrink-0 px-6 pb-2"
              style={reduceMotion ? undefined : { animation: 'stageSlideUp 300ms ease-out' }}
            >
              <div className="rounded-t-3xl border border-white/15 bg-white px-8 py-5 text-slate-900 shadow-2xl">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <span className="text-sm font-bold uppercase tracking-widest text-emerald-700">Answer</span>
                  <h3 className="font-black leading-tight" style={{ fontSize: 'clamp(1.75rem, 3vw, 3rem)' }}>
                    {current.answer}
                  </h3>
                  {hasStory(current) && (
                    <button
                      type="button"
                      onClick={() => setStoryOpen((v) => !v)}
                      className="ml-auto flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                    >
                      <BookOpen className="h-4 w-4" />
                      {storyOpen ? 'Hide story' : 'Story behind the question'}
                    </button>
                  )}
                </div>
                {storyOpen && hasStory(current) && (
                  <p className="mt-4 max-h-[22vh] overflow-y-auto text-lg leading-relaxed text-slate-700">
                    {current.explanation}
                  </p>
                )}
              </div>
            </section>
          )}

          {/* ---- Bottom HUD ---- */}
          <footer
            className={`flex items-center justify-between gap-4 px-6 py-4 transition-all duration-300 ${
              hudHidden ? 'translate-y-full opacity-0' : 'translate-y-0 opacity-100'
            }`}
          >
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={goPrev}
                aria-label="Previous"
                className="rounded-xl bg-white/10 p-3 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
              >
                <ChevronLeft className="h-6 w-6" />
              </button>
              <button
                type="button"
                onClick={goNext}
                aria-label="Next / reveal"
                className="flex items-center gap-2 rounded-xl px-5 py-3 font-bold text-slate-900 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                style={{ backgroundColor: BRAND_YELLOW }}
              >
                {step < maxSteps ? <ChevronRight className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                {step < maxSteps ? 'Next' : revealed ? 'Next question' : 'Reveal'}
              </button>
              <button
                type="button"
                onClick={resetQuestion}
                aria-label="Reset this question"
                className="rounded-xl bg-white/10 p-3 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
              >
                <RotateCcw className="h-5 w-5" />
              </button>
            </div>

            {/* progress dots */}
            <div className="hidden min-w-0 flex-1 items-center justify-center gap-1.5 overflow-hidden lg:flex">
              {questions.map((qq, i) => (
                <span
                  key={qq.id || i}
                  className={`h-2 rounded-full transition-all ${
                    i === index ? 'w-6 bg-[color:var(--d)]' : 'w-2 bg-white/20'
                  }`}
                  style={i === index ? ({ ['--d' as string]: BRAND_YELLOW } as React.CSSProperties) : undefined}
                />
              ))}
            </div>

            {/* timer cluster */}
            <div className="flex items-center gap-2">
              <TimerRing seconds={secondsLeft} duration={duration} warning={warning} />
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={startPauseTimer}
                    aria-label={timerRunning ? 'Pause timer' : 'Start timer'}
                    className="rounded-lg bg-white/10 p-2 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                  >
                    {timerRunning ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                  </button>
                  <button
                    type="button"
                    onClick={resetTimer}
                    aria-label="Reset timer"
                    className="rounded-lg bg-white/10 p-2 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                  >
                    <RotateCcw className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setMuted((m) => !m)}
                    aria-label={muted ? 'Unmute timer sound' : 'Mute timer sound'}
                    className="rounded-lg bg-white/10 p-2 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                  >
                    {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
                  </button>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => applyDuration(duration - 10)}
                    aria-label="Minus 10 seconds"
                    className="rounded-lg bg-white/10 p-1.5 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                  >
                    <Minus className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => applyDuration(30)}
                    className={`rounded-lg px-2 py-1 text-xs font-bold focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60 ${
                      duration === 30 ? 'bg-white text-slate-900' : 'bg-white/10 hover:bg-white/20'
                    }`}
                  >
                    30
                  </button>
                  <button
                    type="button"
                    onClick={() => applyDuration(60)}
                    className={`rounded-lg px-2 py-1 text-xs font-bold focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60 ${
                      duration === 60 ? 'bg-white text-slate-900' : 'bg-white/10 hover:bg-white/20'
                    }`}
                  >
                    60
                  </button>
                  <button
                    type="button"
                    onClick={() => applyDuration(duration + 10)}
                    aria-label="Plus 10 seconds"
                    className="rounded-lg bg-white/10 p-1.5 hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          </footer>
        </>
      )}

      {/* ---- Lightbox ---- */}
      {lightbox && (
        <div
          className="fixed inset-0 z-[110] flex items-center justify-center bg-black/90 p-6"
          onClick={() => setLightbox(null)}
          role="dialog"
          aria-modal="true"
          aria-label="Zoomed image"
        >
          <StageImage
            url={lightbox}
            resolveMedia={resolveMedia}
            alt="Zoomed"
            className="max-h-[92vh] max-w-[92vw] object-contain"
          />
          <button
            type="button"
            onClick={() => setLightbox(null)}
            aria-label="Close zoom"
            className="absolute right-6 top-6 rounded-full bg-white/10 p-3 text-white hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
          >
            <X className="h-6 w-6" />
          </button>
        </div>
      )}

      {/* ---- Keyboard help ---- */}
      {showHelp && (
        <div
          className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 p-6"
          onClick={() => setShowHelp(false)}
        >
          <div
            className="w-full max-w-md rounded-2xl border border-white/15 bg-slate-900 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-4 text-lg font-bold">Keyboard shortcuts</h3>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              {[
                ['→ / Space', 'Next step or reveal'],
                ['←', 'Previous'],
                ['A', 'Reveal answer'],
                ['R', 'Reset question'],
                ['T', 'Start / pause timer'],
                ['1 / 2', 'Timer 30s / 60s'],
                ['F', 'Toggle full screen'],
                ['Esc', 'Close (zoom first)'],
              ].map(([k, v]) => (
                <React.Fragment key={k}>
                  <dt className="font-mono font-semibold text-amber-200">{k}</dt>
                  <dd className="text-white/70">{v}</dd>
                </React.Fragment>
              ))}
            </dl>
            <button
              type="button"
              onClick={() => setShowHelp(false)}
              className="mt-5 w-full rounded-xl bg-white/10 py-2 font-semibold hover:bg-white/20 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400/60"
            >
              Close
            </button>
          </div>
        </div>
      )}

      <style>{`@keyframes stageSlideUp{from{transform:translateY(40px);opacity:0}to{transform:translateY(0);opacity:1}}`}</style>
    </div>
  );

  return createPortal(body, document.body);
}
