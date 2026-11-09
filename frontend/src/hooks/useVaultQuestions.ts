import { useEffect, useRef, useState } from 'react';
import { api, isAbortError } from '../lib/api';
import { HistoricalQuestion, TopicItem, DocumentItem } from '../lib/types';
import { isPlaceholderIcon } from '../lib/placeholderIcons';

// Data-fetching + list-mutation logic for the Knowledge Base page (QShala priority plan P3-1).
// Extracted from knowledge-base/page.tsx's filter state, the two mount/topic-change effects,
// fetchQuestions, and the three handlers that mutate the `questions`/count state
// (handleQuestionTagsSaved, handleAcceptReview, handleResolveDuplicate).
//
// Design note on handleResolveDuplicate: in the original component it closes the page-level
// duplicate-review modal (setSelectedDuplicateForReview(null)) ONLY on success, and swallows
// errors without rethrowing -- a caller can't tell success from failure via try/catch, only by
// whether the modal closed. Reproducing that exactly (rather than a "cleaner" wrapper that
// rethrows) means this hook has to take the page's setSelectedDuplicateForReview setter as a
// parameter and call it itself, instead of trying to return a signal the page would act on.
export function useVaultQuestions(
  initialTopic: string,
  setSelectedDuplicateForReview: (q: HistoricalQuestion | null) => void
) {
  const [activeTab, setActiveTab] = useState<'all' | 'duplicates' | 'review'>('all');
  const [duplicateCount, setDuplicateCount] = useState<number>(0);
  const [reviewCount, setReviewCount] = useState<number>(0);

  const [query, setQuery] = useState('');
  const [topic, setTopic] = useState(initialTopic);
  const [subtopic, setSubtopic] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [difficulty, setDifficulty] = useState('');
  const [formatFilter, setFormatFilter] = useState('');
  const [slotFilter, setSlotFilter] = useState('');
  const [selectedDeckId, setSelectedDeckId] = useState<string>('');
  const [gradeMin, setGradeMin] = useState<number | ''>('');
  const [sortBy, setSortBy] = useState('created_at');
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc');

  const [topicsList, setTopicsList] = useState<TopicItem[]>([]);
  const [documentsList, setDocumentsList] = useState<DocumentItem[]>([]);
  const [availableSubtopics, setAvailableSubtopics] = useState<string[]>([]);
  const [questions, setQuestions] = useState<HistoricalQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  // Performance backlog item 4 (QSHALA_PERFORMANCE_IDEAS.md): every filter change fired a full
  // re-fetch without cancelling whatever fetch was already in flight. On a fast typist / fast
  // clicker, several stale requests could land out of order and the last *response to arrive*
  // (not the last filter set) would win, occasionally flashing stale results after the real
  // answer. Track the in-flight request so a new one can cancel it first.
  const inFlightRequest = useRef<AbortController | null>(null);

  // Fetch topics list, documents list, and duplicate count on mount
  useEffect(() => {
    const loadMetadata = async () => {
      try {
        const list = await api.getTopics();
        setTopicsList(list);
        const docs = await api.listDocuments();
        setDocumentsList(docs);
        const dupRes = await api.getDuplicateCount();
        setDuplicateCount(dupRes.count);
        try {
          setReviewCount(await api.getReviewCount());
        } catch { /* review count is best-effort */ }
      } catch (err) {
        console.error('Failed to load initial metadata:', err);
      }
    };
    loadMetadata();
  }, []);

  // When topic changes, load topic summary for subtopics
  useEffect(() => {
    if (topic) {
      api.getTopicSummary(topic)
        .then((summary) => setAvailableSubtopics(summary.subtopics || []))
        .catch(() => setAvailableSubtopics([]));
    } else {
      setAvailableSubtopics([]);
      setSubtopic('');
    }
  }, [topic]);

  const fetchQuestions = async () => {
    // Cancel whatever fetch is still in flight before starting a new one -- its response, if it
    // ever arrives, belongs to filters the user has already moved past.
    inFlightRequest.current?.abort();
    const controller = new AbortController();
    inFlightRequest.current = controller;

    setLoading(true);
    try {
      if (activeTab === 'review') {
        const data = await api.getReviewCandidates(controller.signal);
        setQuestions(data);
        setReviewCount(data.length);
        return;
      }
      const data = await api.searchQuestions({
        query: query.trim() || undefined,
        topic: topic || undefined,
        subtopic: subtopic || undefined,
        tag: tagFilter.trim() || undefined,
        difficulty: difficulty || undefined,
        question_format: formatFilter || undefined,
        slot: slotFilter || undefined,
        document_id: selectedDeckId || undefined,
        grade_min: gradeMin !== '' ? Number(gradeMin) : undefined,
        duplicate_status: activeTab === 'duplicates' ? 'POSSIBLE_DUPLICATE' : undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
        limit: 100,
        signal: controller.signal,
      });
      setQuestions(data);
    } catch (err) {
      // A superseded request aborting itself is expected traffic, not a failure -- don't log it
      // or flip off the loading state on behalf of the request that replaced it.
      if (isAbortError(err)) return;
      console.error(err);
    } finally {
      if (inFlightRequest.current === controller) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    fetchQuestions();
    // Abort on unmount too, not just on the next filter change, so a pending request can't set
    // state on an unmounted component.
    return () => inFlightRequest.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, topic, subtopic, tagFilter, difficulty, formatFilter, slotFilter, selectedDeckId, gradeMin, sortBy, sortOrder]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchQuestions();
  };

  const handleQuestionTagsSaved = (updated: HistoricalQuestion) => {
    // Editing tags confirms a human label, so the question leaves the review queue.
    if (activeTab === 'review') {
      setQuestions((prev) => prev.filter((item) => item.id !== updated.id));
      setReviewCount((prev) => Math.max(0, prev - 1));
      return;
    }
    setQuestions((prev) =>
      prev.map((item) => (item.id === updated.id ? { ...item, ...updated } : item))
    );
  };

  // A question was changed in place (e.g. its Arrange order was entered by hand): show the saved version, and in the
  // Needs Review tab refetch so it leaves the queue if nothing else is outstanding.
  const handleQuestionUpdated = (updated: HistoricalQuestion) => {
    setQuestions((prev) => prev.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)));
    if (activeTab === 'review') {
      fetchQuestions();
    }
  };

  // "Confirm reading": a person checked the flagged format reading.
  const handleConfirmFormat = async (questionId: string) => {
    setResolvingId(questionId);
    try {
      await api.acceptFormatReview(questionId);
      await fetchQuestions();
      try { setReviewCount(await api.getReviewCount()); } catch { /* best-effort */ }
    } catch (err) {
      console.error('Failed to confirm the format reading:', err);
    } finally {
      setResolvingId(null);
    }
  };

  const handleAcceptReview = async (questionId: string) => {
    setResolvingId(questionId);
    try {
      await api.acceptReview(questionId);
      setQuestions((prev) => prev.filter((q) => q.id !== questionId));
      setReviewCount((prev) => Math.max(0, prev - 1));
    } catch (err) {
      console.error('Failed to accept review question:', err);
    } finally {
      setResolvingId(null);
    }
  };

  const handleResolveDuplicate = async (questionId: string, action: 'CONFIRM_DUPLICATE' | 'DISMISS_UNIQUE') => {
    setResolvingId(questionId);
    try {
      await api.resolveDuplicate(questionId, action);
      setQuestions((prev) => prev.filter((q) => q.id !== questionId));
      setDuplicateCount((prev) => Math.max(0, prev - 1));
      setSelectedDuplicateForReview(null);
    } catch (err) {
      console.error('Failed to resolve duplicate:', err);
    } finally {
      setResolvingId(null);
    }
  };

  return {
    activeTab, setActiveTab,
    duplicateCount, setDuplicateCount,
    reviewCount, setReviewCount,
    query, setQuery,
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
    questions, loading, resolvingId,
    fetchQuestions, handleSearch,
    handleQuestionTagsSaved, handleAcceptReview, handleResolveDuplicate,
    handleQuestionUpdated, handleConfirmFormat,
  };
}

// Re-exported so page.tsx (and QuestionRow) can share the exact same filter without importing
// from two different places.
export { isPlaceholderIcon };
