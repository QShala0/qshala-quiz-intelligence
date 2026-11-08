'use client';

import { useState, useCallback, Suspense } from 'react';
import dynamic from 'next/dynamic';
import { useSearchParams } from 'next/navigation';
import { AlertTriangle, Eye, X } from 'lucide-react';
import { resolveMediaUrl, getFallbackMediaUrl } from '../../lib/api';
import { HistoricalQuestion } from '../../lib/types';
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
  const [selectedDuplicateForReview, setSelectedDuplicateForReview] = useState<HistoricalQuestion | null>(null);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [imgFallbackUrls, setImgFallbackUrls] = useState<Record<string, string>>({});

  const {
    activeTab, setActiveTab,
    duplicateCount,
    reviewCount,
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
    handleSearch,
    handleQuestionTagsSaved, handleAcceptReview, handleResolveDuplicate,
    handleQuestionUpdated, handleConfirmFormat,
  } = useVaultQuestions(initialTopic, setSelectedDuplicateForReview);

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
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b border-slate-200/60 pb-5 gap-4">
        <div>
          <h1 className="text-[28px] sm:text-[32px] font-bold tracking-tight text-slate-900 leading-tight">
            Questions
          </h1>
          <p className="mt-1 text-[14px] text-slate-500 font-normal leading-relaxed">
            Verified, structured, and deduplicated questions across all historical QShala tournaments.
          </p>
        </div>

        {/* View Tabs */}
        <div className="flex items-center bg-slate-100 p-1 rounded-lg border border-slate-200/80">
          <button
            type="button"
            onClick={() => setActiveTab('all')}
            className={`px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
              activeTab === 'all'
                ? 'bg-white text-slate-900 shadow-sm'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            All Questions
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('duplicates')}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
              activeTab === 'duplicates'
                ? 'bg-amber-500 text-white shadow-sm'
                : 'text-amber-700 hover:text-amber-800'
            }`}
          >
            <AlertTriangle className="h-3.5 w-3.5" />
            <span>Possible Duplicates</span>
            {duplicateCount > 0 && (
              <span className={`px-1.5 py-0.2 rounded-full text-[11px] font-bold ${
                activeTab === 'duplicates' ? 'bg-white text-amber-600' : 'bg-amber-200 text-amber-900'
              }`}>
                {duplicateCount}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('review')}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-[13px] font-semibold transition-all cursor-pointer ${
              activeTab === 'review'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-indigo-700 hover:text-indigo-800'
            }`}
          >
            <Eye className="h-3.5 w-3.5" />
            <span>Needs Review</span>
            {reviewCount > 0 && (
              <span className={`px-1.5 py-0.2 rounded-full text-[11px] font-bold ${
                activeTab === 'review' ? 'bg-white text-indigo-600' : 'bg-indigo-200 text-indigo-900'
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
        onSearchSubmit={handleSearch}
        topic={topic} setTopic={setTopic}
        subtopic={subtopic} setSubtopic={setSubtopic}
        tagFilter={tagFilter} setTagFilter={setTagFilter}
        difficulty={difficulty} setDifficulty={setDifficulty}
        formatFilter={formatFilter} setFormatFilter={setFormatFilter}
        slotFilter={slotFilter} setSlotFilter={setSlotFilter}
        selectedDeckId={selectedDeckId} setSelectedDeckId={setSelectedDeckId}
        gradeMin={gradeMin} setGradeMin={setGradeMin}
        sortBy={sortBy} setSortBy={setSortBy}
        sortOrder={sortOrder} setSortOrder={setSortOrder}
        topicsList={topicsList}
        documentsList={documentsList}
        availableSubtopics={availableSubtopics}
      />

      {/* Professional CMS Data Table */}
      <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm shadow-slate-100/50 overflow-hidden">
        {loading ? (
          <TableSkeleton columns={3} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px] border-collapse">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/60 font-semibold text-[12px] uppercase tracking-wider text-slate-600">
                  <th className="py-3 px-4 w-6/12">Question</th>
                  <th className="py-3 px-4">Topics</th>
                  <th className="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {questions.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="py-12 text-center text-slate-400">
                      {activeTab === 'duplicates'
                        ? 'No possible duplicates pending review!'
                        : activeTab === 'review'
                        ? 'Nothing needs review — every tag is confirmed!'
                        : 'No questions match your current search and filters.'}
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
                      setSelectedQuestionForTags={setSelectedQuestionForTags}
                      setSelectedDuplicateForReview={setSelectedDuplicateForReview}
                      handleViewSlide={handleViewSlide}
                      handleAcceptReview={handleAcceptReview}
                      handleConfirmFormat={handleConfirmFormat}
                      onQuestionUpdated={handleQuestionUpdated}
                      resolvingId={resolvingId}
                      tagFilter={tagFilter}
                      setTagFilter={setTagFilter}
                    />
                  ))
                )}
              </tbody>
            </table>
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
          onFilterDeck={(id) => setSelectedDeckId(id)}
          availableTopics={topicsList.map((t) => t.topic)}
        />
      )}

      {selectedQuestionForMedia && (
        <MediaViewerModal
          question={selectedQuestionForMedia}
          onClose={() => setSelectedQuestionForMedia(null)}
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
