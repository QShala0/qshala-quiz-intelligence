// Shapes of `format_payload` as written by format-aware ingestion (backend/app/services/ingestion/format_extractors.py),
// plus small helpers the question views share. Rows ingested before that existed carry older payloads
// (Connect "elements", Stepped "points", Arrange "key"); the guards below say whether a payload is the new shape,
// so the old rendering stays as the fallback.

export type FormatName =
  | 'STANDARD_QA' | 'LIST_IT' | 'IMAGE_REVEAL' | 'CONNECT' | 'STEPPED_CLUE' | 'ARRANGE_ORDER' | 'MCQ';

export interface ExtractionInfo {
  confidence: number;
  needs_review: boolean;
  route: 'accept' | 'vision' | 'review' | string;
  reasons: string[];
  reviewed?: boolean;
}

interface PayloadBase {
  type: string;
  round_name?: string | null;
  round_number?: number | null;
  team?: number | null;
  extraction?: ExtractionInfo;
}

export interface ListItPayload extends PayloadBase {
  type: 'LIST_IT';
  prompt: string;
  timer_seconds: number | null;
  max_picks: number;
  scoring: { per_correct: number | null };
  options: { text: string; correct: boolean | null }[];
}

export type RevealPosition = 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';

export interface ImageRevealPayload extends PayloadBase {
  type: 'IMAGE_REVEAL';
  prompt: string;
  full_image: string | null;
  timer_seconds: number | null;
  reveal_steps: { step: number; cover: string | null; position: RevealPosition | string; correct: number; wrong: number }[];
  answer: string;
}

export interface ConnectPayload extends PayloadBase {
  type: 'CONNECT';
  items: { order: number; image: string }[];
  text_hint: string | null;
  item_labels: string[];
  scoring: { correct: number | null; wrong: number | null };
  answer: string;
}

export interface SteppedCluePayload extends PayloadBase {
  type: 'STEPPED_CLUE';
  clues: { step: number; text: string; correct: number | null; wrong: number | null; images: string[] }[];
  scoring_ladder_ok: boolean;
  points_missing?: boolean;
  answer: string;
}

export interface ArrangeItem {
  label: number;
  text: string;
  value: string | null;
  value_num: number | null;
  image: string | null;
}

export interface ArrangePayload extends PayloadBase {
  type: 'ARRANGE_ORDER';
  prompt: string;
  direction: 'asc' | 'desc' | null;
  timer_seconds: number | null;
  scoring: { all_correct: number; two_correct: number; one_correct: number };
  items: ArrangeItem[];
  correct_order: number[];
  order_source: 'computed' | 'human' | 'none';
}

export interface McqPayload extends PayloadBase {
  type: 'MCQ';
  prompt: string;
  options: { letter: string; text: string }[];
  correct_letter: string | null;
  letters_inferred: boolean;
  star: boolean;
}

function obj(p: unknown): Record<string, unknown> | null {
  return p && typeof p === 'object' && !Array.isArray(p) ? (p as Record<string, unknown>) : null;
}

export const isListIt = (p: unknown): p is ListItPayload => obj(p)?.type === 'LIST_IT' && Array.isArray(obj(p)?.options);
export const isImageReveal = (p: unknown): p is ImageRevealPayload =>
  obj(p)?.type === 'IMAGE_REVEAL' && Array.isArray(obj(p)?.reveal_steps);
export const isConnect = (p: unknown): p is ConnectPayload => obj(p)?.type === 'CONNECT' && Array.isArray(obj(p)?.items);
export const isStepped = (p: unknown): p is SteppedCluePayload => {
  const o = obj(p);
  return o?.type === 'STEPPED_CLUE' && Array.isArray(o.clues) && 'scoring_ladder_ok' in o;
};
export const isArrange = (p: unknown): p is ArrangePayload => {
  const o = obj(p);
  return o?.type === 'ARRANGE_ORDER' && Array.isArray(o.items) && 'correct_order' in o;
};
export const isMcq = (p: unknown): p is McqPayload => obj(p)?.type === 'MCQ' && Array.isArray(obj(p)?.options);

