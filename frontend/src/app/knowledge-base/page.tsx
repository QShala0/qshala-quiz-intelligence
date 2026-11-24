'use client';

import { useState, useCallback, Suspense } from 'react';
import dynamic from 'next/dynamic';
import { useSearchParams } from 'next/navigation';
import { AlertTriangle, Eye, RefreshCw, X } from 'lucide-react';
import { resolveMediaUrl, getFallbackMediaUrl } from '../../lib/api';
import { HistoricalQuestion } from '../../lib/types';
import QT from '../../components/ui/QT';
// Performance backlog item 5 (QSHALA_PERFORMANCE_IDEAS.md): these three only ever render once a
// user clicks into a slide/tag/media view, but were imported eagerly into the page bundle. All
// three are already conditionally rendered below ({selected... && <Modal/>}), so swapping to
// next/dynamic trims first-load JS with no change to the render logic. ssr: false is safe here --
// each only opens from a client-side click, never needed on the initial server render.
const SlideViewerModal = dynamic(() => import('../../components/SlideViewerModal'), { ssr: false });
const TagManagerModal = dynamic(() => import('../../components/TagManagerModal'), { ssr: false });
const MediaViewerModal = dynamic(() => import('../../components/MediaViewerModal'), { ssr: false });
import TableSkeleton from '../../components/TableSkeleton';
import VaultFilters from '../../components/knowledge-base/VaultFilters';
import QuestionRow from '../../components/knowledge-base/QuestionRow';
const DuplicateReviewModal = dynamic(() => import('../../components/knowledge-base/DuplicateReviewModal'), { ssr: false });
import { useVaultQuestions } from '../../hooks/useVaultQuestions';
import { isPlaceholderIcon } from '../../lib/placeholderIcons';

