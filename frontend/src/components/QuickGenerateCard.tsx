'use client';

import { useState, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowRight,
  Loader2,
  AlertCircle,
  Sparkles,
  Database,
  Tag,
  X,
  Search,
  Plus,
  Trophy,
  BookOpen,
  HelpCircle,
} from 'lucide-react';
import { api } from '../lib/api';
import { TopicItem, TopicSummary, TagItem } from '../lib/types';
import { saveLocalQuiz } from '../lib/quizStorage';

import { QuizFormat } from '../lib/types';

type DifficultyPreset = 'Balanced' | 'Easy-heavy' | 'Hard-heavy' | 'Custom';



function getDistributionForPreset(preset: DifficultyPreset, count: number): { easy: number; medium: number; hard: number } {
  if (preset === 'Easy-heavy') {
    if (count === 10) return { easy: 6, medium: 3, hard: 1 };
    if (count === 20) return { easy: 12, medium: 6, hard: 2 };
    const easy = Math.max(1, Math.round(count * 0.6));
    const medium = Math.max(1, Math.round(count * 0.3));
    const hard = Math.max(0, count - easy - medium);
    return { easy, medium, hard };
  }

  if (preset === 'Hard-heavy') {
    if (count === 10) return { easy: 1, medium: 3, hard: 6 };
    if (count === 20) return { easy: 2, medium: 6, hard: 12 };
    const hard = Math.max(1, Math.round(count * 0.6));
    const medium = Math.max(1, Math.round(count * 0.3));
    const easy = Math.max(0, count - hard - medium);
    return { easy, medium, hard };
  }

  // Balanced default
  if (count === 10) return { easy: 3, medium: 5, hard: 2 };
  if (count === 20) return { easy: 5, medium: 10, hard: 5 };
  if (count === 5) return { easy: 1, medium: 3, hard: 1 };
  if (count === 15) return { easy: 4, medium: 8, hard: 3 };

  const easy = Math.max(1, Math.round(count * 0.25));
  const hard = Math.max(1, Math.round(count * 0.25));
  const medium = Math.max(0, count - easy - hard);
  return { easy, medium, hard };
}