/** Does this payload have a structured (format-aware) view? */
export function hasStructuredView(payload: unknown): boolean {
  return isListIt(payload) || isImageReveal(payload) || isConnect(payload) || isStepped(payload)
    || isArrange(payload) || isMcq(payload);
}

export function extractionOf(payload: unknown): ExtractionInfo | null {
  const ex = obj(payload)?.extraction;
  return ex && typeof ex === 'object' ? (ex as ExtractionInfo) : null;
}

export interface FormatStyle { label: string; chip: string }

const STYLES: Record<string, FormatStyle> = {
  LIST_IT: { label: 'List It', chip: 'bg-emerald-100 border-emerald-200 text-emerald-700' },
  IMAGE_REVEAL: { label: 'Image Reveal', chip: 'bg-sky-100 border-sky-200 text-sky-700' },
  CONNECT: { label: 'Connect', chip: 'bg-blue-100 border-blue-200 text-blue-700' },
  CONNECT_4: { label: 'Connect', chip: 'bg-blue-100 border-blue-200 text-blue-700' },
  STEPPED_CLUE: { label: 'Stepped Clue', chip: 'bg-amber-100 border-amber-200 text-amber-700' },
  THREE_CLUE: { label: 'Stepped Clue', chip: 'bg-amber-100 border-amber-200 text-amber-700' },
  ARRANGE_ORDER: { label: 'Arrange', chip: 'bg-purple-100 border-purple-200 text-purple-700' },
  MCQ: { label: 'MCQ', chip: 'bg-teal-100 border-teal-200 text-teal-700' },
  BUZZER: { label: 'Buzzer', chip: 'bg-rose-50 border-rose-200 text-rose-700' },
  VIDEO_CLUE: { label: 'Video Clue', chip: 'bg-purple-50 border-purple-200 text-purple-700' },
  AUDIO_CLUE: { label: 'Audio Clue', chip: 'bg-emerald-50 border-emerald-200 text-emerald-700' },
  VISUAL_CLUE: { label: 'Visual Clue', chip: 'bg-slate-100 border-slate-200 text-slate-700' },
};

export function formatStyle(fmt: string | null | undefined): FormatStyle {
  return (fmt && STYLES[fmt]) || { label: 'Direct QA', chip: 'bg-slate-100 border-slate-200 text-slate-700' };
}

export const FORMAT_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'LIST_IT', label: 'List It' },
  { value: 'IMAGE_REVEAL', label: 'Image Reveal' },
  { value: 'CONNECT', label: 'Connect' },
  { value: 'STEPPED_CLUE', label: 'Stepped Clue (Three Clues)' },
  { value: 'ARRANGE_ORDER', label: 'Arrange the Order' },
  { value: 'MCQ', label: 'Multiple Choice' },
  { value: 'STANDARD_QA', label: 'Direct Question & Answer' },
];

const PLAY_MODES: Record<string, string> = {
  BUZZER: 'Buzzer', DIRECTED: 'Directed', PASS: 'Pass', AUDIENCE: 'Audience', TIEBREAK: 'Tie-break',
  ALL_ON_SCREEN: 'All on screen', BUFFER: 'Buffer',
};

export function playModeLabel(mode: string | null | undefined, team?: number | null): string | null {
  if (!mode) return null;
  const base = PLAY_MODES[mode] || mode;
  return mode === 'DIRECTED' && team ? `${base} · Team ${team}` : base;
}

export function confidenceTone(c: number): string {
  if (c >= 0.9) return 'bg-emerald-50 border-emerald-200 text-emerald-700';
  if (c >= 0.7) return 'bg-amber-50 border-amber-200 text-amber-700';
  return 'bg-rose-50 border-rose-200 text-rose-700';
}

/** "+20 / -15" from a correct / wrong pair (wrong is stored negative; 0 stays "0"). */
export function ladderText(correct: number | null | undefined, wrong: number | null | undefined): string {
  if (correct == null) return '';
  const w = wrong == null ? 0 : wrong;
  return `+${correct} / ${w === 0 ? '0' : w < 0 ? `−${Math.abs(w)}` : `+${w}`}`;
}
