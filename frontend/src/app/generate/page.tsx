'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowUp, Check, Lock, RefreshCw, Sparkles, Trash2, Unlock } from 'lucide-react';
import { getApiBase, resolveMediaUrl } from '../../lib/api';
import FormatCounts, { VaultCounts } from '../../components/FormatCounts';
import { FORMAT_FILTER_OPTIONS, hasStructuredView, formatStyle } from '../../lib/formatPayloads';
import StructuredFormatBody from '../../components/formats/StructuredFormatBody';

type DraftQ = {
  id: string;
  question_text: string;
  answer: string;
  topic_l1?: string | null;
  subtopic_l2?: string | null;
  difficulty?: string | null;
  image_dependent?: boolean;
  question_images?: string[];
  answer_images?: string[];
  image_refs?: string[];
  question_format?: string | null;
  format_payload?: any;
  slot?: string | null;
  play_mode?: string | null;
  topic_label?: string | null;
};

type FulfillmentSuggestion = {
  action: 'add_adjacent' | 'relax';
  count: number;
  flag?: string;
};

type Fulfillment = {
  requested: number;
  delivered: number;
  short: boolean;
  padded: number;
  off_difficulty: number;
  suggestions: FulfillmentSuggestion[];
  note: string;
};

type BriefData = {
  prompt: string;
  query: string;
  count: number;
  filters: Record<string, unknown>;
  ramp?: boolean;
  hard_bias?: string | null;
  framing?: string | null;
  source?: string;
  format_filter?: string | null;
  locality?: string | null;
  venue_type?: string | null;
  tone?: string[];
  cross_topic_padding?: boolean;
  topics_l1?: string[];
  subtopics_l2?: string[];
  include_reserve?: boolean;
  include_example?: boolean;
};

type Draft = {
  message: string;
  questions: DraftQ[];
  bank_size: number;
  brief: BriefData;
  fulfillment?: Fulfillment;
  stages?: string[];
  narrative_arc?: string | null;
};

const GREETINGS = [
  'Who are we stumping today?',
  'Ready to ruin someone\u2019s weekend?',
  'Pick a topic. Pick a victim.',
  'Quizmaster mode: engaged.',
  'Let\u2019s make the back row sweat.',
  'Name a theme. I\u2019ll find the trivia.',
  'Fastest finger first. What\u2019s the round?',
  'Somewhere, a team is about to lose on a tiebreaker.',
  'Your audience thinks they\u2019re clever. Prove them right or wrong.',
  'Bring snacks. I\u2019ll bring the questions.',
  'No Googling in the back, please. What\u2019s the quiz?',
  'Tell me the vibe. I\u2019ll bring the buzzer.',
];
const PLACEHOLDERS = [
  '12 tough questions on fintech for a college finals',
  'A gentle warm-up on brands and logos, no images',
  'Family quiz night: food, festivals and cricket, 15 questions',
  'Something fiendish on banking, for people who think they know money',
];
const STAGES = ['Reading your brief', 'Searching the archive', 'Weeding out repeats', 'Shaping the order', 'Writing the intro'];
const FALLBACK_STARTERS = [
  '10 evergreen questions on Indian startups',
  'A tough 15-question finals round on banking and money',
  'Warm-up quiz on brands and logos, no images',
];

