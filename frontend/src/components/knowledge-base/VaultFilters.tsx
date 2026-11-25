import { useMemo, useRef } from 'react';
import { Search, Filter, Tag, ArrowUpDown } from 'lucide-react';
import { TopicItem, DocumentItem, SourceCatalog } from '../../lib/types';
import { FORMAT_FILTER_OPTIONS } from '../../lib/formatPayloads';
import SourceMultiPicker, { formatDeckDisplayName } from '../SourceMultiPicker';
import VoiceInputButton from '../VoiceInputButton';
import { buildVocabulary } from '../../lib/voiceVocabulary';

// The search box + filter bar on the Knowledge Base page (QShala priority plan P3-1). Purely
// controlled: owns no state of its own, just renders the current filter values and reports
// changes back up. Extracted unchanged from knowledge-base/page.tsx's "Search & Filters Bar"
// section -- a change here can no longer accidentally touch row-rendering or data-fetching code.
export interface VaultFiltersProps {
  query: string;
  setQuery: (v: string) => void;
  matchMode: 'exact' | 'smart';
  setMatchMode: (v: 'exact' | 'smart') => void;
  onSearchSubmit: (e: React.FormEvent) => void;
  topic: string;
  setTopic: (v: string) => void;
  subtopic: string;
  setSubtopic: (v: string) => void;
  tagFilter: string;
  setTagFilter: (v: string) => void;
  difficulty: string;
  setDifficulty: (v: string) => void;
  formatFilter: string;
  setFormatFilter: (v: string) => void;
  slotFilter: string;
  setSlotFilter: (v: string) => void;
  selectedDeckIds: string[];
  setSelectedDeckIds: (v: string[]) => void;
  gradeMin: number | '';
  setGradeMin: (v: number | '') => void;
  sortBy: string;
  setSortBy: (v: string) => void;
  sortOrder: 'asc' | 'desc';
  setSortOrder: (v: 'asc' | 'desc') => void;
  topicsList: TopicItem[];
  documentsList: DocumentItem[];
  sourceCatalog: SourceCatalog | null;
  availableSubtopics: string[];
}