export default function QuickGenerateCard() {
  const router = useRouter();

  // Topic Predictive State
  const [topic, setTopic] = useState('');
  const [subtopic, setSubtopic] = useState('');
  const [topicsList, setTopicsList] = useState<TopicItem[]>([]);
  const [filteredTopics, setFilteredTopics] = useState<TopicItem[]>([]);
  const [showTopicDropdown, setShowTopicDropdown] = useState(false);
  const [topicSummary, setTopicSummary] = useState<TopicSummary | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Default Quiz Format & Question Count
  const quizFormat: QuizFormat = 'TOURNAMENT_PRELIMS';
  const [questionCount, setQuestionCount] = useState<number>(20);

  // Tags & Live Compilation State
  const [vaultTags, setVaultTags] = useState<TagItem[]>([]);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');

  // Difficulty Distribution
  const [difficultyPreset, setDifficultyPreset] = useState<DifficultyPreset>('Balanced');
  const [easyCount, setEasyCount] = useState<number>(3);
  const [mediumCount, setMediumCount] = useState<number>(5);
  const [hardCount, setHardCount] = useState<number>(2);

  // Format
  const [questionTypes, setQuestionTypes] = useState<string[]>(['SLIDE_QA']);

  // Quizmaster Personality
  const [personality, setPersonality] = useState<string>('DEFAULT');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load topics and vault tags on mount
  useEffect(() => {
    api.getTopics()
      .then((list) => {
        setTopicsList(list);
        setFilteredTopics(list);
      })
      .catch((err) => console.error('Error fetching vault topics:', err));

    api.getVaultTags()
      .then((tags) => {
        setVaultTags(tags);
      })
      .catch((err) => console.error('Error fetching vault tags:', err));
  }, []);

  // Update topic summary whenever selected topic matches an indexed topic
  useEffect(() => {
    if (topic.trim()) {
      api.getTopicSummary(topic.trim())
        .then((summary) => setTopicSummary(summary))
        .catch(() => setTopicSummary(null));
    } else {
      setTopicSummary(null);
    }
  }, [topic]);


  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowTopicDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleToggleTag = (tagName: string) => {
    if (selectedTags.includes(tagName)) {
      setSelectedTags(selectedTags.filter((t) => t !== tagName));
    } else {
      setSelectedTags([...selectedTags, tagName]);
    }
  };

  const handleAddCustomTag = (e?: React.KeyboardEvent | React.MouseEvent) => {
    if (e && 'key' in e && e.key !== 'Enter') return;
    if (e) e.preventDefault();
    const clean = tagInput.trim().replace(/^#/, '');
    if (clean && !selectedTags.includes(clean)) {
      setSelectedTags([...selectedTags, clean]);
      setTagInput('');
    }
  };

  const handleRemoveTag = (tagName: string) => {
    setSelectedTags(selectedTags.filter((t) => t !== tagName));
  };

  const handleTopicInputChange = (val: string) => {
    setTopic(val);
    if (!val.trim()) {
      setFilteredTopics(topicsList);
    } else {
      const q = val.toLowerCase();
      setFilteredTopics(topicsList.filter((t) => t.topic.toLowerCase().includes(q)));
    }
    setShowTopicDropdown(true);
  };

  const handleSelectTopic = (selected: string) => {
    setTopic(selected);
    setShowTopicDropdown(false);
  };

  const totalAllocated = easyCount + mediumCount + hardCount;
  const isDistributionValid = totalAllocated === questionCount;

  const handleQuestionCountChange = (newCount: number) => {
    setQuestionCount(newCount);
    if (difficultyPreset !== 'Custom') {
      const dist = getDistributionForPreset(difficultyPreset, newCount);
      setEasyCount(dist.easy);
      setMediumCount(dist.medium);
      setHardCount(dist.hard);
    }
  };

  const handlePresetChange = (preset: DifficultyPreset) => {
    setDifficultyPreset(preset);
    if (preset !== 'Custom') {
      const dist = getDistributionForPreset(preset, questionCount);
      setEasyCount(dist.easy);
      setMediumCount(dist.medium);
      setHardCount(dist.hard);
    }
  };

  const handleDistributionInput = (type: 'easy' | 'medium' | 'hard', val: number) => {
    setDifficultyPreset('Custom');
    const safeVal = Math.max(0, isNaN(val) ? 0 : val);
    if (type === 'easy') setEasyCount(safeVal);
    if (type === 'medium') setMediumCount(safeVal);
    if (type === 'hard') setHardCount(safeVal);
  };

  const toggleQuestionType = (type: string) => {
    if (questionTypes.includes(type)) {
      if (questionTypes.length > 1) {
        setQuestionTypes(questionTypes.filter((t) => t !== type));
      }
    } else {
      setQuestionTypes([...questionTypes, type]);
    }
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    if (!topic.trim() && selectedTags.length === 0) {
      setError('Please select or enter a quiz topic or select at least one concept tag');
      return;
    }

    if (!isDistributionValid) {
      setError(`Difficulty distribution total (${totalAllocated}) must equal question count (${questionCount}).`);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const effectiveTopic = topic.trim() || (selectedTags.length > 0 ? selectedTags.join(', ') : 'General Knowledge');
      const tagSuffix = selectedTags.length > 0 ? ` [Tags: ${selectedTags.join(', ')}]` : '';
      const promptSummary = `Create a ${questionCount}-question quiz on ${effectiveTopic}${tagSuffix} with distribution: ${easyCount} Easy, ${mediumCount} Medium, ${hardCount} Hard.`;

      const quiz = await api.generateQuiz({
        topic: effectiveTopic,
        subtopic: subtopic.trim() || undefined,
        quiz_format: quizFormat,
        difficulty: difficultyPreset,
        difficulty_distribution: {
          easy: easyCount,
          medium: mediumCount,
          hard: hardCount,
        },
        question_count: questionCount,
        question_types: questionTypes,
        generation_mode: 'HISTORICAL',
        tags: selectedTags.length > 0 ? selectedTags : undefined,
        personality: personality !== 'DEFAULT' ? personality : undefined,
        style: 'QSHALA_HISTORICAL',
        raw_prompt: promptSummary,
      });

      // Persist to local storage immediately so it always appears in Generated Quizzes
      saveLocalQuiz(quiz);

      router.push(`/quizzes/${quiz.id}`);
    } catch (err: any) {
      console.error(err);
      setError(err.message || 'Failed to create quiz. Please verify the questions repository.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-6 sm:p-7 shadow-sm shadow-slate-100/50">
      <form onSubmit={handleGenerate} className="space-y-6">
        {/* Section 1: Topic Selection */}
        <div className="space-y-4" ref={dropdownRef}>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-[13px] font-semibold text-slate-800">
                Quiz Topic
              </label>
              <span className="text-[12px] text-slate-400 font-normal">
                Type to search available topics
              </span>
            </div>

            <div className="relative">
              <input
                type="text"
                value={topic}
                onChange={(e) => handleTopicInputChange(e.target.value)}
                onFocus={() => setShowTopicDropdown(true)}
                placeholder="e.g. Australian History, World Geography, Science & Nature"
                className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-[14px] font-normal text-slate-900 placeholder:text-slate-400 focus:outline-none"
              />

              {showTopicDropdown && filteredTopics.length > 0 && (
                <div className="absolute z-20 left-0 right-0 mt-1 max-h-56 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg py-1">
                  <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400 border-b border-slate-100">
                    Available Topics ({filteredTopics.length})
                  </div>
                  {filteredTopics.map((t) => (
                    <button
                      key={t.topic}
                      type="button"
                      onClick={() => handleSelectTopic(t.topic)}
                      className="w-full text-left px-3.5 py-2 hover:bg-slate-50 flex items-center justify-between text-[13px] text-slate-800 cursor-pointer"
                    >
                      <span className="font-medium">{t.topic}</span>
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600">
                        {t.count} questions
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Subtopics from Topic Summary if available */}
            {topicSummary && topicSummary.subtopics && topicSummary.subtopics.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                <span className="text-slate-400 text-[11px] font-medium">Subtopics:</span>
                {topicSummary.subtopics.map((sub) => (
                  <button
                    key={sub}
                    type="button"
                    onClick={() => setSubtopic(subtopic === sub ? '' : sub)}
                    className={`px-2 py-0.5 rounded-md text-[11px] font-medium border transition-colors cursor-pointer ${
                      subtopic === sub
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-slate-50 text-slate-600 border-slate-200 hover:bg-slate-100'
                    }`}
                  >
                    {sub}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Micro Concept Tags Dual-Axis Filter */}
          <div className="space-y-2.5 rounded-lg border border-slate-200/90 bg-slate-50/50 p-3.5">
            <div className="flex items-center justify-between">
              <label className="text-[12.5px] font-semibold text-slate-800 flex items-center gap-1.5">
                <Tag className="h-3.5 w-3.5 text-blue-600" />
                <span>Micro Concept Tags (Cross-Topic Compilation)</span>
              </label>
              <span className="text-[11px] text-slate-400">
                Pull specific concepts across multiple decks
              </span>
            </div>

            {/* Selected Tags Chips */}
            {selectedTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                {selectedTags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 rounded-md bg-blue-100 text-blue-800 px-2.5 py-1 text-[12px] font-medium"
                  >
                    <span>#{tag}</span>
                    <button
                      type="button"
                      onClick={() => handleRemoveTag(tag)}
                      className="hover:text-blue-900 focus:outline-none cursor-pointer"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
                <button
                  type="button"
                  onClick={() => setSelectedTags([])}
                  className="text-[11px] text-slate-400 hover:text-slate-600 underline ml-1 cursor-pointer"
                >
                  Clear all
                </button>
              </div>
            )}

            {/* Tag Add / Search Input */}
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
                <input
                  type="text"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={handleAddCustomTag}
                  placeholder="Type concept tag (e.g. Captain Cook, Botany, Maritime) and press Enter..."
                  className="w-full rounded-md border border-slate-200 bg-white pl-8 pr-3 py-1.5 text-[12.5px] text-slate-800 placeholder:text-slate-400 focus:outline-none"
                />
              </div>
              <button
                type="button"
                onClick={handleAddCustomTag}
                disabled={!tagInput.trim()}
                className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-slate-900 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-colors"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>Add Tag</span>
              </button>
            </div>

            {/* Concept Tag Cloud from Vault */}
            {vaultTags.length > 0 && (
              <div className="space-y-1 pt-1">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                  Popular Concept Tags:
                </span>
                <div className="flex flex-wrap gap-1.5 max-h-24 overflow-y-auto pt-0.5">
                  {vaultTags
                    .filter((t) => !tagInput.trim() || t.tag.toLowerCase().includes(tagInput.toLowerCase()))
                    .slice(0, 16)
                    .map((item) => {
                      const isSelected = selectedTags.includes(item.tag);
                      return (
                        <button
                          key={item.tag}
                          type="button"
                          onClick={() => handleToggleTag(item.tag)}
                          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium border transition-colors cursor-pointer ${
                            isSelected
                              ? 'bg-blue-600 text-white border-blue-600 shadow-xs'
                              : 'bg-white text-slate-700 border-slate-200 hover:border-blue-400 hover:bg-blue-50/40'
                          }`}
                        >
                          <span>#{item.tag}</span>
                          <span
                            className={`rounded-full px-1 text-[9.5px] ${
                              isSelected ? 'bg-blue-700 text-blue-100' : 'bg-slate-100 text-slate-500'
                            }`}
                          >
                            {item.count}
                          </span>
                        </button>
                      );
                    })}
                </div>
              </div>
            )}
          </div>

          {/* Questions Available in Database */}
          {topicSummary && topicSummary.total_questions > 0 && (
            <div className="rounded-lg bg-blue-50/60 border border-blue-100 p-3 text-[12px] space-y-1.5">
              <div className="flex items-center justify-between font-semibold text-blue-900">
                <span className="flex items-center gap-1.5">
                  <Database className="h-3.5 w-3.5 text-blue-600" />
                  {topicSummary.total_questions} Questions Available in Vault
                </span>
                <span className="text-[11px] text-blue-600 font-medium">
                  {topicSummary.subtopics?.length || 0} Subtopics Indexed
                </span>
              </div>
              <div className="flex items-center gap-3 text-slate-600">
                <span>Easy: <strong className="text-emerald-700">{topicSummary.difficulty_breakdown.Easy || 0}</strong></span>
                <span>Medium: <strong className="text-amber-700">{topicSummary.difficulty_breakdown.Medium || 0}</strong></span>
                <span>Hard: <strong className="text-purple-700">{topicSummary.difficulty_breakdown.Hard || 0}</strong></span>
              </div>
            </div>
          )}
        </div>

        {/* Section 2: Question Count & Format */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <label className="text-[13px] font-semibold text-slate-800">
              Question Count
            </label>
            <div className="flex gap-1.5">
              {[5, 10, 15, 20, 25, 30].map((cnt) => (
                <button
                  key={cnt}
                  type="button"
                  onClick={() => handleQuestionCountChange(cnt)}
                  className={`flex-1 py-1.5 rounded-md text-[12.5px] font-semibold transition-colors cursor-pointer ${
                    questionCount === cnt
                      ? 'bg-blue-600 text-white shadow-xs'
                      : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                  }`}
                >
                  {cnt}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-[13px] font-semibold text-slate-800">
              Presentation Format
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setQuestionTypes(['SLIDE_QA'])}
                className={`flex-1 py-1.5 rounded-md text-[12px] font-semibold transition-colors cursor-pointer ${
                  questionTypes.includes('SLIDE_QA') && !questionTypes.includes('MULTIPLE_CHOICE')
                    ? 'bg-blue-600 text-white shadow-xs'
                    : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                Q&A Slide Pairs
              </button>
              <button
                type="button"
                onClick={() => setQuestionTypes(['MULTIPLE_CHOICE'])}
                className={`flex-1 py-1.5 rounded-md text-[12px] font-semibold transition-colors cursor-pointer ${
                  questionTypes.includes('MULTIPLE_CHOICE')
                    ? 'bg-blue-600 text-white shadow-xs'
                    : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                }`}
              >
                4-Option MCQ
              </button>
            </div>
          </div>
        </div>

        {/* Section 3: Difficulty Distribution */}
        <div className="space-y-3 rounded-lg border border-slate-200/80 bg-white p-4">
          <div className="flex items-center justify-between">
            <label className="text-[13px] font-semibold text-slate-800">
              Difficulty Distribution
            </label>
            <span
              className={`text-[12px] font-semibold ${
                isDistributionValid ? 'text-emerald-600' : 'text-rose-600'
              }`}
            >
              {totalAllocated} / {questionCount} Allocated
            </span>
          </div>

          <div className="flex gap-2">
            {(['Balanced', 'Easy-heavy', 'Hard-heavy'] as DifficultyPreset[]).map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => handlePresetChange(preset)}
                className={`px-3 py-1 text-[12px] rounded-md font-medium transition-colors cursor-pointer ${
                  difficultyPreset === preset
                    ? 'bg-slate-900 text-white'
                    : 'border border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100'
                }`}
              >
                {preset}
              </button>
            ))}
          </div>

          {/* Granular Sliders / Number Controls */}
          <div className="grid grid-cols-3 gap-3 pt-2">
            <div className="rounded-md border border-emerald-100 bg-emerald-50/30 p-2.5 text-center">
              <div className="text-[11px] font-bold text-emerald-700 uppercase tracking-wider">
                Easy
              </div>
              <input
                type="number"
                min={0}
                max={questionCount}
                value={easyCount}
                onChange={(e) => handleDistributionInput('easy', parseInt(e.target.value))}
                className="mt-1 w-full text-center text-[16px] font-bold text-slate-900 bg-transparent border-0 focus:ring-0"
              />
            </div>

            <div className="rounded-md border border-amber-100 bg-amber-50/30 p-2.5 text-center">
              <div className="text-[11px] font-bold text-amber-700 uppercase tracking-wider">
                Medium
              </div>
              <input
                type="number"
                min={0}
                max={questionCount}
                value={mediumCount}
                onChange={(e) => handleDistributionInput('medium', parseInt(e.target.value))}
                className="mt-1 w-full text-center text-[16px] font-bold text-slate-900 bg-transparent border-0 focus:ring-0"
              />
            </div>

            <div className="rounded-md border border-purple-100 bg-purple-50/30 p-2.5 text-center">
              <div className="text-[11px] font-bold text-purple-700 uppercase tracking-wider">
                Hard
              </div>
              <input
                type="number"
                min={0}
                max={questionCount}
                value={hardCount}
                onChange={(e) => handleDistributionInput('hard', parseInt(e.target.value))}
                className="mt-1 w-full text-center text-[16px] font-bold text-slate-900 bg-transparent border-0 focus:ring-0"
              />
            </div>
          </div>
        </div>

        {/* Section 4: Quizmaster Host Personality */}
        <div className="space-y-3 rounded-lg border border-slate-200/80 bg-white p-4">
          <div className="flex items-center justify-between">
            <label className="text-[13px] font-semibold text-slate-800 flex items-center gap-1.5">
              <Sparkles className="h-4 w-4 text-amber-500" />
              Quizmaster Personality
            </label>
            <span className="text-[12px] text-slate-400">
              Adapts tone, clues &amp; host discussion style
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {[
              {
                id: 'DEFAULT',
                title: 'Standard Quizmaster',
                desc: 'Direct, balanced, professional competition tone',
              },
              {
                id: 'CURIOSITY_STORYTELLER',
                title: 'Curiosity Storyteller',
                desc: 'Narrative storytelling with rich context & curiosity hooks',
              },
              {
                id: 'DETECTIVE_PUZZLER',
                title: 'Detective Puzzler',
                desc: 'Mystery clues, step-by-step logic, and deduction riddles',
              },
              {
                id: 'TOURNAMENT_PRO',
                title: 'Tournament Pro',
                desc: 'High-energy, buzzer-ready, precise contest phrasing',
              },
              {
                id: 'SOCRATIC_EXPLORER',
                title: 'Socratic Explorer',
                desc: 'Inquiry-driven questions with discussion prompts for debate',
              },
            ].map((p) => {
              const active = personality === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setPersonality(p.id)}
                  className={`flex flex-col items-start p-2.5 rounded-lg border text-left transition-all cursor-pointer ${
                    active
                      ? 'border-blue-600 bg-blue-50/50 ring-1 ring-blue-500/20'
                      : 'border-slate-200 bg-slate-50/50 hover:bg-slate-50 hover:border-slate-300'
                  }`}
                >
                  <span
                    className={`text-[12.5px] font-semibold ${
                      active ? 'text-blue-700' : 'text-slate-800'
                    }`}
                  >
                    {p.title}
                  </span>
                  <span className="text-[11px] text-slate-500 mt-0.5 leading-snug line-clamp-2">
                    {p.desc}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {error && (
          <div className="rounded-lg bg-rose-50 border border-rose-200 p-3 text-[13px] text-rose-700 flex items-start gap-2">
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Submit Action */}
        <button
          type="submit"
          disabled={loading || !isDistributionValid}
          className="w-full rounded-lg bg-blue-600 py-3 text-[14px] font-semibold text-white shadow-sm shadow-blue-500/20 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all cursor-pointer flex items-center justify-center gap-2"
        >
          {loading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Compiling Quiz from Database...</span>
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" />
              <span>Compile {questionCount}-Question Quiz</span>
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </form>
    </div>
  );
}