function KnowledgeBaseContent() {
  const searchParams = useSearchParams();
  const initialTopic = searchParams.get('topic') || '';
  const initialDeck = searchParams.get('deck') || '';   // the Upload Center links here while a deck is still being tagged

  const [selectedSlide, setSelectedSlide] = useState<{
    document_title?: string | null;
    slide_number?: number | null;
    extracted_text?: string;
    speaker_notes?: string | null;
    slide_type?: string;
    image_paths?: string[];
    question?: HistoricalQuestion | null;
  } | null>(null);
  const [selectedQuestionForTags, setSelectedQuestionForTags] = useState<HistoricalQuestion | null>(null);
  const [selectedQuestionForMedia, setSelectedQuestionForMedia] = useState<HistoricalQuestion | null>(null);
  const [questionForImageEdit, setQuestionForImageEdit] = useState<HistoricalQuestion | null>(null);
  const [selectedDuplicateForReview, setSelectedDuplicateForReview] = useState<HistoricalQuestion | null>(null);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [imgFallbackUrls, setImgFallbackUrls] = useState<Record<string, string>>({});

  const {
    activeTab, setActiveTab,
    duplicateCount,
    reviewCount,
    query, setQuery,
    matchMode, setMatchMode, appliedQuery,
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
    questions, loading, loadError, resolvingId,
    total, hasMore, loadingMore, loadMoreError, loadMore,
    retryLoad, handleSearch,
    handleQuestionTagsSaved, handleAcceptReview, handleResolveDuplicate,
    handleQuestionUpdated, handleConfirmFormat, handleQuestionDeleted,
  } = useVaultQuestions(initialTopic, setSelectedDuplicateForReview, initialDeck);

  const getImageSrc = useCallback((rawUrl: string | null | undefined) => {
    if (!rawUrl) return '';
    if (imgFallbackUrls[rawUrl]) {
      return imgFallbackUrls[rawUrl];
    }
    return resolveMediaUrl(rawUrl);
  }, [imgFallbackUrls]);

  const handleImgError = useCallback((rawUrl: string) => {
    const current = resolveMediaUrl(rawUrl);
    const nextFallback = getFallbackMediaUrl(imgFallbackUrls[rawUrl] || current);
    if (nextFallback && nextFallback !== (imgFallbackUrls[rawUrl] || current)) {
      setImgFallbackUrls((prev) => ({ ...prev, [rawUrl]: nextFallback }));
    }
  }, [imgFallbackUrls]);

  const handleViewSlide = useCallback((q: HistoricalQuestion) => {
    const rawQImgs = (q.question_images && q.question_images.length > 0)
      ? q.question_images
      : (q.image_refs || []);
    const qImgs = rawQImgs.filter(x => !isPlaceholderIcon(x));
    const ansImgs = (q.answer_images || []).filter(x => !isPlaceholderIcon(x));
    const allSlideImgs = Array.from(new Set([...qImgs, ...ansImgs]));

    setSelectedSlide({
      document_title: q.document_title,
      slide_number: q.slide_number,
      extracted_text: `${q.question_text}\n\n${(q.options || []).join('\n')}\n\nAnswer: ${q.answer}`,
      speaker_notes: q.explanation,
      slide_type: 'QUESTION',
      image_paths: allSlideImgs,
      question: q,
    });
  }, []);

  return (
    <div className="p-6 sm:p-8 lg:p-10 max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b-2 border-sun pb-5 gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-[28px] sm:text-[32px] font-black tracking-tight text-ink leading-tight">
              Questions
            </h1>
            <QT
              pose="sherlock"
              height={36}
              interactive={true}
              speechBubble="Detective QT on the quest for the best trivia!"
              bubblePosition="top-right"
              alt="Detective QT"
            />
          </div>
          <p className="mt-1 text-[14px] text-slate-500 font-normal leading-relaxed">
            Verified, structured, and deduplicated questions across all historical QShala tournaments.
          </p>
        </div>

        {/* View Tabs */}
        <div className="flex items-center bg-slate-100 p-0.5 rounded-xl border border-slate-200/80 gap-0.5">
          <button
            type="button"
            onClick={() => setActiveTab('all')}
            className={`px-3 py-1.5 rounded-lg text-[12px] font-bold transition-all cursor-pointer whitespace-nowrap ${
              activeTab === 'all'
                ? 'bg-sky-100 text-ink shadow-sticker-sm'
                : 'text-slate-700 hover:text-ink'
            }`}
          >
            All Questions
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('duplicates')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-bold transition-all cursor-pointer whitespace-nowrap ${
              activeTab === 'duplicates'
                ? 'bg-ink text-white shadow-sticker-sm'
                : 'text-amber-800 hover:text-ink'
            }`}
          >
            <AlertTriangle className="h-3 w-3 shrink-0" />
            <span>Possible Duplicates</span>
            {duplicateCount > 0 && (
              <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold leading-none ${
                activeTab === 'duplicates' ? 'bg-sky-100 text-ink' : 'bg-amber-200 text-amber-900'
              }`}>
                {duplicateCount}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('review')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-bold transition-all cursor-pointer whitespace-nowrap ${
              activeTab === 'review'
                ? 'bg-sky text-ink shadow-sticker-sm'
                : 'text-sky-800 hover:text-ink'
            }`}
          >
            <Eye className="h-3 w-3 shrink-0" />
            <span>Needs Review</span>
            {reviewCount > 0 && (
              <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold leading-none ${
                activeTab === 'review' ? 'bg-ink text-white' : 'bg-sky-200 text-sky-900'
              }`}>
                {reviewCount}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Search & Filters Bar */}
      <VaultFilters
        query={query} setQuery={setQuery}
        matchMode={matchMode} setMatchMode={setMatchMode}
        onSearchSubmit={handleSearch}
        topic={topic} setTopic={setTopic}
        subtopic={subtopic} setSubtopic={setSubtopic}
        tagFilter={tagFilter} setTagFilter={setTagFilter}
        difficulty={difficulty} setDifficulty={setDifficulty}
        formatFilter={formatFilter} setFormatFilter={setFormatFilter}
        slotFilter={slotFilter} setSlotFilter={setSlotFilter}
        selectedDeckIds={selectedDeckIds} setSelectedDeckIds={setSelectedDeckIds}
        gradeMin={gradeMin} setGradeMin={setGradeMin}
        sortBy={sortBy} setSortBy={setSortBy}
        sortOrder={sortOrder} setSortOrder={setSortOrder}
        topicsList={topicsList}
        documentsList={documentsList}
        sourceCatalog={sourceCatalog}
        availableSubtopics={availableSubtopics}
      />

      {/* Error Banner if question bank request fails or times out */}
      {loadError && !loading && (
        <div className="mb-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/90 px-4 py-3 text-[13.5px] text-amber-900">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
            <div>
              <span className="font-semibold">Unable to load the question bank:</span>{' '}
              <span className="text-amber-800">{loadError}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={retryLoad}
            className="inline-flex items-center gap-1.5 self-start sm:self-auto rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-[12.5px] font-medium text-amber-900 hover:bg-amber-100/70 transition-colors cursor-pointer"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      )}

      {/* Professional CMS Data Table */}
      <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
        {loading ? (
          <TableSkeleton columns={3} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] table-fixed text-left text-[13px] border-collapse">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/60 font-semibold text-[12px] uppercase tracking-wider text-slate-600">
                  <th className="py-3 px-4 w-[56%]">Question</th>
                  <th className="py-3 pl-9 pr-4 w-[26%]">Topics</th>
                  <th className="py-3 px-4 w-[18%] text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {questions.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="py-12 text-center text-slate-500">
                      {loadError ? (
                        <div className="space-y-2">
                          <p className="font-medium text-slate-700">Could not reach the question bank API.</p>
                          <p className="text-xs text-slate-500">{loadError}</p>
                          <button
                            type="button"
                            onClick={retryLoad}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
                          >
                            <RefreshCw className="h-3.5 w-3.5" />
                            Retry loading questions
                          </button>
                        </div>
                      ) : activeTab === 'duplicates' ? (
                        'No possible duplicates pending review!'
                      ) : activeTab === 'review' ? (
                        'Nothing needs review — every tag is confirmed!'
                      ) : (appliedQuery || query) ? (
                        <div className="flex flex-col items-center justify-center py-8 text-center space-y-2">
                          <div className="relative inline-block">
                            <QT
                              pose="sherlock"
                              height={58}
                              interactive={true}
                              motion="bob"
                              speechBubble="Detective QT looked everywhere — no clues found!"
                              bubblePosition="top"
                              alt="Detective QT searching for clues"
                            />
                          </div>
                          <p className="text-sm font-bold text-ink">
                            No questions contain &ldquo;{appliedQuery || query}&rdquo;. Detective QT looked everywhere.
                          </p>
                          <p className="text-xs text-slate-500">
                            Try checking for typos or searching with a broader word.
                          </p>
                        </div>
                      ) : (
                        <div className="flex flex-col items-center justify-center py-8 text-center space-y-2">
                          <QT
                            pose="thinking"
                            height={52}
                            interactive={true}
                            motion="breathe"
                            speechBubble="Paws for thought! Try relaxing your filters."
                            alt="QT thinking"
                          />
                          <p className="text-sm font-bold text-slate-700">
                            No questions match your current search and filters.
                          </p>
                        </div>
                      )}
                    </td>
                  </tr>
                ) : (
                  questions.map((q) => (
                    <QuestionRow
                      key={q.id}
                      q={q}
                      activeTab={activeTab}
                      getImageSrc={getImageSrc}
                      handleImgError={handleImgError}
                      setPreviewImage={setPreviewImage}
                      setSelectedQuestionForMedia={setSelectedQuestionForMedia}
                      onEditImages={setQuestionForImageEdit}
                      setSelectedQuestionForTags={setSelectedQuestionForTags}
                      setSelectedDuplicateForReview={setSelectedDuplicateForReview}
                      handleViewSlide={handleViewSlide}
                      handleAcceptReview={handleAcceptReview}
                      handleConfirmFormat={handleConfirmFormat}
                      onQuestionUpdated={handleQuestionUpdated}
                      onQuestionDeleted={handleQuestionDeleted}
                      resolvingId={resolvingId}
                      tagFilter={tagFilter}
                      setTagFilter={setTagFilter}
                      highlight={appliedQuery}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Paging footer: how much is shown, and a way to reach the rest */}
        {!loading && !loadError && questions.length > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/50 px-4 py-3 text-[13px] text-slate-600">
            <span aria-live="polite">
              {total !== null
                ? `Showing ${questions.length.toLocaleString()} of ${total.toLocaleString()} questions`
                : `Showing ${questions.length.toLocaleString()} questions${hasMore ? ' (more available)' : ''}`}
            </span>
            {loadMoreError && (
              <span role="alert" className="text-amber-800">
                {loadMoreError}
              </span>
            )}
            {hasMore && (
              <button
                type="button"
                onClick={loadMore}
                disabled={loadingMore}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-4 py-1.5 text-[13px] font-semibold text-slate-800 hover:bg-slate-50 disabled:opacity-60 transition-colors cursor-pointer"
              >
                {loadingMore ? 'Loading…' : loadMoreError ? 'Try again' : 'Load more'}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Duplicate Review & Resolution Modal */}
      {selectedDuplicateForReview && (
        <DuplicateReviewModal
          question={selectedDuplicateForReview}
          resolvingId={resolvingId}
          onClose={() => setSelectedDuplicateForReview(null)}
          onResolve={handleResolveDuplicate}
        />
      )}

      {selectedSlide && (
        <SlideViewerModal
          isOpen={!!selectedSlide}
          onClose={() => setSelectedSlide(null)}
          slide={selectedSlide}
        />
      )}

      {selectedQuestionForTags && (
        <TagManagerModal
          isOpen={!!selectedQuestionForTags}
          onClose={() => setSelectedQuestionForTags(null)}
          question={selectedQuestionForTags}
          onSaved={handleQuestionTagsSaved}
          onFilterDeck={(id) => setSelectedDeckIds(id ? [id] : [])}
          availableTopics={topicsList.map((t) => t.topic)}
        />
      )}

      {selectedQuestionForMedia && (
        <MediaViewerModal
          question={selectedQuestionForMedia}
          onClose={() => setSelectedQuestionForMedia(null)}
        />
      )}

      {questionForImageEdit && (
        <MediaViewerModal
          question={questionForImageEdit}
          startInEditMode
          onQuestionUpdated={handleQuestionUpdated}
          onClose={() => setQuestionForImageEdit(null)}
        />
      )}

      {/* Lightbox Image Zoom Modal */}
      {previewImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm p-4 cursor-pointer"
          onClick={() => setPreviewImage(null)}
        >
          <div
            className="relative max-w-4xl max-h-[90vh] bg-white rounded-xl shadow-2xl p-2 border border-slate-700 overflow-hidden cursor-default"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setPreviewImage(null)}
              className="absolute top-3 right-3 z-10 rounded-full bg-slate-900/70 p-1.5 text-white hover:bg-slate-900 transition-colors cursor-pointer"
            >
              <X className="h-5 w-5" />
            </button>
            <img
              src={getImageSrc(previewImage)}
              onError={() => previewImage && handleImgError(previewImage)}
              alt="Vault Visual Asset"
              className="max-h-[82vh] w-auto object-contain rounded-lg mx-auto"
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default function KnowledgeBasePage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-slate-400">Loading...</div>}>
      <KnowledgeBaseContent />
    </Suspense>
  );
}