export default function VaultFilters({
  query, setQuery, matchMode, setMatchMode, onSearchSubmit,
  topic, setTopic,
  subtopic, setSubtopic,
  tagFilter, setTagFilter,
  difficulty, setDifficulty,
  formatFilter, setFormatFilter,
  slotFilter, setSlotFilter,
  selectedDeckIds, setSelectedDeckIds,
  gradeMin, setGradeMin,
  sortBy, setSortBy,
  sortOrder, setSortOrder,
  topicsList, documentsList, sourceCatalog, availableSubtopics,
}: VaultFiltersProps) {
  const formRef = useRef<HTMLFormElement>(null);

  const effectiveCatalog: SourceCatalog = useMemo(() => sourceCatalog || {
    series: [],
    decks: documentsList.map((d) => ({
      id: d.id,
      title: formatDeckDisplayName(d.filename, d.title),
      filename: d.filename,
      series: formatDeckDisplayName(d.filename, d.title),
      series_key: d.id,
      year: d.year ?? null,
      question_count: d.question_count ?? 0,
      created_at: d.uploaded_at ?? null,
    })),
  }, [sourceCatalog, documentsList]);

  const effectiveVocab = useMemo(
    () => buildVocabulary(effectiveCatalog),
    [effectiveCatalog]
  );

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-3 sm:p-3.5 shadow-sm shadow-slate-100/50 space-y-2.5">
      <form ref={formRef} onSubmit={onSearchSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400 pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search questions, answers, topics, or presentation deck name..."
            className="w-full h-[34px] rounded-lg border border-slate-200 bg-white py-1.5 pl-9 pr-9 text-[13px] font-normal text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center">
            <VoiceInputButton
              mode="search"
              vocabulary={effectiveVocab}
              buttonSize="sm"
              onInterim={(interim) => {
                setQuery(interim);
              }}
              onText={(cleanText) => {
                setQuery(cleanText);
                setTimeout(() => {
                  if (formRef.current) {
                    if (typeof formRef.current.requestSubmit === 'function') {
                      formRef.current.requestSubmit();
                    } else {
                      formRef.current.dispatchEvent(
                        new Event('submit', { cancelable: true, bubbles: true })
                      );
                    }
                  }
                }, 0);
              }}
            />
          </div>
        </div>

        <button
          type="submit"
          className="h-[34px] rounded-lg bg-slate-900 px-3.5 py-1.5 text-[12px] font-semibold text-white hover:bg-slate-800 transition-colors cursor-pointer shrink-0"
        >
          Search
        </button>
      </form>

      {/* Match mode chips and Sorting on one tidy row */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
        <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="Match mode">
          <span className="font-medium text-slate-600">Match:</span>
          {([['exact', 'Exact words'], ['smart', 'Smart (related)']] as const).map(([val, label]) => (
            <button
              key={val}
              type="button"
              role="radio"
              aria-checked={matchMode === val}
              onClick={() => setMatchMode(val)}
              className={`rounded-full border px-2 py-0.5 font-semibold text-[11px] transition-colors cursor-pointer ${
                matchMode === val ? 'border-amber-400 bg-amber-50 text-slate-900' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
              }`}
            >
              {label}
            </button>
          ))}
          <span className="hidden sm:inline text-slate-400 text-[10.5px]">
            {matchMode === 'exact' ? 'Only questions containing every word you type.' : 'Also returns loosely related questions.'}
          </span>
        </div>

        {/* Sorting controls placed compactly with Match line */}
        <div className="flex items-center gap-1.5 ml-auto">
          <ArrowUpDown className="h-3 w-3 text-slate-400" />
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="h-[28px] rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11.5px] font-normal text-slate-700 focus:outline-none cursor-pointer"
          >
            <option value="created_at">Date Added</option>
            <option value="difficulty_score">Difficulty Score</option>
            <option value="curiosity_score">Curiosity Score</option>
            <option value="occurrence_count">Occurrences</option>
          </select>

          <button
            type="button"
            onClick={() => setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')}
            className="h-[28px] px-2 rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 text-[11px] font-semibold cursor-pointer"
            title="Toggle sort direction"
          >
            {sortOrder.toUpperCase()}
          </button>
        </div>
      </div>

      {/* Dynamic Filters Row */}
      <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-100 text-[12px]">
        <div className="flex items-center gap-1 text-slate-500 font-medium text-[11.5px]">
          <Filter className="h-3 w-3" />
          <span>Filters:</span>
        </div>

        {/* Presentation Deck / Series Source Filter */}
        <SourceMultiPicker
          catalog={effectiveCatalog}
          selectedIds={selectedDeckIds}
          onChange={setSelectedDeckIds}
          variant="filter"
          placeholder={`All Presentations / Decks (${effectiveCatalog.decks.length})`}
        />

        {/* Dynamic Topics Dropdown */}
        <select
          value={topic}
          onChange={(e) => {
            setTopic(e.target.value);
            setSubtopic('');
          }}
          className="h-[32px] rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] font-normal text-slate-700 focus:outline-none cursor-pointer"
        >
          <option value="">All Topics ({topicsList.reduce((a, b) => a + b.count, 0)})</option>
          {topicsList.map((t) => (
            <option key={t.topic} value={t.topic}>
              {t.topic} ({t.count})
            </option>
          ))}
        </select>

        {/* Subtopic Filter (if available) */}
        {availableSubtopics.length > 0 && (
          <select
            value={subtopic}
            onChange={(e) => setSubtopic(e.target.value)}
            className="h-[32px] rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] font-normal text-slate-700 focus:outline-none cursor-pointer"
          >
            <option value="">All Subtopics</option>
            {availableSubtopics.map((sub) => (
              <option key={sub} value={sub}>
                {sub}
              </option>
            ))}
          </select>
        )}

        {/* Difficulty */}
        <select
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value)}
          className="h-[32px] rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] font-normal text-slate-700 focus:outline-none cursor-pointer"
        >
          <option value="">All Difficulties</option>
          <option value="Easy">Easy</option>
          <option value="Medium">Medium</option>
          <option value="Hard">Hard</option>
        </select>

        {/* Format Filter */}
        <select
          value={formatFilter}
          onChange={(e) => setFormatFilter(e.target.value)}
          className="h-[32px] rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] font-normal text-slate-700 focus:outline-none cursor-pointer"
        >
          <option value="">All Formats</option>
          {FORMAT_FILTER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
          <option value="BUZZER">Buzzer (Closing Bell)</option>
          <option value="VISUAL_CLUE">Text + Image</option>
          <option value="VIDEO_CLUE">Text + Video</option>
          <option value="AUDIO_CLUE">Text + Audio</option>
        </select>

        {/* Slot: live questions, reserve spares, or the example slides */}
        <select
          value={slotFilter}
          onChange={(e) => setSlotFilter(e.target.value)}
          className="h-[32px] rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] font-normal text-slate-700 focus:outline-none cursor-pointer"
          aria-label="Slot"
        >
          <option value="">Live + Reserve + Example</option>
          <option value="LIVE">Live only</option>
          <option value="RESERVE">Reserve only</option>
          <option value="EXAMPLE">Example only</option>
        </select>

        {/* Tag Filter */}
        <div className="relative">
          <Tag className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-slate-400" />
          <input
            type="text"
            placeholder="Filter by tag..."
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value.replace(/^#+/, ''))}
            className="h-[32px] rounded-md border border-slate-200 bg-white pl-6 pr-5 py-1 text-[12px] font-normal text-slate-700 placeholder-slate-400 focus:outline-none w-[125px]"
          />
          {tagFilter && (
            <button
              type="button"
              onClick={() => setTagFilter('')}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 text-[12px] font-bold"
              title="Clear tag filter"
            >
              &times;
            </button>
          )}
        </div>

        {(query || topic || subtopic || tagFilter || difficulty || formatFilter || slotFilter || gradeMin || selectedDeckIds.length > 0) && (
          <button
            type="button"
            onClick={() => {
              setQuery('');
              setTopic('');
              setSubtopic('');
              setTagFilter('');
              setDifficulty('');
              setFormatFilter('');
              setSlotFilter('');
              setGradeMin('');
              setSelectedDeckIds([]);
            }}
            className="text-[12px] text-blue-600 hover:underline cursor-pointer font-medium ml-1"
          >
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
