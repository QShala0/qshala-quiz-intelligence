import { Search, Filter, Tag, ArrowUpDown } from 'lucide-react';
import { TopicItem, DocumentItem } from '../../lib/types';
import { FORMAT_FILTER_OPTIONS } from '../../lib/formatPayloads';

// The search box + filter bar on the Knowledge Base page (QShala priority plan P3-1). Purely
// controlled: owns no state of its own, just renders the current filter values and reports
// changes back up. Extracted unchanged from knowledge-base/page.tsx's "Search & Filters Bar"
// section -- a change here can no longer accidentally touch row-rendering or data-fetching code.
export interface VaultFiltersProps {
  query: string;
  setQuery: (v: string) => void;
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
  selectedDeckId: string;
  setSelectedDeckId: (v: string) => void;
  gradeMin: number | '';
  setGradeMin: (v: number | '') => void;
  sortBy: string;
  setSortBy: (v: string) => void;
  sortOrder: 'asc' | 'desc';
  setSortOrder: (v: 'asc' | 'desc') => void;
  topicsList: TopicItem[];
  documentsList: DocumentItem[];
  availableSubtopics: string[];
}

export default function VaultFilters({
  query, setQuery, onSearchSubmit,
  topic, setTopic,
  subtopic, setSubtopic,
  tagFilter, setTagFilter,
  difficulty, setDifficulty,
  formatFilter, setFormatFilter,
  slotFilter, setSlotFilter,
  selectedDeckId, setSelectedDeckId,
  gradeMin, setGradeMin,
  sortBy, setSortBy,
  sortOrder, setSortOrder,
  topicsList, documentsList, availableSubtopics,
}: VaultFiltersProps) {
  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm shadow-slate-100/50 space-y-3">
      <form onSubmit={onSearchSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-3 h-4 w-4 text-slate-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search questions, answers, topics, or presentation deck name..."
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-10 pr-4 text-[14px] font-normal text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <button
          type="submit"
          className="rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-700 transition-colors cursor-pointer"
        >
          Search
        </button>
      </form>

      {/* Dynamic Filters & Sorting */}
      <div className="flex flex-wrap items-center gap-3 pt-2 border-t border-slate-100 text-[13px]">
        <div className="flex items-center gap-1.5 text-slate-500 font-medium">
          <Filter className="h-3.5 w-3.5" />
          <span>Filters:</span>
        </div>

        {/* Presentation Deck Filter */}
        <select
          value={selectedDeckId}
          onChange={(e) => setSelectedDeckId(e.target.value)}
          className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-blue-500 focus:outline-none cursor-pointer max-w-[210px] truncate"
          title="Filter by Presentation Deck"
        >
          <option value="">All Presentations / Decks ({documentsList.length})</option>
          {documentsList.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.title || doc.filename}
            </option>
          ))}
        </select>

        {/* Dynamic Topics Dropdown */}
        <select
          value={topic}
          onChange={(e) => {
            setTopic(e.target.value);
            setSubtopic('');
          }}
          className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-blue-500 focus:outline-none cursor-pointer"
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
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-blue-500 focus:outline-none cursor-pointer"
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
          className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-blue-500 focus:outline-none cursor-pointer"
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
          className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-indigo-500 focus:outline-none cursor-pointer"
        >
          <option value="">All Formats</option>
          {FORMAT_FILTER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
          <option value="BUZZER">Buzzer (Closing Bell)</option>
          <option value="VISUAL_CLUE">Visual Clue</option>
          <option value="VIDEO_CLUE">Video Clue</option>
          <option value="AUDIO_CLUE">Audio Clue</option>
        </select>

        {/* Slot: live questions, reserve spares, or the example slides */}
        <select
          value={slotFilter}
          onChange={(e) => setSlotFilter(e.target.value)}
          className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-indigo-500 focus:outline-none cursor-pointer"
          aria-label="Slot"
        >
          <option value="">Live + Reserve + Example</option>
          <option value="LIVE">Live only</option>
          <option value="RESERVE">Reserve only</option>
          <option value="EXAMPLE">Example only</option>
        </select>

        {/* Tag Filter */}
        <div className="relative">
          <Tag className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
          <input
            type="text"
            placeholder="Filter by tag..."
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value.replace(/^#+/, ''))}
            className="rounded-md border border-slate-200 bg-white pl-8 pr-6 py-1.5 text-[13px] font-normal text-slate-700 placeholder-slate-400 focus:border-indigo-500 focus:outline-none w-[140px]"
          />
          {tagFilter && (
            <button
              type="button"
              onClick={() => setTagFilter('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 text-[12px] font-bold"
              title="Clear tag filter"
            >
              &times;
            </button>
          )}
        </div>

        {/* Sorting */}
        <div className="flex items-center gap-1.5 ml-auto">
          <ArrowUpDown className="h-3.5 w-3.5 text-slate-400" />
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] font-normal text-slate-700 focus:border-blue-500 focus:outline-none cursor-pointer"
          >
            <option value="created_at">Date Added</option>
            <option value="difficulty_score">Difficulty Score</option>
            <option value="curiosity_score">Curiosity Score</option>
            <option value="occurrence_count">Occurrences</option>
          </select>

          <button
            type="button"
            onClick={() => setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')}
            className="px-2 py-1.5 rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 text-[12px] font-semibold cursor-pointer"
            title="Toggle sort direction"
          >
            {sortOrder.toUpperCase()}
          </button>
        </div>

        {(query || topic || subtopic || tagFilter || difficulty || formatFilter || slotFilter || gradeMin || selectedDeckId) && (
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
              setSelectedDeckId('');
            }}
            className="text-[13px] text-blue-600 hover:underline cursor-pointer font-medium"
          >
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
