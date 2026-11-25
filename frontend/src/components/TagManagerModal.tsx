'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  X, Tag, Plus, Check, Loader2, AlertCircle, Star, RefreshCw, Trash2, Pencil, ExternalLink, Layers,
} from 'lucide-react';
import {
  HistoricalQuestion, QuestionDetail, TaxonomyV6, TopicPath, RepeatEntry, EditHistoryItem,
} from '../lib/types';
import { api, resolveMediaUrl } from '../lib/api';

const getImageSrc = (u: string) => (u ? resolveMediaUrl(u) : '');

interface Props {
  isOpen: boolean;
  onClose: () => void;
  question: HistoricalQuestion | null;
  onSaved: (updated: HistoricalQuestion) => void;
  /** Show only questions from this deck in the list behind the modal. */
  onFilterDeck?: (documentId: string) => void;
  availableTopics?: string[]; // kept for compatibility; the topic list now comes from the taxonomy
}

type EditablePath = TopicPath & { _ct?: boolean; _cs?: boolean };
type TabKey = 'topics' | 'quiz' | 'question' | 'source' | 'repeated' | 'history';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'topics', label: 'Topics' },
  { key: 'quiz', label: 'Quiz' },
  { key: 'question', label: 'Question' },
  { key: 'source', label: 'Source' },
  { key: 'repeated', label: 'Repeated' },
  { key: 'history', label: 'History' },
];

const FORMAT_LABELS: Record<string, string> = {
  QA: 'Question and Answer (Direct)',
  CONNECT_4: 'Connect 4',
  CONNECT: 'Connect 4',
  STEPPED_CLUE: '3 Clue',
  THREE_CLUE: '3 Clue',
  ARRANGE_ORDER: 'Arrange the Order (Ticker Tangle)',
  BUZZER: 'Buzzer (Closing Bell / Rapid Fire)',
  VISUAL_CLUE: 'Text + Image',
  AUDIO_CLUE: 'Text + Audio',
  VIDEO_CLUE: 'Text + Video',
  POTPOURRI: 'Potpourri',
  IMAGE_REVEAL: 'Image Reveal',
  LIST_IT: 'List It',
};

const CANONICAL_FORMAT_OPTIONS = [
  'QA',
  'CONNECT_4',
  'STEPPED_CLUE',
  'ARRANGE_ORDER',
  'BUZZER',
  'VISUAL_CLUE',
  'AUDIO_CLUE',
  'VIDEO_CLUE',
  'POTPOURRI',
  'IMAGE_REVEAL',
  'LIST_IT',
];
const ANSWER_TYPE_LABELS: Record<string, string> = {
  TEXT: 'Text',
  TEXT_IMAGE: 'Text + image',
  IMAGE: 'Image',
  TEXT_VIDEO: 'Text + video',
  VIDEO: 'Video',
  TEXT_AUDIO: 'Text + audio',
  AUDIO: 'Audio',
};
const GEO_LABELS: Record<string, string> = { IN: 'India', INTL: 'International', NA: 'Not applicable' };
const ROUND_STAGES = ['Prelims', 'Finals', 'Semi-finals', 'Quarter-finals', 'Qualifiers', 'Wildcard'];
const LANGUAGES = ['English', 'Hindi', 'Kannada', 'Tamil', 'Telugu', 'Malayalam', 'Marathi', 'Bengali', 'Gujarati', 'Punjabi', 'Bilingual'];
const DIFFICULTIES = ['Easy', 'Medium', 'Hard'];

const inputCls =
  'w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] text-slate-800 placeholder-slate-400 focus:outline-none';
const inputCompactCls =
  'w-full rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-500';
const labelCls = 'block text-[11.5px] font-bold uppercase tracking-wider text-slate-600 mb-1';

function Field({ label, hint, children, compact = false }: { label: string; hint?: string; children: React.ReactNode; compact?: boolean }) {
  return (
    <div>
      <label className={compact ? 'block text-[10.5px] font-bold uppercase tracking-wider text-slate-500 mb-0.5' : labelCls}>{label}</label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-slate-400">{hint}</p>}
    </div>
  );
}