// (format counts: see components/FormatCounts.tsx)
async function apiFetch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${getApiBase()}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Request failed (${res.status})`);
  return res.json();
}

export default function QuizzerHome() {
  const router = useRouter();
  const [greeting, setGreeting] = useState(GREETINGS[0]);
  const [prompt, setPrompt] = useState('');
  const [ph, setPh] = useState(PLACEHOLDERS[0]);
  const [starters, setStarters] = useState<string[]>(FALLBACK_STARTERS);
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [qs, setQs] = useState<DraftQ[]>([]);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [locked, setLocked] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [lastPrompt, setLastPrompt] = useState('');

  // Brief chips state
  const [editCount, setEditCount] = useState<number>(10);
  const [editDifficulty, setEditDifficulty] = useState<string>('balanced');
  const [editAudience, setEditAudience] = useState<string>('general');
  const [editLocality, setEditLocality] = useState<string>('');
  const [editFormat, setEditFormat] = useState<string>('');
  const [isEditingChips, setIsEditingChips] = useState<boolean>(false);

  // What the quiz may draw from: live questions by default; reserve spares / example slides only when ticked
  const [includeReserve, setIncludeReserve] = useState(false);
  const [includeExample, setIncludeExample] = useState(false);
  const [pickedFormat, setPickedFormat] = useState('');
  const [vault, setVault] = useState<VaultCounts | null>(null);

  // Refine box state
  const [refineText, setRefineText] = useState('');

  // Interactive topic change state
  const [cardTopicEdit, setCardTopicEdit] = useState<string | null>(null);
  const [cardTopicInput, setCardTopicInput] = useState<string>('');
  const [batchTopicInput, setBatchTopicInput] = useState<string>('');

  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- random pick must run after hydration
    setGreeting(GREETINGS[Math.floor(Math.random() * GREETINGS.length)]);
    setPh(PLACEHOLDERS[Math.floor(Math.random() * PLACEHOLDERS.length)]);
    apiFetch<{ starters: string[] }>('/prompt-quiz/starters').then((d) => setStarters(d.starters.slice(0, 4))).catch(() => {});
  }, []);

  useEffect(() => {
    apiFetch<VaultCounts>(`/prompt-quiz/format-counts?include_reserve=${includeReserve}&include_example=${includeExample}`)
      .then(setVault).catch(() => setVault(null));
  }, [includeReserve, includeExample]);

  useEffect(() => {
    if (!loading) return;
    const t = setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 700);
    return () => clearInterval(t);
  }, [loading]);

  useEffect(() => {
    const el = area.current;
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 220) + 'px'; }
  }, [prompt]);

  function syncChipsFromBrief(b: BriefData) {
    setEditCount(b.count || 10);
    setEditDifficulty(b.hard_bias || 'balanced');
    setEditAudience((b.filters?.audience as string) || 'general');
    setEditLocality(b.locality || '');
    setEditFormat(b.format_filter || '');
    setIsEditingChips(false);
  }

  function quizOptions(): Record<string, unknown> {
    return {
      include_reserve: includeReserve,
      include_example: includeExample,
      ...(pickedFormat ? { format_filter: pickedFormat } : {}),
    };
  }

  async function run(text = prompt, extraPayload?: { previous_brief?: unknown; keep_ids?: string[] }) {
    const p = text.trim();
    if (p.length < 2 || loading) return;
    setError(''); setStage(0); setLoading(true);
    try {
      const payload: Record<string, unknown> = { prompt: p, options: quizOptions(), ...extraPayload };
      const d = await apiFetch<Draft>('/prompt-quiz/draft', payload);
      setDraft(d);
      setQs(d.questions);
      setChecked(new Set(d.questions.map((q) => q.id)));
      setLocked(new Set());
      setLastPrompt(p);
      syncChipsFromBrief(d.brief);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  async function swapSingleWithTopic(id: string, slotIndex: number, targetTopic: string) {
    const t = targetTopic.trim();
    if (!t || loading || !draft) return;
    setError(''); setLoading(true); setStage(0);
    try {
      const currentQuestions = qs.map((q, idx) => ({
        index: idx + 1,
        id: q.id,
        question_text: q.question_text,
        topic: q.subtopic_l2 || q.topic_l1,
      }));
      const d = await apiFetch<Draft>('/prompt-quiz/draft', {
        prompt: `replace question ${slotIndex} with ${t}`,
        previous_brief: draft.brief,
        options: quizOptions(),
        keep_ids: Array.from(locked),
        current_questions: currentQuestions,
      });
      setDraft(d);
      setQs(d.questions);
      setChecked(new Set(d.questions.map((q) => q.id)));
      setCardTopicEdit(null);
      setCardTopicInput('');
      syncChipsFromBrief(d.brief);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Swap failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleReplaceUnselected(topicOverride?: string) {
    const t = (topicOverride !== undefined ? topicOverride : batchTopicInput).trim();
    if (!t || loading || !draft) return;
    const unselectedCount = qs.length - checked.size;
    if (unselectedCount <= 0) return;

    setError(''); setLoading(true); setStage(0);
    try {
      const keptIds = Array.from(checked);
      const excludedIds = qs.filter((q) => !checked.has(q.id)).map((q) => q.id);
      const currentQuestions = qs.map((q, idx) => ({
        index: idx + 1,
        id: q.id,
        question_text: q.question_text,
        topic: q.subtopic_l2 || q.topic_l1,
      }));

      const d = await apiFetch<Draft>('/prompt-quiz/draft', {
        prompt: `change ${unselectedCount} questions to ${t}`,
        previous_brief: draft.brief,
        keep_ids: keptIds,
        exclude_ids: excludedIds,
        current_questions: currentQuestions,
      });
      setDraft(d);
      setQs(d.questions);
      setChecked(new Set(d.questions.map((q) => q.id)));
      setBatchTopicInput('');
      syncChipsFromBrief(d.brief);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Replacement failed');
    } finally {
      setLoading(false);
    }
  }

  async function swap(id: string) {
    try {
      const nq = await apiFetch<DraftQ>('/prompt-quiz/swap', {
        prompt: lastPrompt,
        question_id: id,
        keep_ids: qs.map((q) => q.id),
      });
      setQs((cur) => cur.map((q) => (q.id === id ? nq : q)));
      setChecked((cur) => {
        const next = new Set(cur);
        next.delete(id);
        next.add(nq.id);
        return next;
      });
    } catch {
      setError('No other matching question found for that slot.');
    }
  }

  function removeQuestion(id: string) {
    setQs((c) => c.filter((x) => x.id !== id));
    setChecked((cur) => {
      const next = new Set(cur);
      next.delete(id);
      return next;
    });
  }

  async function save() {
    const selectedIds = qs.filter((q) => checked.has(q.id)).map((q) => q.id);
    if (selectedIds.length === 0) return;
    setSaving(true);
    try {
      const r = await apiFetch<{ quiz_id: string }>('/prompt-quiz/save', {
        prompt: lastPrompt,
        question_ids: selectedIds,
        brief: draft?.brief,
      });
      router.push(`/quizzes/${r.quiz_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  }

  const toggle = (set: Set<string>, id: string, fn: (s: Set<string>) => void) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id); else n.add(id);
    fn(n);
  };

  async function handleRefine(overrideText?: string) {
    const textToUse = (overrideText !== undefined ? overrideText : refineText).trim();
    if (!textToUse || loading || !draft) return;

    // Only genuinely locked questions are kept; unchecked questions are excluded
    const keptIds = Array.from(locked);
    const excludedIds = qs.filter((q) => !checked.has(q.id)).map((q) => q.id);
    const currentQuestions = qs.map((q, idx) => ({
      index: idx + 1,
      id: q.id,
      question_text: q.question_text,
      topic: q.subtopic_l2 || q.topic_l1,
    }));

    setError(''); setLoading(true); setStage(0);
    try {
      const d = await apiFetch<Draft>('/prompt-quiz/draft', {
        prompt: textToUse,
        previous_brief: draft.brief,
        keep_ids: keptIds,
        exclude_ids: excludedIds,
        current_questions: currentQuestions,
      });
      setDraft(d);
      setQs(d.questions);
      setChecked(new Set(d.questions.map((q) => q.id)));
      // Retain lock state for questions that survived in the updated list
      const newIds = new Set(d.questions.map((q) => q.id));
      setLocked(new Set(Array.from(locked).filter((id) => newIds.has(id))));
      setRefineText('');
      setLastPrompt(textToUse);
      syncChipsFromBrief(d.brief);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Refinement failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleRedoChips() {
    if (!draft || loading) return;
    const updatedBrief: Record<string, unknown> = {
      ...draft.brief,
      count: editCount,
      hard_bias: editDifficulty === 'balanced' ? null : editDifficulty,
      filters: {
        ...(draft.brief.filters || {}),
        audience: editAudience === 'general' ? null : editAudience,
      },
      locality: editLocality.trim() || null,
      format_filter: editFormat || null,
      include_reserve: includeReserve,
      include_example: includeExample,
    };
    await run(lastPrompt, { previous_brief: updatedBrief });
  }

  async function handleSuggestion(s: FulfillmentSuggestion) {
    if (!draft || loading) return;
    if (s.action === 'add_adjacent') {
      const nextBrief = { ...draft.brief, cross_topic_padding: true };
      await run(lastPrompt, { previous_brief: nextBrief });
    } else if (s.action === 'relax') {
      if (s.flag === 'format') {
        const nextBrief = { ...draft.brief, format_filter: null };
        await run(lastPrompt, { previous_brief: nextBrief });
      } else if (s.flag === 'no_images') {
        const nextFilters = { ...(draft.brief.filters || {}), no_images: false, exclude_image: false };
        const nextBrief = { ...draft.brief, filters: nextFilters };
        await run(lastPrompt, { previous_brief: nextBrief });
      } else if (s.flag === 'evergreen') {
        const nextFilters = { ...(draft.brief.filters || {}), evergreen: false, evergreen_only: false };
        const nextBrief = { ...draft.brief, filters: nextFilters };
        await run(lastPrompt, { previous_brief: nextBrief });
      }
    }
  }

  return (
    <div className="min-h-[calc(100vh-4rem)] px-5 pb-24">
      <div className={`mx-auto max-w-2xl transition-all ${draft ? 'pt-8' : 'pt-[16vh]'}`}>
        <h1 className="text-center font-serif text-[34px] sm:text-[42px] leading-tight tracking-tight text-slate-900" style={{ fontFamily: 'var(--font-fraunces), ui-serif, Georgia, Cambria, serif' }}>
          {greeting}
        </h1>

        <div className="mt-8 rounded-3xl border border-slate-200 bg-white shadow-sm focus-within:border-slate-400 focus-within:shadow-md transition">
          <textarea
            ref={area}
            value={prompt}
            rows={2}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); run(); } }}
            placeholder={ph}
            aria-label="Describe your quiz"
            className="w-full resize-none bg-transparent px-5 pt-4 text-[16px] leading-relaxed text-slate-900 placeholder:text-slate-400 focus:outline-none"
          />
          <div className="flex flex-wrap items-center gap-2 px-3 pb-3 pt-1">
            <button onClick={() => run()} disabled={loading || prompt.trim().length < 2} aria-label="Build quiz"
              className="ml-auto flex h-10 w-9 items-center justify-center rounded-full bg-slate-900 text-white transition disabled:opacity-30 hover:bg-slate-700">
              <ArrowUp size={18} />
            </button>
          </div>
        </div>

        <FormatCounts
          data={vault}
          includeReserve={includeReserve}
          includeExample={includeExample}
          onToggleReserve={setIncludeReserve}
          onToggleExample={setIncludeExample}
          picked={pickedFormat}
          onPick={setPickedFormat}
        />

        {!draft && !loading && (
          <ul className="mt-6 space-y-1 text-center">
            {starters.map((s) => (
              <li key={s}>
                <button onClick={() => { setPrompt(s); run(s); }} className="text-[14px] text-slate-500 underline-offset-4 hover:text-slate-900 hover:underline">
                  {s}
                </button>
              </li>
            ))}
          </ul>
        )}

        {loading && (
          <div className="mt-8 text-center text-[14px] text-slate-500" role="status" aria-live="polite">
            <span className="inline-block animate-pulse">{STAGES[stage]}…</span>
          </div>
        )}
        {error && <p className="mt-6 text-center text-[14px] text-red-600" role="alert">{error}</p>}

        {/* Brief chips + Framing sentence / Short note */}
        {draft && (
          <div className="mt-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
            {(draft.fulfillment?.short ? draft.fulfillment.note : draft.brief.framing) && (
              <p className="text-center text-[14px] font-medium text-slate-700">
                {draft.fulfillment?.short
                  ? draft.fulfillment.note
                  : `“${draft.brief.framing}”`}
              </p>
            )}
            
            <div className="mt-3 flex flex-wrap items-center justify-center gap-2 text-[12px]">
              {!isEditingChips ? (
                <>
                  <span className="rounded-full bg-white px-3 py-1 font-medium text-slate-700 border border-slate-200">
                    {draft.brief.count} questions
                  </span>
                  <span className="rounded-full bg-white px-3 py-1 font-medium text-slate-700 border border-slate-200">
                    Difficulty: {draft.brief.hard_bias || 'Balanced'}
                  </span>
                  <span className="rounded-full bg-white px-3 py-1 font-medium text-slate-700 border border-slate-200">
                    Audience: {((draft.brief.filters?.audience as string) || 'General')}
                  </span>
                  {draft.brief.locality && (
                    <span className="rounded-full bg-white px-3 py-1 font-medium text-slate-700 border border-slate-200">
                      Location: {draft.brief.locality}
                    </span>
                  )}
                  {draft.brief.format_filter && (
                    <span className="rounded-full bg-white px-3 py-1 font-medium text-slate-700 border border-slate-200">
                      Format: {draft.brief.format_filter}
                    </span>
                  )}
                  <button
                    onClick={() => setIsEditingChips(true)}
                    className="ml-1 text-[12px] text-slate-500 hover:text-slate-900 underline underline-offset-2"
                  >
                    Edit
                  </button>
                </>
              ) : (
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <label className="flex items-center gap-1 text-slate-600">
                    Count:
                    <input
                      type="number"
                      min={1}
                      max={50}
                      value={editCount}
                      onChange={(e) => setEditCount(Math.max(1, Math.min(50, Number(e.target.value) || 10)))}
                      className="w-14 rounded border border-slate-300 bg-white px-2 py-0.5 text-[12px] text-slate-900"
                    />
                  </label>
                  <label className="flex items-center gap-1 text-slate-600">
                    Difficulty:
                    <select
                      value={editDifficulty}
                      onChange={(e) => setEditDifficulty(e.target.value)}
                      className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[12px] text-slate-900"
                    >
                      <option value="balanced">Balanced</option>
                      <option value="easy">Easy</option>
                      <option value="medium">Medium</option>
                      <option value="hard">Hard</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-1 text-slate-600">
                    Audience:
                    <select
                      value={editAudience}
                      onChange={(e) => setEditAudience(e.target.value)}
                      className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[12px] text-slate-900"
                    >
                      <option value="general">General</option>
                      <option value="school">School</option>
                      <option value="college">College</option>
                      <option value="corporate">Corporate</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-1 text-slate-600">
                    Location:
                    <input
                      type="text"
                      placeholder="e.g. Bengaluru"
                      value={editLocality}
                      onChange={(e) => setEditLocality(e.target.value)}
                      className="w-24 rounded border border-slate-300 bg-white px-2 py-0.5 text-[12px] text-slate-900"
                    />
                  </label>
                  <label className="flex items-center gap-1 text-slate-600">
                    Format:
                    <select
                      value={editFormat}
                      onChange={(e) => setEditFormat(e.target.value)}
                      className="rounded border border-slate-300 bg-white px-2 py-0.5 text-[12px] text-slate-900"
                    >
                      <option value="">Any</option>
                      {FORMAT_FILTER_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    onClick={handleRedoChips}
                    disabled={loading}
                    className="rounded-full bg-slate-900 px-3 py-1 text-[12px] font-medium text-white transition hover:bg-slate-700 disabled:opacity-40"
                  >
                    Redo
                  </button>
                  <button
                    onClick={() => setIsEditingChips(false)}
                    className="text-[12px] text-slate-500 hover:text-slate-900"
                  >
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {draft && (
        <section className="mx-auto mt-10 max-w-2xl">
          {/* Fulfillment Banner */}
          {draft.fulfillment?.short && (
            <div className="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <p className="text-[14px] text-slate-700">{draft.fulfillment.note}</p>
              {draft.fulfillment.suggestions && draft.fulfillment.suggestions.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {draft.fulfillment.suggestions
                    .filter((s) => s.flag !== 'school_safe')
                    .map((s, idx) => {
                      let label = '';
                      if (s.action === 'add_adjacent') {
                        label = `Add adjacent questions (+${s.count})`;
                      } else if (s.flag === 'format') {
                        label = `Allow any format (+${s.count})`;
                      } else if (s.flag === 'no_images') {
                        label = `Allow images (+${s.count})`;
                      } else if (s.flag === 'evergreen') {
                        label = `Allow time-sensitive (+${s.count})`;
                      } else {
                        label = `Relax ${s.flag} (+${s.count})`;
                      }
                      return (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => handleSuggestion(s)}
                          disabled={loading}
                          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-700 transition hover:bg-slate-100 hover:text-slate-900 disabled:opacity-40"
                        >
                          {label}
                        </button>
                      );
                    })}
                </div>
              )}
            </div>
          )}

          <p className="text-[15px] text-slate-600">{draft.message}</p>

          {draft.narrative_arc && (
            <div className="mt-3 flex items-start gap-2.5 rounded-xl border border-purple-200 bg-purple-50/70 p-3 text-[13px] text-purple-950 shadow-xs">
              <Sparkles size={16} className="mt-0.5 shrink-0 text-purple-600" />
              <div>
                <span className="font-semibold text-purple-900">Curated Round Arc:</span>{' '}
                <span className="text-purple-800">{draft.narrative_arc}</span>
              </div>
            </div>
          )}

          {/* Selection header */}
          <div className="mt-4 flex items-center justify-between border-b border-slate-200 pb-2 text-[13px] text-slate-600">
            <span>{checked.size} of {qs.length} selected</span>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setChecked(new Set(qs.map((q) => q.id)))}
                className="text-slate-600 hover:text-slate-900 underline-offset-2 hover:underline cursor-pointer"
              >
                Select all
              </button>
              <button
                type="button"
                onClick={() => setChecked(new Set())}
                className="text-slate-600 hover:text-slate-900 underline-offset-2 hover:underline cursor-pointer"
              >
                Clear
              </button>
            </div>
          </div>

          {/* Interactive Batch Replace Bar for Unchecked Questions */}
          {checked.size < qs.length && (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/90 p-3 text-slate-900 shadow-xs">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
                <div>
                  <p className="text-[13px] font-semibold text-amber-950">
                    {qs.length - checked.size} of {qs.length} questions unselected
                  </p>
                  <p className="text-[12px] text-amber-800">
                    Replace the {qs.length - checked.size} unchecked questions while keeping the {checked.size} selected questions:
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {['Technology', 'Cricket', 'Startups', 'Food', 'Finance'].map((t) => (
                    <button
                      key={t}
                      type="button"
                      disabled={loading}
                      onClick={() => handleReplaceUnselected(t)}
                      className="rounded-md border border-amber-300 bg-white px-2 py-0.5 text-[11px] font-semibold text-amber-900 hover:bg-amber-100 transition cursor-pointer disabled:opacity-40"
                    >
                      + {t}
                    </button>
                  ))}
                </div>
              </div>
              <div className="mt-2.5 flex gap-2">
                <input
                  type="text"
                  placeholder="Or type a replacement topic (e.g. Indian Brands, World History)..."
                  value={batchTopicInput}
                  onChange={(e) => setBatchTopicInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleReplaceUnselected(); }}
                  className="flex-1 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[13px] text-slate-900 placeholder:text-slate-400 focus:border-amber-500 focus:outline-none"
                />
                <button
                  type="button"
                  disabled={!batchTopicInput.trim() || loading}
                  onClick={() => handleReplaceUnselected()}
                  className="rounded-lg bg-amber-600 hover:bg-amber-700 px-3.5 py-1.5 text-[12px] font-semibold text-white transition disabled:opacity-40 cursor-pointer"
                >
                  Replace {qs.length - checked.size}
                </button>
              </div>
            </div>
          )}

          <ol className="mt-2 divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white overflow-hidden shadow-xs">
            {qs.map((q, i) => (
              <li
                key={q.id}
                className={`group px-5 py-4 transition-all duration-150 ${
                  locked.has(q.id)
                    ? 'bg-amber-50/30 border-l-4 border-l-amber-500'
                    : checked.has(q.id) ? 'bg-white' : 'bg-slate-50/70 opacity-45'
                }`}
              >
                <div className="flex gap-3.5 items-start">
                  <label className="flex shrink-0 cursor-pointer items-center gap-2 pt-0.5 select-none" title={checked.has(q.id) ? "Click to exclude" : "Click to include"}>
                    <input
                      type="checkbox"
                      checked={checked.has(q.id)}
                      onChange={() => toggle(checked, q.id, setChecked)}
                      aria-label={`Include question ${i + 1}`}
                      className="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-500 accent-slate-900 cursor-pointer"
                    />
                    <span className="w-5 text-right text-[13px] font-medium tabular-nums text-slate-400 group-hover:text-slate-600 transition">
                      {i + 1}
                    </span>
                  </label>
                  <div className="min-w-0 flex-1">
                    {/* Format-aware questions (List It, Image Reveal, Connect, Stepped Clue, Arrange, MCQ) show their own view */}
                    {hasStructuredView(q.format_payload) ? (
                      <div className="space-y-1.5">
                        <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
                          <span className={`rounded-full border px-2 py-0.5 ${formatStyle(q.question_format).chip}`}>{formatStyle(q.question_format).label}</span>
                          {q.slot && q.slot !== 'LIVE' && <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-amber-800">{q.slot === 'RESERVE' ? 'Reserve' : 'Example'}</span>}
                        </div>
                        <StructuredFormatBody
                          q={{ id: q.id, format_payload: q.format_payload, question_videos: [] }}
                          getImageSrc={(u) => resolveMediaUrl(u)}
                          handleImgError={() => {}}
                          setPreviewImage={(u) => window.open(resolveMediaUrl(u), '_blank', 'noopener')}
                          readOnly
                        />
                      </div>
                    ) : q.question_format === 'CONNECT_4' && q.format_payload ? (
                      <div className="space-y-3">
                        <div className="flex items-center gap-2">
                          <span className="inline-flex items-center gap-1 rounded-md bg-blue-100 text-blue-800 text-[11px] font-bold px-2 py-0.5 uppercase tracking-wide">
                            Connect 4 • Common Link
                          </span>
                          {q.format_payload.sub_hint && (
                            <span className="text-[12px] text-slate-500 font-medium italic">
                              Hint: {q.format_payload.sub_hint}
                            </span>
                          )}
                        </div>
                        <p className="text-[15px] font-semibold text-slate-900 leading-snug">
                          {q.format_payload.prompt || "What common thread or concept connects all four clues and images?"}
                        </p>
                        {/* 4 Clue Tiles */}
                        {q.format_payload.elements && Array.isArray(q.format_payload.elements) && q.format_payload.elements.length > 0 && (
                          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-1">
                            {q.format_payload.elements.map((elem: any, elIdx: number) => {
                              const imgUrl = elem.image || elem.image_url;
                              return (
                                <div
                                  key={elIdx}
                                  className="relative rounded-xl overflow-hidden border border-blue-200 bg-blue-50/40 p-2 flex flex-col items-center text-center group hover:border-blue-400 transition"
                                >
                                  <span className="self-start rounded bg-blue-600 text-white text-[10px] font-bold px-1.5 py-0.5 uppercase mb-1.5">
                                    {elem.label || `Clue ${elIdx + 1}`}
                                  </span>
                                  {imgUrl ? (
                                    <img
                                      src={resolveMediaUrl(imgUrl)}
                                      alt={elem.label || `Clue ${elIdx + 1}`}
                                      className="w-full h-24 object-contain rounded-lg bg-white border border-blue-100 mb-1.5"
                                      loading="lazy"
                                    />
                                  ) : (
                                    <div className="w-full h-24 flex items-center justify-center rounded-lg bg-white border border-blue-100 text-slate-400 text-[12px] mb-1.5">
                                      Text Clue
                                    </div>
                                  )}
                                  {elem.text && elem.text !== elem.label && (
                                    <p className="text-[11.5px] text-slate-700 font-medium line-clamp-2">{elem.text}</p>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    ) : q.question_format === 'STEPPED_CLUE' && q.format_payload ? (
                      /* Specialized Stepped Clue Rendering */
                      <div className="space-y-2.5">
                        <div className="flex items-center gap-2">
                          <span className="inline-flex items-center gap-1 rounded-md bg-amber-100 text-amber-900 text-[11px] font-bold px-2 py-0.5 uppercase tracking-wide">
                            Portfolio Builder • 3 Stepped Clues
                          </span>
                          <span className="text-[11px] text-slate-400">All on buzzer · 1 attempt per clue</span>
                        </div>
                        {/* 3 Progressive Tiers */}
                        <div className="space-y-2 pt-0.5">
                          {((q.format_payload.clues && Array.isArray(q.format_payload.clues)) ? q.format_payload.clues : []).map((clue: any, cIdx: number) => {
                            const borderStyle = cIdx === 0
                              ? 'border-amber-300 bg-amber-50/60'
                              : cIdx === 1
                              ? 'border-orange-200 bg-orange-50/40'
                              : 'border-emerald-200 bg-emerald-50/40';
                            const badgeStyle = cIdx === 0
                              ? 'bg-amber-600 text-white'
                              : cIdx === 1
                              ? 'bg-orange-600 text-white'
                              : 'bg-emerald-600 text-white';
                            const imgUrl = clue.image || clue.image_url;
                            return (
                              <div key={cIdx} className={`flex items-start gap-3 rounded-xl border p-2.5 ${borderStyle}`}>
                                <span className={`shrink-0 rounded-lg px-2 py-1 text-[11px] font-bold font-mono tracking-tight ${badgeStyle}`}>
                                  {clue.points || (cIdx === 0 ? '+20 / -15' : cIdx === 1 ? '+15 / -10' : '+10 / -5')}
                                </span>
                                <div className="min-w-0 flex-1">
                                  <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 block mb-0.5">
                                    {clue.difficulty_label || (cIdx === 0 ? 'Hard Clue' : cIdx === 1 ? 'Medium Clue' : 'Giveaway Clue')}
                                  </span>
                                  <p className="text-[13.5px] text-slate-800 leading-snug">{clue.text}</p>
                                </div>
                                {imgUrl && (
                                  <img
                                    src={resolveMediaUrl(imgUrl)}
                                    alt={`Step ${cIdx + 1} Visual`}
                                    className="h-14 w-20 shrink-0 rounded-lg object-contain bg-white border border-slate-200"
                                    loading="lazy"
                                  />
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    ) : (
                      /* Standard Question Rendering */
                      <>
                        <p className="whitespace-pre-line text-[15px] leading-relaxed text-slate-900">{q.question_text}</p>
                        
                        {/* Visual Clue Preview */}
                        {((q.question_images && q.question_images.length > 0) || (q.image_refs && q.image_refs.length > 0)) && (
                          <div className="mt-2.5 flex items-center gap-2.5">
                            <img
                              src={resolveMediaUrl((q.question_images && q.question_images[0]) || (q.image_refs && q.image_refs[0]))}
                              alt="Question Clue"
                              className="h-16 w-24 rounded-lg object-contain bg-slate-50 border border-slate-200 shadow-xs"
                              loading="lazy"
                            />
                            <span className="text-[11px] font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-md">
                              Visual Clue
                            </span>
                          </div>
                        )}
                      </>
                    )}

                    {/* Answer shown with Format-Specific Reveal Badge */}
                    {q.answer && (
                      <div className="mt-3 flex items-center gap-2">
                        <span className={`text-[11px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-md ${
                          q.question_format === 'CONNECT_4'
                            ? 'bg-blue-100 text-blue-800'
                            : q.question_format === 'STEPPED_CLUE'
                            ? 'bg-amber-100 text-amber-900'
                            : 'bg-emerald-100 text-emerald-800'
                        }`}>
                          {q.question_format === 'CONNECT_4' ? 'Common Link' : 'Answer'}
                        </span>
                        <p className="text-[14.5px] font-semibold text-slate-900">{q.answer}</p>
                        {q.answer_images && q.answer_images.length > 0 && (
                          <div className="ml-2 flex items-center gap-2">
                            <img
                              src={resolveMediaUrl(q.answer_images[0])}
                              alt="Answer Diagram"
                              className="h-14 w-20 rounded-lg object-contain bg-emerald-50/50 border border-emerald-200 shadow-xs"
                              loading="lazy"
                            />
                          </div>
                        )}
                      </div>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      {locked.has(q.id) && (
                        <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-semibold text-amber-800">
                          <Lock size={11} /> Pinned
                        </span>
                      )}
                      <p className="text-[12px] text-slate-400">{[q.subtopic_l2 || q.topic_l1, q.difficulty !== 'Unrated' ? q.difficulty : null, q.image_dependent ? 'has image' : null].filter(Boolean).join(' · ')}</p>
                    </div>

                    {/* Inline Topic Change Panel for this Card */}
                    {cardTopicEdit === q.id && (
                      <div className="mt-3.5 rounded-xl border border-indigo-100 bg-indigo-50/60 p-3">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[12px] font-semibold text-indigo-900 flex items-center gap-1.5">
                            <Sparkles size={13} className="text-indigo-600" />
                            Change Question {i + 1} to a different topic:
                          </span>
                          <button
                            type="button"
                            onClick={() => setCardTopicEdit(null)}
                            className="text-[11px] text-slate-400 hover:text-slate-600 cursor-pointer"
                          >
                            Cancel
                          </button>
                        </div>
                        <div className="flex flex-wrap gap-1.5 mb-2.5">
                          {['Technology', 'Cricket', 'Indian Startups', 'Food & Culture', 'Finance & Banking', 'World Geography'].map((t) => (
                            <button
                              key={t}
                              type="button"
                              disabled={loading}
                              onClick={() => swapSingleWithTopic(q.id, i + 1, t)}
                              className="rounded-md border border-indigo-200 bg-white px-2 py-0.5 text-[11px] font-medium text-indigo-800 hover:bg-indigo-100 transition cursor-pointer disabled:opacity-40"
                            >
                              {t}
                            </button>
                          ))}
                        </div>
                        <div className="flex gap-2">
                          <input
                            type="text"
                            placeholder="Or type any topic (e.g. Space, Automobile, Movies)..."
                            value={cardTopicInput}
                            onChange={(e) => setCardTopicInput(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') swapSingleWithTopic(q.id, i + 1, cardTopicInput); }}
                            className="flex-1 rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-[13px] text-slate-900 placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none"
                          />
                          <button
                            type="button"
                            disabled={!cardTopicInput.trim() || loading}
                            onClick={() => swapSingleWithTopic(q.id, i + 1, cardTopicInput)}
                            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-indigo-700 transition disabled:opacity-40 cursor-pointer"
                          >
                            Change
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-start gap-1 text-slate-400 opacity-60 transition group-hover:opacity-100 focus-within:opacity-100">
                    <button
                      aria-label="Change topic for this question"
                      disabled={locked.has(q.id)}
                      onClick={() => {
                        setCardTopicEdit(cardTopicEdit === q.id ? null : q.id);
                        setCardTopicInput('');
                      }}
                      className={`p-1.5 transition rounded ${cardTopicEdit === q.id ? 'bg-indigo-100 text-indigo-700' : 'hover:text-indigo-600 disabled:opacity-30'}`}
                      title="Change topic for this question"
                    >
                      <Sparkles size={16} />
                    </button>
                    <button aria-label={locked.has(q.id) ? 'Unlock' : 'Lock'} onClick={() => toggle(locked, q.id, setLocked)} className={`p-1.5 transition ${locked.has(q.id) ? 'text-amber-600 hover:text-amber-700' : 'hover:text-slate-900'}`} title={locked.has(q.id) ? 'Unlock question' : 'Lock question'}>{locked.has(q.id) ? <Lock size={16} /> : <Unlock size={16} />}</button>
                    <button aria-label="Swap for another" disabled={locked.has(q.id)} onClick={() => swap(q.id)} className="p-1.5 hover:text-slate-900 disabled:opacity-30" title="Swap with another random question"><RefreshCw size={16} /></button>
                    <button aria-label="Remove" disabled={locked.has(q.id)} onClick={() => removeQuestion(q.id)} className="p-1.5 hover:text-red-600 disabled:opacity-30" title="Remove question"><Trash2 size={16} /></button>
                  </div>
                </div>
              </li>
            ))}
          </ol>
          {qs.length === 0 && <p className="mt-4 text-[14px] text-slate-500">Nothing left. Try a broader prompt.</p>}

          {/* Refine Box & Quick Suggestions */}
          <div className="mt-6">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[12px] font-medium text-slate-500">Quick Adjustments:</span>
              {locked.size > 0 && (
                <span className="text-[11px] font-medium text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Lock size={11} /> {locked.size} {locked.size === 1 ? 'question' : 'questions'} pinned
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5 mb-2.5">
              {[
                { label: '🔥 Make it harder', prompt: 'Make it harder' },
                { label: '🌱 School friendly', prompt: 'Make it for middle school kids' },
                { label: '🖼️ Only with images', prompt: 'Only questions with images' },
                { label: '📝 Text only', prompt: 'Text only, no images' },
                { label: '➕ Add 5 questions', prompt: 'Give me 5 more questions' },
                { label: '🇮🇳 India focus', prompt: 'Focus on India' },
              ].map((pill, idx) => (
                <button
                  key={idx}
                  type="button"
                  disabled={loading}
                  onClick={() => {
                    setRefineText(pill.prompt);
                    handleRefine(pill.prompt);
                  }}
                  className="rounded-lg border border-slate-200 bg-slate-50 hover:bg-slate-100 hover:border-slate-300 px-2.5 py-1 text-[12px] text-slate-700 transition disabled:opacity-40 cursor-pointer"
                >
                  {pill.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={refineText}
                onChange={(e) => setRefineText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleRefine(); }}
                placeholder="Adjust this quiz… (e.g. make it harder, add 1 question based on Bengaluru)"
                className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[14px] text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none"
              />
              <button
                onClick={() => handleRefine()}
                disabled={loading || !refineText.trim()}
                className="rounded-xl bg-slate-900 px-5 py-2.5 text-[14px] font-medium text-white transition hover:bg-slate-700 disabled:opacity-40 cursor-pointer"
              >
                Refine
              </button>
            </div>
          </div>

          <div className="mt-6 flex items-center gap-4">
            <button onClick={save} disabled={saving || checked.size === 0}
              className="inline-flex items-center gap-2 rounded-full bg-slate-900 px-5 py-2.5 text-[14px] font-medium text-white transition hover:bg-slate-700 disabled:opacity-40">
              <Check size={16} /> {saving ? 'Saving…' : `Save ${checked.size} questions`}
            </button>
            <button onClick={() => run(lastPrompt)} className="text-[14px] text-slate-500 hover:text-slate-900">Start over</button>
          </div>
        </section>
      )}

    </div>
  );
}