function Toggle({ value, onChange, yes = 'Yes', no = 'No' }: { value: boolean; onChange: (v: boolean) => void; yes?: string; no?: string }) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 overflow-hidden text-[12.5px] font-semibold">
      <button
        type="button"
        onClick={() => onChange(true)}
        className={`px-3.5 py-1.5 cursor-pointer ${value ? 'bg-indigo-600 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
      >
        {yes}
      </button>
      <button
        type="button"
        onClick={() => onChange(false)}
        className={`px-3.5 py-1.5 border-l border-slate-200 cursor-pointer ${!value ? 'bg-slate-700 text-white' : 'bg-white text-slate-600 hover:bg-slate-50'}`}
      >
        {no}
      </button>
    </div>
  );
}

function ChipInput({
  values, onChange, placeholder, prefix = '', compact = false,
}: { values: string[]; onChange: (v: string[]) => void; placeholder: string; prefix?: string; compact?: boolean }) {
  const [text, setText] = useState('');
  const add = () => {
    const clean = text.trim().replace(/^#+/, '').trim();
    if (clean && !values.some((v) => v.toLowerCase() === clean.toLowerCase())) onChange([...values, clean]);
    setText('');
  };

  if (compact) {
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {values.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded bg-indigo-50 border border-indigo-200/80 px-1.5 py-0.5 text-[11px] font-medium text-indigo-700">
            {prefix}{v}
            <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} className="text-indigo-400 hover:text-indigo-800 cursor-pointer" title={`Remove ${v}`}>
              <X className="h-2.5 w-2.5" />
            </button>
          </span>
        ))}
        <div className="inline-flex items-center gap-1">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); } }}
            placeholder={placeholder}
            className="w-40 sm:w-48 rounded border border-slate-200 bg-white px-2 py-1 text-[11.5px] text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <button
            type="button"
            onClick={add}
            disabled={!text.trim()}
            className="inline-flex items-center gap-0.5 rounded border border-slate-200 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40 cursor-pointer shrink-0"
          >
            <Plus className="h-3 w-3" /> Add
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      {values.length > 0 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {values.map((v) => (
            <span key={v} className="inline-flex items-center gap-1 rounded-md bg-indigo-50 border border-indigo-200/80 px-2 py-0.5 text-[12px] font-medium text-indigo-700">
              {prefix}{v}
              <button type="button" onClick={() => onChange(values.filter((x) => x !== v))} className="text-indigo-400 hover:text-indigo-800 cursor-pointer" title={`Remove ${v}`}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); } }}
          placeholder={placeholder}
          className={inputCls}
        />
        <button type="button" onClick={add} disabled={!text.trim()} className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 text-[12px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-40 cursor-pointer shrink-0">
          <Plus className="h-3.5 w-3.5" /> Add
        </button>
      </div>
    </div>
  );
}

const toInt = (v: any): number | null => {
  if (v === '' || v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : null;
};

export default function TagManagerModal({ isOpen, onClose, question, onSaved, onFilterDeck }: Props) {
  const [detail, setDetail] = useState<QuestionDetail | null>(null);
  const [taxonomy, setTaxonomy] = useState<TaxonomyV6 | null>(null);
  const [tab, setTab] = useState<TabKey>('topics');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // editable copies
  const [paths, setPaths] = useState<EditablePath[]>([]);
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [meta, setMeta] = useState<Record<string, any>>({});
  const [difficulty, setDifficulty] = useState('Medium');
  const [visualClues, setVisualClues] = useState('');
  const [applyToDeck, setApplyToDeck] = useState(false);

  const [history, setHistory] = useState<EditHistoryItem[] | null>(null);
  const [retagging, setRetagging] = useState(false);
  const [retagForce, setRetagForce] = useState(false);
  const [editingRepeat, setEditingRepeat] = useState<string | null>(null);
  const [repeatDraft, setRepeatDraft] = useState<Record<string, any>>({});
  const [removingRepeat, setRemovingRepeat] = useState<string | null>(null);

  const hydrate = useCallback((d: QuestionDetail) => {
    setDetail(d);
    setPaths(d.topic_paths.map((p) => ({ ...p, keywords: [...(p.keywords || [])] })));
    setHashtags([...(d.hashtags || [])]);
    setMeta({ ...d.meta });
    setDifficulty(d.difficulty || 'Medium');
    setVisualClues(d.visual_clues || '');
  }, []);

  useEffect(() => {
    if (!isOpen || !question) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setTab('topics');
    setHistory(null);
    setApplyToDeck(false);
    setEditingRepeat(null);
    Promise.all([api.getQuestionDetail(question.id), taxonomy ? Promise.resolve(taxonomy) : api.getTaxonomyV6()])
      .then(([d, t]) => {
        if (cancelled) return;
        hydrate(d);
        setTaxonomy(t);
      })
      .catch((e: any) => !cancelled && setError(e?.message || 'Could not load this question\'s tags.'))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, question?.id]);

  useEffect(() => {
    if (tab === 'history' && history === null && question) {
      api.getQuestionHistory(question.id).then(setHistory).catch(() => setHistory([]));
    }
  }, [tab, history, question]);

  const themes = taxonomy?.themes ?? [];
  const themeNames = useMemo(() => themes.map((t) => t.name), [themes]);
  const setMetaField = (k: string, v: any) => setMeta((m) => ({ ...m, [k]: v }));

  // ---- topic path editing
  const updatePath = (i: number, patch: Partial<EditablePath>) =>
    setPaths((ps) => ps.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
  const addPath = () => setPaths((ps) => (ps.length >= 6 ? ps : [...ps, { l1: '', l2: '', keywords: [] }]));
  const removePath = (i: number) => setPaths((ps) => ps.filter((_, idx) => idx !== i));

  const cleanPaths = (): TopicPath[] =>
    paths
      .filter((p) => (p.l1 || '').trim() || (p.l2 || '').trim())
      .map((p) => ({ l1: (p.l1 || '').trim(), l2: (p.l2 || '').trim() || null, keywords: p.keywords }));

  const pathsChanged = () => {
    if (!detail) return false;
    const norm = (ps: { l1: string; l2?: string | null; keywords: string[] }[]) =>
      JSON.stringify(ps.map((p) => [p.l1, p.l2 || '', p.keywords]));
    return norm(cleanPaths()) !== norm(detail.topic_paths);
  };

  // ---- diffs
  const QUIZ_FIELDS: { meta: string; deck: string }[] = [
    { meta: 'quiz_name', deck: 'quiz_name' },
    { meta: 'college_institution', deck: 'college' },
    { meta: 'city_name', deck: 'city' },
    { meta: 'round_stage', deck: 'round_stage' },
    { meta: 'deck_year', deck: 'deck_year' },
    { meta: 'language', deck: 'language' },
    { meta: 'primary_audience', deck: 'primary_audience' },
    { meta: 'is_varsity', deck: 'is_varsity' },
  ];
  const QUESTION_FIELDS = [
    'question_number', 'round_number', 'question_in_round', 'question_format', 'star_marked', 'answer_type',
    'secondary_audience', 'relevant_year', 'geo_region', 'india_international',
  ];
  const INT_KEYS = new Set(['deck_year', 'question_number', 'round_number', 'question_in_round', 'relevant_year']);

  const normVal = (k: string, v: any) => {
    if (INT_KEYS.has(k)) return toInt(v);
    if (typeof v === 'boolean') return v;
    const s = (v ?? '').toString().trim();
    return s === '' ? null : s;
  };
  const diffOf = (keys: string[]) => {
    const out: Record<string, any> = {};
    if (!detail) return out;
    for (const k of keys) {
      const before = normVal(k, (detail.meta as any)[k]);
      const after = normVal(k, meta[k]);
      if (before !== after) out[k] = after;
    }
    return out;
  };

  const hasChanges = () => {
    if (!detail) return false;
    if (pathsChanged()) return true;
    if (JSON.stringify(hashtags) !== JSON.stringify(detail.hashtags)) return true;
    if (Object.keys(diffOf(QUIZ_FIELDS.map((f) => f.meta))).length) return true;
    if (Object.keys(diffOf(QUESTION_FIELDS)).length) return true;
    if (difficulty !== (detail.difficulty || 'Medium')) return true;
    if ((visualClues || '') !== (detail.visual_clues || '')) return true;
    return false;
  };

  const handleSave = async () => {
    if (!detail || !question) return;
    setSaving(true);
    setError(null);
    try {
      if (pathsChanged()) await api.updateTopicPaths(question.id, cleanPaths());
      if (JSON.stringify(hashtags) !== JSON.stringify(detail.hashtags)) await api.updateHashtags(question.id, hashtags);

      const quizDiff = diffOf(QUIZ_FIELDS.map((f) => f.meta));
      if (Object.keys(quizDiff).length) {
        if (applyToDeck) {
          const deckChanges: Record<string, any> = {};
          for (const f of QUIZ_FIELDS) if (f.meta in quizDiff) deckChanges[f.deck] = quizDiff[f.meta];
          await api.updateDeckMeta(detail.document.id, deckChanges);
          // A hand edit made earlier on this question keeps its value through a deck edit; set it explicitly.
          const fresh = await api.getQuestionDetail(question.id);
          const stillOff: Record<string, any> = {};
          for (const k of Object.keys(quizDiff)) if (normVal(k, (fresh.meta as any)[k]) !== quizDiff[k]) stillOff[k] = quizDiff[k];
          if (Object.keys(stillOff).length) await api.updateQuestionMeta(question.id, stillOff);
        } else {
          await api.updateQuestionMeta(question.id, quizDiff);
        }
      }
      const qDiff = diffOf(QUESTION_FIELDS);
      if (Object.keys(qDiff).length) await api.updateQuestionMeta(question.id, qDiff);

      const content: { difficulty?: string; visual_clues?: string } = {};
      if (difficulty !== (detail.difficulty || 'Medium')) content.difficulty = difficulty;
      if ((visualClues || '') !== (detail.visual_clues || '')) content.visual_clues = visualClues;
      if (Object.keys(content).length) await api.updateQuestionContent(question.id, content);

      const fresh = await api.getQuestionDetail(question.id);
      hydrate(fresh);
      setHistory(null);
      onSaved(mergeIntoQuestion(question, fresh));
      onClose();
    } catch (err: any) {
      console.error('Failed to save tags:', err);
      setError(err?.message || 'Failed to save. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const handleRetag = async () => {
    if (!question) return;
    setRetagging(true);
    setError(null);
    try {
      const d = await api.retagQuestion(question.id, retagForce);
      hydrate(d);
      setHistory(null);
      onSaved(mergeIntoQuestion(question, d));
    } catch (e: any) {
      setError(e?.message || 'Re-tagging failed.');
    } finally {
      setRetagging(false);
    }
  };

  const saveRepeat = async (r: RepeatEntry) => {
    if (!question) return;
    try {
      await api.patchRepeat(question.id, r.id, {
        quiz_name: (repeatDraft.quiz_name ?? '').toString().trim() || undefined,
        round_number: toInt(repeatDraft.round_number) ?? undefined,
        question_in_round: toInt(repeatDraft.question_in_round) ?? undefined,
        question_number: toInt(repeatDraft.question_number) ?? undefined,
      });
      const d = await api.getQuestionDetail(question.id);
      setDetail(d);
      setEditingRepeat(null);
      setHistory(null);
      onSaved(mergeIntoQuestion(question, d));
    } catch (e: any) {
      setError(e?.message || 'Could not update the entry.');
    }
  };

  const removeRepeat = async (r: RepeatEntry) => {
    if (!question) return;
    try {
      await api.deleteRepeat(question.id, r.id);
      const d = await api.getQuestionDetail(question.id);
      setDetail(d);
      setRemovingRepeat(null);
      setHistory(null);
      onSaved(mergeIntoQuestion(question, d));
    } catch (e: any) {
      setError(e?.message || 'Could not remove the entry.');
    }
  };

  if (!isOpen || !question) return null;

  const headLabel = detail?.meta.question_label || question.question_label;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm animate-in fade-in">
      <div className="relative flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
              <Tag className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-[16px] font-bold text-slate-900">Edit tags</h3>
              <p className="text-[12px] text-slate-500 font-normal">Every tag on this question can be changed. Changes are saved to its history.</p>
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors cursor-pointer">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Question context */}
        <div className="border-b border-slate-100 bg-slate-50/70 px-6 py-3 shrink-0">
          <div className="flex items-start gap-2">
            {headLabel && (
              <span className="mt-0.5 shrink-0 rounded bg-slate-200 px-1.5 py-0.5 text-[11px] font-bold text-slate-700">{headLabel}</span>
            )}
            <p className="text-[13px] font-semibold text-slate-800 leading-snug">{question.question_text}</p>
            {(detail?.meta.star_marked ?? question.star_marked) && <Star className="mt-0.5 h-4 w-4 shrink-0 fill-amber-400 text-amber-500" />}
          </div>
          <p className="mt-1 text-[12px] text-slate-600">
            <span className="font-semibold text-slate-700">Answer:</span>{' '}
            <span className="font-bold text-emerald-700">{question.answer}</span>
          </p>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 overflow-x-auto border-b border-slate-200 px-4 pt-2 shrink-0 scrollbar-none">
          {TABS.map((t) => {
            const count = t.key === 'repeated' ? detail?.repeated.length : undefined;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`whitespace-nowrap rounded-t-lg px-3.5 py-2 text-[12.5px] font-semibold cursor-pointer border-b-2 shrink-0 ${
                  tab === t.key ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-500 hover:text-slate-800'
                }`}
              >
                {t.label}
                {count ? <span className="ml-1.5 rounded-full bg-emerald-100 px-1.5 text-[10.5px] text-emerald-700">{count}</span> : null}
              </button>
            );
          })}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4 text-[13px]">
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-red-50 border border-red-200 p-3 text-[12.5px] text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {loading || !detail ? (
            <div className="flex items-center justify-center py-16 text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading tags…
            </div>
          ) : (
            <>
              {/* ---------------- Topics ---------------- */}
              {tab === 'topics' && (
                <div className="space-y-3">
                  <p className="text-[12px] text-slate-500">
                    Topic, then subtopic, then keywords. A question can sit under more than one topic. The first one is the main topic.
                  </p>

                  {paths.length === 0 && (
                    <p className="rounded-lg border border-dashed border-slate-300 p-3.5 text-center text-[12px] text-slate-400">
                      Not tagged yet. Add a topic below, or re-tag with AI.
                    </p>
                  )}
                  {paths.length === 0 && detail.tag_error && (
                    <p className="rounded-lg border border-red-200 bg-red-50 p-2.5 text-[12px] text-red-700 break-words">
                      The AI could not tag this question. {detail.tag_error}
                    </p>
                  )}

                  {paths.map((p, i) => {
                    const theme = themes.find((t) => t.name === p.l1);
                    const subNames = theme ? theme.subtopics.map((s) => s.name) : [];
                    const customTheme = p._ct || (!!p.l1 && !themeNames.includes(p.l1));
                    const customSub = p._cs || (!!p.l2 && !subNames.includes(p.l2 as string));
                    const sub = theme?.subtopics.find((s) => s.name === p.l2);
                    return (
                      <div key={i} className="rounded-xl border border-slate-200 bg-white p-3 sm:p-3.5 space-y-2.5 shadow-xs">
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                            {i === 0 ? 'Main topic' : `Also under (${i + 1})`}
                            {p.source && (
                              <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${p.source === 'HUMAN' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'}`}>
                                {p.source === 'HUMAN' ? 'You' : 'AI'}
                              </span>
                            )}
                          </span>
                          <button type="button" onClick={() => removePath(i)} className="text-slate-400 hover:text-red-600 cursor-pointer p-0.5" title="Remove this topic">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                          <Field label="Topic" compact>
                            <select
                              value={customTheme ? '__custom__' : p.l1}
                              onChange={(e) => {
                                const v = e.target.value;
                                if (v === '__custom__') updatePath(i, { _ct: true, l1: customTheme ? p.l1 : '', _cs: true });
                                else updatePath(i, { _ct: false, l1: v, ...(subNames.includes(p.l2 as string) && v === p.l1 ? {} : { l2: '', _cs: false }) });
                              }}
                              className={inputCompactCls}
                            >
                              <option value="">Choose a topic…</option>
                              {themes.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                              <option value="__custom__">+ New topic…</option>
                            </select>
                            {customTheme && (
                              <input type="text" value={p.l1} onChange={(e) => updatePath(i, { l1: e.target.value, _ct: true })} placeholder="Name the new topic" className={`${inputCompactCls} mt-1.5`} />
                            )}
                          </Field>
                          <Field label="Subtopic" compact>
                            <select
                              value={customSub ? '__custom__' : (p.l2 || '')}
                              disabled={!p.l1}
                              title={sub?.description || undefined}
                              onChange={(e) => {
                                const v = e.target.value;
                                if (v === '__custom__') updatePath(i, { _cs: true, l2: customSub ? p.l2 : '' });
                                else updatePath(i, { _cs: false, l2: v });
                              }}
                              className={`${inputCompactCls} disabled:bg-slate-50`}
                            >
                              <option value="">None</option>
                              {subNames.map((s) => <option key={s} value={s}>{s}</option>)}
                              <option value="__custom__">+ New subtopic…</option>
                            </select>
                            {customSub && (
                              <input type="text" value={p.l2 || ''} onChange={(e) => updatePath(i, { l2: e.target.value, _cs: true })} placeholder="Name the new subtopic" className={`${inputCompactCls} mt-1.5`} />
                            )}
                            {sub?.description && (
                              <p className="mt-0.5 text-[11px] leading-tight text-slate-400 truncate" title={sub.description}>
                                {sub.description}
                              </p>
                            )}
                          </Field>
                        </div>
                        <Field label="Keywords for this question" compact>
                          <ChipInput values={p.keywords} onChange={(kw) => updatePath(i, { keywords: kw })} placeholder="e.g. Comet, Shoes" compact />
                        </Field>
                      </div>
                    );
                  })}

                  <div className="flex flex-wrap items-center gap-2.5 pt-1">
                    <button type="button" onClick={addPath} disabled={paths.length >= 6} className="inline-flex items-center gap-1 rounded-lg border border-dashed border-slate-300 px-3 py-1.5 text-[12px] font-semibold text-slate-600 hover:border-indigo-400 hover:text-indigo-600 disabled:opacity-40 cursor-pointer">
                      <Plus className="h-3.5 w-3.5" /> Add another topic
                    </button>
                    <button type="button" onClick={handleRetag} disabled={retagging} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50 cursor-pointer">
                      {retagging ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />} Re-tag with AI
                    </button>
                    <label className="inline-flex items-center gap-1.5 text-[11px] text-slate-500 cursor-pointer">
                      <input type="checkbox" checked={retagForce} onChange={(e) => setRetagForce(e.target.checked)} />
                      Replace my edits too
                    </label>
                  </div>

                  <div className="border-t border-slate-100 pt-3">
                    <Field label="Hashtags" hint="Your own free-form tags. They are kept separate from the keywords above." compact>
                      <ChipInput values={hashtags} onChange={setHashtags} placeholder="e.g. Throwback, Finals (press Enter)" prefix="#" compact />
                    </Field>
                  </div>
                </div>
              )}

              {/* ---------------- Quiz ---------------- */}
              {tab === 'quiz' && (
                <div className="space-y-4">
                  <p className="text-[12px] text-slate-500">
                    These come from the file name of the presentation <span className="font-semibold text-slate-700">{detail.deck.source_presentation || detail.document.filename}</span>.
                    Fix anything that was read wrongly.
                  </p>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <Field label="Quiz name"><input className={inputCls} value={meta.quiz_name ?? ''} onChange={(e) => setMetaField('quiz_name', e.target.value)} /></Field>
                    <Field label="Venue (college, school or company)"><input className={inputCls} value={meta.college_institution ?? ''} onChange={(e) => setMetaField('college_institution', e.target.value)} /></Field>
                    <Field label="City (where the quiz was held)"><input className={inputCls} value={meta.city_name ?? ''} onChange={(e) => setMetaField('city_name', e.target.value)} /></Field>
                    <Field label="Prelims / Finals">
                      <input className={inputCls} list="round-stages" value={meta.round_stage ?? ''} onChange={(e) => setMetaField('round_stage', e.target.value)} />
                      <datalist id="round-stages">{ROUND_STAGES.map((s) => <option key={s} value={s} />)}</datalist>
                    </Field>
                    <Field label="Year of the quiz"><input type="number" className={inputCls} value={meta.deck_year ?? ''} onChange={(e) => setMetaField('deck_year', e.target.value)} /></Field>
                    <Field label="Language">
                      <input className={inputCls} list="languages" value={meta.language ?? ''} onChange={(e) => setMetaField('language', e.target.value)} />
                      <datalist id="languages">{LANGUAGES.map((s) => <option key={s} value={s} />)}</datalist>
                    </Field>
                    <Field label="Primary audience">
                      <select className={inputCls} value={meta.primary_audience ?? ''} onChange={(e) => setMetaField('primary_audience', e.target.value)}>
                        <option value="">Not set</option>
                        {(taxonomy?.audiences ?? []).map((a) => <option key={a} value={a}>{a}</option>)}
                        {meta.primary_audience && !(taxonomy?.audiences ?? []).includes(meta.primary_audience) && <option value={meta.primary_audience}>{meta.primary_audience}</option>}
                      </select>
                    </Field>
                    <Field label="Secondary audience" hint="Optional. For example School or Corporate when the questions suit them too.">
                      <input className={inputCls} list="audiences" value={meta.secondary_audience ?? ''} onChange={(e) => setMetaField('secondary_audience', e.target.value)} />
                      <datalist id="audiences">{(taxonomy?.audiences ?? []).map((a) => <option key={a} value={a} />)}</datalist>
                    </Field>
                    <Field label="Zerodha Varsity question">
                      <Toggle value={!!meta.is_varsity} onChange={(v) => setMetaField('is_varsity', v)} />
                      <p className="mt-1 text-[11px] text-slate-400">Decks with ZVQ in the name are marked Yes automatically.</p>
                    </Field>
                  </div>
                  <label className="flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50/70 p-3 text-[12.5px] text-slate-700 cursor-pointer">
                    <input type="checkbox" className="mt-0.5" checked={applyToDeck} onChange={(e) => setApplyToDeck(e.target.checked)} />
                    <span>
                      <span className="font-semibold">Apply these changes to every question from this presentation.</span>
                      <span className="block text-[11.5px] text-slate-500">Questions you edited by hand keep their own value. The secondary audience always stays on this question only.</span>
                    </span>
                  </label>
                </div>
              )}

              {/* ---------------- Question ---------------- */}
              {tab === 'question' && (
                <div className="space-y-5">
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                    <Field label="Question number"><input type="number" className={inputCls} value={meta.question_number ?? ''} onChange={(e) => setMetaField('question_number', e.target.value)} /></Field>
                    <Field label="Round"><input type="number" className={inputCls} value={meta.round_number ?? ''} onChange={(e) => setMetaField('round_number', e.target.value)} /></Field>
                    <Field label="Number in the round"><input type="number" className={inputCls} value={meta.question_in_round ?? ''} onChange={(e) => setMetaField('question_in_round', e.target.value)} /></Field>
                    <Field label="Question format">
                      <select
                        className={inputCls}
                        value={
                          meta.question_format === 'CONNECT'
                            ? 'CONNECT_4'
                            : meta.question_format === 'THREE_CLUE'
                            ? 'STEPPED_CLUE'
                            : meta.question_format ?? 'QA'
                        }
                        onChange={(e) => setMetaField('question_format', e.target.value)}
                      >
                        {CANONICAL_FORMAT_OPTIONS.map((f) => (
                          <option key={f} value={f}>
                            {FORMAT_LABELS[f] || f}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Answer type">
                      <select className={inputCls} value={meta.answer_type ?? 'TEXT'} onChange={(e) => setMetaField('answer_type', e.target.value)}>
                        {(taxonomy?.answer_types ?? Object.keys(ANSWER_TYPE_LABELS)).map((f) => <option key={f} value={f}>{ANSWER_TYPE_LABELS[f] || f}</option>)}
                      </select>
                    </Field>
                    <Field label="Star marked"><Toggle value={!!meta.star_marked} onChange={(v) => setMetaField('star_marked', v)} /></Field>
                  </div>

                  <div className="border-t border-slate-100 pt-4">
                    <p className="mb-3 text-[11.5px] font-bold uppercase tracking-wider text-slate-500">Geography and time</p>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                      <Field label="Place the question is about"><input className={inputCls} placeholder="e.g. Mumbai, India" value={meta.geo_region ?? ''} onChange={(e) => setMetaField('geo_region', e.target.value)} /></Field>
                      <Field label="India or international">
                        <select className={inputCls} value={meta.india_international ?? 'NA'} onChange={(e) => setMetaField('india_international', e.target.value)}>
                          {(taxonomy?.geographies ?? ['IN', 'INTL', 'NA']).map((g) => <option key={g} value={g}>{GEO_LABELS[g] || g}</option>)}
                        </select>
                      </Field>
                      <Field label="Year the question is about" hint="For example 2016 for demonetisation.">
                        <input type="number" className={inputCls} value={meta.relevant_year ?? ''} onChange={(e) => setMetaField('relevant_year', e.target.value)} />
                      </Field>
                    </div>
                  </div>

                  <div className="border-t border-slate-100 pt-4">
                    <Field label="Difficulty" hint="Not shown in the list. Create Quiz still uses it.">
                      <select className={`${inputCls} sm:w-48`} value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
                        {DIFFICULTIES.map((d) => <option key={d} value={d}>{d}</option>)}
                      </select>
                    </Field>
                  </div>
                </div>
              )}

              {/* ---------------- Source ---------------- */}
              {tab === 'source' && (
                <div className="space-y-5">
                  <div className="rounded-lg border border-slate-200 p-4 space-y-1.5">
                    <p className="text-[11.5px] font-bold uppercase tracking-wider text-slate-500">Origin presentation</p>
                    <p className="text-[14px] font-semibold text-slate-800">{detail.document.title || 'Archive deck'}</p>
                    {detail.document.filename && <p className="text-[12px] text-slate-500 font-mono break-all">{detail.document.filename}</p>}
                    <p className="text-[12px] text-slate-600">
                      Slides: <span className="font-semibold">{detail.source_slide_range || 'not recorded'}</span>
                    </p>
                    {onFilterDeck && (
                      <button type="button" onClick={() => { onFilterDeck(detail.document.id); onClose(); }} className="mt-1 inline-flex items-center gap-1 text-[12px] font-semibold text-blue-600 hover:text-blue-700 cursor-pointer">
                        Show only questions from this presentation <ExternalLink className="h-3 w-3" />
                      </button>
                    )}
                  </div>

                  <div className="space-y-2">
                    <p className="text-[11.5px] font-bold uppercase tracking-wider text-slate-500">Images</p>
                    {detail.question_images.length === 0 && detail.answer_images.length === 0 ? (
                      <p className="text-[12px] text-slate-400">No images on this question.</p>
                    ) : (
                      <div className="flex flex-wrap gap-3">
                        {detail.question_images.map((u, i) => (
                          <figure key={`q${i}`} className="w-32">
                            <img src={getImageSrc(u)} alt="Question image" className="h-24 w-32 rounded-lg border border-sky-300 object-cover" />
                            <figcaption className="mt-1 text-center text-[10.5px] font-bold uppercase tracking-wide text-sky-700">Question image</figcaption>
                          </figure>
                        ))}
                        {detail.answer_images.map((u, i) => (
                          <figure key={`a${i}`} className="w-32">
                            <img src={getImageSrc(u)} alt="Answer image" className="h-24 w-32 rounded-lg border border-emerald-300 object-cover" />
                            <figcaption className="mt-1 text-center text-[10.5px] font-bold uppercase tracking-wide text-emerald-700">Answer image</figcaption>
                          </figure>
                        ))}
                      </div>
                    )}
                  </div>

                  <Field label="Visual clue text" hint="Read from the question's images. Edit it if the description is wrong.">
                    <textarea rows={4} className={inputCls} value={visualClues} onChange={(e) => setVisualClues(e.target.value)} placeholder="No visual clue text" />
                  </Field>
                </div>
              )}

              {/* ---------------- Repeated ---------------- */}
              {tab === 'repeated' && (
                <div className="space-y-3">
                  <p className="text-[12px] text-slate-500">
                    The first time a question is uploaded this list is empty. When the same question turns up in another quiz it is not stored again; it is listed here instead.
                  </p>
                  {detail.repeated.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-slate-300 p-5 text-center text-[12.5px] text-slate-400">Not repeated in any other quiz yet.</p>
                  ) : (
                    detail.repeated.map((r) => (
                      <div key={r.id} className="rounded-lg border border-emerald-200 bg-emerald-50/40 p-3.5">
                        {editingRepeat === r.id ? (
                          <div className="space-y-2.5">
                            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-4">
                              <div className="sm:col-span-4"><Field label="Quiz"><input className={inputCls} value={repeatDraft.quiz_name ?? ''} onChange={(e) => setRepeatDraft({ ...repeatDraft, quiz_name: e.target.value })} /></Field></div>
                              <Field label="Round"><input type="number" className={inputCls} value={repeatDraft.round_number ?? ''} onChange={(e) => setRepeatDraft({ ...repeatDraft, round_number: e.target.value })} /></Field>
                              <Field label="Q in round"><input type="number" className={inputCls} value={repeatDraft.question_in_round ?? ''} onChange={(e) => setRepeatDraft({ ...repeatDraft, question_in_round: e.target.value })} /></Field>
                              <Field label="Q number"><input type="number" className={inputCls} value={repeatDraft.question_number ?? ''} onChange={(e) => setRepeatDraft({ ...repeatDraft, question_number: e.target.value })} /></Field>
                            </div>
                            <div className="flex gap-2">
                              <button type="button" onClick={() => saveRepeat(r)} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-indigo-700 cursor-pointer">Save entry</button>
                              <button type="button" onClick={() => setEditingRepeat(null)} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-slate-600 cursor-pointer">Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <div className="flex items-start justify-between gap-3">
                            <div>
                              <p className="flex items-center gap-1.5 text-[13px] font-semibold text-emerald-900"><Layers className="h-3.5 w-3.5" />{r.label}</p>
                              <p className="mt-0.5 text-[11.5px] text-slate-500">
                                {r.slide_range ? `${r.slide_range} · ` : ''}{r.match_type === 'CONFIRMED' ? 'Confirmed by you' : 'Exact match'}
                                {r.found_at ? ` · found ${new Date(r.found_at).toLocaleDateString()}` : ''}
                              </p>
                            </div>
                            <div className="flex shrink-0 items-center gap-3">
                              <button type="button" onClick={() => { setEditingRepeat(r.id); setRepeatDraft({ quiz_name: r.quiz_name ?? '', round_number: r.round_number ?? '', question_in_round: r.question_in_round ?? '', question_number: r.question_number ?? '' }); }} className="text-slate-400 hover:text-indigo-600 cursor-pointer" title="Edit entry"><Pencil className="h-3.5 w-3.5" /></button>
                              {removingRepeat === r.id ? (
                                <span className="flex items-center gap-2 text-[11.5px]">
                                  <button type="button" onClick={() => removeRepeat(r)} className="font-semibold text-red-600 cursor-pointer">Remove</button>
                                  <button type="button" onClick={() => setRemovingRepeat(null)} className="text-slate-500 cursor-pointer">Keep</button>
                                </span>
                              ) : (
                                <button type="button" onClick={() => setRemovingRepeat(r.id)} className="text-slate-400 hover:text-red-600 cursor-pointer" title="Remove entry"><Trash2 className="h-3.5 w-3.5" /></button>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* ---------------- History ---------------- */}
              {tab === 'history' && (
                <div className="space-y-2">
                  {history === null ? (
                    <div className="flex items-center justify-center py-10 text-slate-400"><Loader2 className="h-4 w-4 animate-spin mr-2" /> Loading history…</div>
                  ) : history.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-slate-300 p-5 text-center text-[12.5px] text-slate-400">No changes recorded yet.</p>
                  ) : (
                    history.map((h) => (
                      <div key={h.id} className="rounded-lg border border-slate-200 p-3">
                        <div className="flex items-center justify-between text-[11.5px] text-slate-500">
                          <span className="font-bold uppercase tracking-wide text-slate-700">{h.field.replace(/_/g, ' ')}</span>
                          <span>{h.changed_at ? new Date(h.changed_at).toLocaleString() : ''} · {h.actor || 'system'} · {h.kind.toLowerCase()}</span>
                        </div>
                        <p className="mt-1 break-words text-[12.5px] text-slate-700">
                          {h.old_value ? <span className="text-slate-400 line-through">{shorten(h.old_value)}</span> : <span className="text-slate-400">empty</span>}
                          {' → '}
                          <span className="font-medium">{h.new_value ? shorten(h.new_value) : 'removed'}</span>
                        </p>
                      </div>
                    ))
                  )}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2.5 border-t border-slate-100 bg-slate-50/60 px-6 py-3.5 shrink-0">
          <button type="button" onClick={onClose} disabled={saving} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer">
            Close
          </button>
          <button type="button" onClick={handleSave} disabled={saving || loading || !detail || !hasChanges()} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-[13px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors cursor-pointer">
            {saving ? (<><Loader2 className="h-3.5 w-3.5 animate-spin" /><span>Saving…</span></>) : (<><Check className="h-3.5 w-3.5" /><span>Save changes</span></>)}
          </button>
        </div>
      </div>
    </div>
  );
}

function shorten(s: string): string {
  if (s.startsWith('[') || s.startsWith('{')) {
    try {
      const j = JSON.parse(s);
      if (Array.isArray(j)) {
        return j.map((x) => (typeof x === 'object' && x ? `${x.l1}${x.l2 ? ' › ' + x.l2 : ''}${x.keywords?.length ? ' (' + x.keywords.join(', ') + ')' : ''}` : String(x))).join(' | ') || 'none';
      }
    } catch { /* fall through */ }
  }
  return s.length > 160 ? `${s.slice(0, 160)}…` : s;
}

/** Fold the saved detail back into the list row so the table updates without a reload. */
function mergeIntoQuestion(q: HistoricalQuestion, d: QuestionDetail): HistoricalQuestion {
  const keywords = d.topic_paths.flatMap((p) => p.keywords || []);
  const seen = new Set<string>();
  const tags = [...keywords, ...d.hashtags].filter((t) => (seen.has(t.toLowerCase()) ? false : (seen.add(t.toLowerCase()), true)));
  const themes = Array.from(new Set(d.topic_paths.map((p) => p.l1)));
  return {
    ...q,
    topic: d.topic || q.topic,
    subtopic: d.subtopic ?? null,
    topics: themes.length ? themes : q.topics,
    tags,
    difficulty: d.difficulty || q.difficulty,
    visual_clues: d.visual_clues ?? null,
    topic_paths: d.topic_paths,
    repeated: d.repeated,
    repeated_count: d.repeated.length,
    question_number: d.meta.question_number ?? null,
    question_label: d.meta.question_label || null,
    question_format: d.meta.question_format || q.question_format,
    star_marked: d.meta.star_marked,
  };
}
