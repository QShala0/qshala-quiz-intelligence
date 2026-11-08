import React from 'react';
import {
  AlertTriangle, Star, Play, Volume2, Image as ImageIcon, Video, Layers,
  CheckCircle, Tag, ExternalLink,
} from 'lucide-react';
import { HistoricalQuestion } from '../../lib/types';
import { isPlaceholderIcon } from '../../lib/placeholderIcons';
import QuestionFormatDisplay from './QuestionFormatDisplay';
import FormatReviewBar from '../formats/FormatReviewBar';
import { formatStyle, playModeLabel } from '../../lib/formatPayloads';

// A single question's <tr> in the Knowledge Base table (QShala priority plan P3-1). Named
// QuestionRow rather than the plan's suggested "QuestionCard" since it renders a table row, not a
// standalone card -- extracted unchanged (same markup, same conditions) from the big
// `questions.map((q) => ...)` block that used to live inline in knowledge-base/page.tsx.
export interface QuestionRowProps {
  q: HistoricalQuestion;
  activeTab: string;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage: (url: string) => void;
  setSelectedQuestionForMedia: (q: HistoricalQuestion) => void;
  setSelectedQuestionForTags: (q: HistoricalQuestion) => void;
  setSelectedDuplicateForReview: (q: HistoricalQuestion) => void;
  handleViewSlide: (q: HistoricalQuestion) => void;
  handleAcceptReview: (id: string) => void;
  handleConfirmFormat: (id: string) => void;
  onQuestionUpdated: (q: HistoricalQuestion) => void;
  resolvingId: string | null;
  tagFilter: string;
  setTagFilter: (v: string) => void;
}

function QuestionRow({
  q,
  activeTab,
  getImageSrc,
  handleImgError,
  setPreviewImage,
  setSelectedQuestionForMedia,
  setSelectedQuestionForTags,
  setSelectedDuplicateForReview,
  handleViewSlide,
  handleAcceptReview,
  handleConfirmFormat,
  onQuestionUpdated,
  resolvingId,
  tagFilter,
  setTagFilter,
}: QuestionRowProps) {
  const rawQImgs = (q.question_images && q.question_images.length > 0 ? q.question_images : q.image_refs || []);
  const cleanQImgs = rawQImgs.filter(x => !isPlaceholderIcon(x));
  const cleanAnsImgs = (q.answer_images || []).filter(x => !isPlaceholderIcon(x));
  const hasVideoClue = Boolean(
    ((q.video_refs && q.video_refs.length > 0 && !q.video_refs.some(r => r.toLowerCase().includes('timer') || q.question_format === 'ARRANGE_ORDER')) ||
     (q.question_videos && q.question_videos.length > 0 && !q.question_videos.some(r => r.toLowerCase().includes('timer') || q.question_format === 'ARRANGE_ORDER')) ||
     (q.raw_media_refs && q.raw_media_refs.some(r => (r.endsWith('.mp4') || r.endsWith('.webm') || r.endsWith('.mov')) && !r.toLowerCase().includes('timer') && q.question_format !== 'ARRANGE_ORDER')))
  );

  return (
    <tr className="hover:bg-slate-50/70 transition-colors group">
      <td className="py-3.5 px-4">
        {/* Duplicate Alert Banner */}
        {q.duplicate_status === 'POSSIBLE_DUPLICATE' && (
          <div className="mb-2 flex items-center justify-between rounded-md bg-amber-50 border border-amber-200 px-2.5 py-1.5 text-[11.5px] text-amber-800">
            <span className="flex items-center gap-1.5 font-semibold">
              <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
              Possible Duplicate ({Math.round((q.duplicate_similarity || 0.92) * 100)}% Match)
            </span>
            <button
              type="button"
              onClick={() => setSelectedDuplicateForReview(q)}
              className="rounded bg-amber-600 px-2 py-0.5 font-semibold text-white hover:bg-amber-700 transition-colors text-[11px] cursor-pointer"
            >
              Review Match
            </button>
          </div>
        )}

        <div className="flex items-start gap-1.5">
          {q.question_label && (
            <span className="mt-0.5 shrink-0 rounded bg-slate-100 border border-slate-200 px-1.5 py-0.5 text-[10.5px] font-bold text-slate-600">
              {q.question_label}
            </span>
          )}
          {q.question_format && (
            <span
              className={`mt-0.5 shrink-0 rounded-full border px-2 py-0.5 text-[10.5px] font-bold ${formatStyle(q.question_format).chip}`}
              title={`Format: ${q.question_format}`}
            >
              {formatStyle(q.question_format).label}
            </span>
          )}
          <div className="flex-1 min-w-0">
            <QuestionFormatDisplay
              q={q}
              getImageSrc={getImageSrc}
              setPreviewImage={setPreviewImage}
              handleImgError={handleImgError}
              onQuestionUpdated={onQuestionUpdated}
            />
          </div>
          {q.star_marked && (
            <span className="mt-0.5 inline-flex shrink-0 items-center gap-0.5 rounded bg-amber-50 border border-amber-200 px-1.5 py-0.5 text-[10.5px] font-bold text-amber-700">
              <Star className="h-3 w-3 fill-amber-400 text-amber-500" />Star
            </span>
          )}
        </div>

        {/* Where it sits in the quiz: slot, how it is played, the deck's own topic label */}
        {(q.slot && q.slot !== 'LIVE') || q.play_mode || q.topic_label ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10.5px] font-semibold" data-testid="row-meta">
            {q.slot && q.slot !== 'LIVE' && (
              <span className={`rounded-full border px-2 py-0.5 ${q.slot === 'RESERVE' ? 'bg-amber-50 border-amber-300 text-amber-800' : 'bg-slate-100 border-slate-300 text-slate-700'}`}>
                {q.slot === 'RESERVE' ? 'Reserve' : 'Example'}
              </span>
            )}
            {playModeLabel(q.play_mode, (q.format_payload as { team?: number | null } | null)?.team) && (
              <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-slate-600">
                {playModeLabel(q.play_mode, (q.format_payload as { team?: number | null } | null)?.team)}
              </span>
            )}
            {q.topic_label && <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-indigo-700">{q.topic_label}</span>}
          </div>
        ) : null}

        {(activeTab === 'review' || q.format_needs_review) && (
          <FormatReviewBar q={q} busy={resolvingId === q.id} onConfirm={handleConfirmFormat} />
        )}

        {/* Video Clue Prominent Button */}
        {hasVideoClue && (
          <div className="mt-2.5 flex items-center gap-2">
            <button
              type="button"
              onClick={() => setSelectedQuestionForMedia(q)}
              className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-violet-600 to-indigo-600 px-3 py-1.5 text-[12px] font-bold text-white shadow-xs hover:from-violet-700 hover:to-indigo-700 hover:shadow transition-all cursor-pointer group"
              title="Watch and hear embedded video clue"
            >
              <div className="flex h-5 w-5 items-center justify-center rounded-full bg-white/20 group-hover:scale-110 transition-transform">
                <Play className="h-3 w-3 fill-white text-white ml-0.5" />
              </div>
              <span>Watch Video Clue</span>
            </button>
          </div>
        )}

        {/* Clue and Answer Images Thumbnails (excluding placeholder camera icons) */}
        {(cleanQImgs.length > 0 || cleanAnsImgs.length > 0) && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {cleanQImgs.slice(0, 3).map((imgUrl, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setPreviewImage(imgUrl)}
                className="relative rounded-md overflow-hidden border border-slate-200 hover:border-blue-400 bg-white transition-all cursor-pointer group shadow-xs"
                title="Question image: click to view"
              >
                <img
                  src={getImageSrc(imgUrl)}
                  onError={() => handleImgError(imgUrl)}
                  alt={`Question image ${i + 1}`}
                  className="h-9 w-13 object-cover group-hover:scale-105 transition-transform"
                />
                <span className="absolute inset-x-0 bottom-0 bg-sky-700/80 text-center text-[8px] font-bold uppercase leading-3 text-white">Question</span>
              </button>
            ))}
            {cleanAnsImgs.slice(0, 2).map((imgUrl, i) => (
              <button
                key={`ans-${i}`}
                type="button"
                onClick={() => setPreviewImage(imgUrl)}
                className="relative rounded-md overflow-hidden border border-emerald-300 ring-1 ring-emerald-400/50 hover:border-emerald-500 bg-white transition-all cursor-pointer group shadow-xs"
                title="Answer image: click to view"
              >
                <img
                  src={getImageSrc(imgUrl)}
                  onError={() => handleImgError(imgUrl)}
                  alt={`Answer image ${i + 1}`}
                  className="h-9 w-13 object-cover group-hover:scale-105 transition-transform"
                />
                <span className="absolute inset-x-0 bottom-0 bg-emerald-700/80 text-center text-[8px] font-bold uppercase leading-3 text-white">Answer</span>
              </button>
            ))}
          </div>
        )}

        {/* Audio Clue Inline Player (if question has audio) */}
        {((q.question_audio && q.question_audio.length > 0) || (q.audio_refs && q.audio_refs.length > 0)) && (
          <div className="mt-2 flex items-center gap-2 p-1.5 rounded-lg bg-teal-50/80 border border-teal-200/80 max-w-sm">
            <Volume2 className="h-4 w-4 text-teal-600 shrink-0 ml-1" />
            <audio
              controls
              src={getImageSrc((q.question_audio && q.question_audio.length > 0 ? q.question_audio : q.audio_refs || [])[0])}
              onError={() => handleImgError((q.question_audio && q.question_audio.length > 0 ? q.question_audio : q.audio_refs || [])[0])}
              className="w-full h-7 text-xs accent-teal-600"
              preload="none"
            />
          </div>
        )}

        {/* Multimodal Badges: Images, Audio, Video */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {((cleanQImgs.length > 0) ||
            (cleanAnsImgs.length > 0) ||
            hasVideoClue ||
            (q.question_audio && q.question_audio.length > 0) ||
            (q.answer_audio && q.answer_audio.length > 0) ||
            (q.audio_refs && q.audio_refs.length > 0) ||
            q.visual_clues ||
            q.audio_transcript ||
            q.video_transcript ||
            (q.raw_media_refs && q.raw_media_refs.length > 0)) && (
            <button
              type="button"
              onClick={() => setSelectedQuestionForMedia(q)}
              className="inline-flex items-center gap-1.5 rounded-md bg-blue-50 hover:bg-blue-100 border border-blue-200 px-2 py-0.5 text-[11px] font-bold text-blue-700 transition-colors cursor-pointer shadow-2xs"
              title="Click to view slide images, audio, video & transcripts in a popup"
            >
              <ImageIcon className="h-3 w-3 text-blue-600" />
              <span>View Media</span>
              {cleanQImgs.length + cleanAnsImgs.length > 0 && (
                <span className="rounded-full bg-blue-200/80 text-blue-800 text-[10px] px-1 py-0.2 font-mono">
                  {cleanQImgs.length + cleanAnsImgs.length}
                </span>
              )}
              {((q.question_audio && q.question_audio.length > 0) || (q.audio_refs && q.audio_refs.length > 0) || q.audio_transcript) && (
                <Volume2 className="h-2.5 w-2.5 text-teal-600" />
              )}
              {hasVideoClue && <Video className="h-2.5 w-2.5 text-violet-600" />}
            </button>
          )}

          {hasVideoClue && (
            <button
              type="button"
              onClick={() => setSelectedQuestionForMedia(q)}
              className="inline-flex items-center gap-1 rounded bg-violet-50 hover:bg-violet-100 border border-violet-200 px-1.5 py-0.5 text-[10.5px] font-semibold text-violet-700 cursor-pointer"
              title="Click to watch embedded video clue"
            >
              <Video className="h-2.5 w-2.5" />
              Video Clue
            </button>
          )}

          {((q.question_audio && q.question_audio.length > 0) || (q.audio_refs && q.audio_refs.length > 0)) && (
            <button
              type="button"
              onClick={() => setSelectedQuestionForMedia(q)}
              className="inline-flex items-center gap-1 rounded bg-teal-50 hover:bg-teal-100 border border-teal-200 px-1.5 py-0.5 text-[10.5px] font-semibold text-teal-700 cursor-pointer"
              title="Click to play audio clue"
            >
              <Volume2 className="h-2.5 w-2.5" />
              Audio Clue
            </button>
          )}
          {q.answer_audio && q.answer_audio.length > 0 && (
            <button
              type="button"
              onClick={() => setSelectedQuestionForMedia(q)}
              className="inline-flex items-center gap-1 rounded bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 px-1.5 py-0.5 text-[10.5px] font-semibold text-emerald-700 cursor-pointer"
              title="Click to play answer reveal audio"
            >
              <Volume2 className="h-2.5 w-2.5" />
              Reveal Audio
            </button>
          )}

          {cleanQImgs.length + cleanAnsImgs.length > 0 && (
            <button
              type="button"
              onClick={() => setSelectedQuestionForMedia(q)}
              className="inline-flex items-center gap-1 rounded bg-sky-50 hover:bg-sky-100 border border-sky-200 px-1.5 py-0.5 text-[10.5px] font-semibold text-sky-700 cursor-pointer"
              title="Click to preview slide images"
            >
              <ImageIcon className="h-2.5 w-2.5" />
              {cleanQImgs.length + cleanAnsImgs.length} Images
            </button>
          )}
        {q.audio_transcript && (
          <button
            type="button"
            onClick={() => setSelectedQuestionForMedia(q)}
            className="inline-flex items-center gap-1 rounded bg-teal-50 hover:bg-teal-100 border border-teal-200 px-1.5 py-0.5 text-[10.5px] font-semibold text-teal-800 max-w-[200px] truncate cursor-pointer"
            title={q.audio_transcript}
          >
            <Volume2 className="h-2.5 w-2.5 flex-shrink-0" />
            <span className="truncate">{q.audio_transcript}</span>
          </button>
        )}
        {Boolean(
          q.video_transcript ||
          q.question_format === 'VIDEO_CLUE' ||
          q.question_type === 'VIDEO_CLUE' ||
          ((q.question_text || '').toLowerCase().match(/\b(video|clip|watch)\b/)) ||
          ((q as any).video_refs && (q as any).video_refs.some((r: string) => !r.toLowerCase().includes('timer') && !r.toLowerCase().includes('1min') && !r.toLowerCase().includes('countdown')))
        ) && (
          <button
            type="button"
            onClick={() => setSelectedQuestionForMedia(q)}
            className="inline-flex items-center gap-1 rounded bg-violet-100 hover:bg-violet-200 border border-violet-300 px-1.5 py-0.5 text-[10.5px] font-bold text-violet-900 max-w-[200px] truncate cursor-pointer shadow-2xs"
            title={q.video_transcript || "Tournament Video Clue"}
          >
            <Video className="h-3 w-3 text-violet-700 flex-shrink-0" />
            <span className="truncate">{q.video_transcript ? `Video: ${q.video_transcript}` : 'Video Clue'}</span>
          </button>
        )}
        {(q.repeated_count ?? 0) > 0 && (
          <button
            type="button"
            onClick={() => setSelectedQuestionForTags(q)}
            className="rounded bg-emerald-50 hover:bg-emerald-100 border border-emerald-200 px-1.5 py-0.5 text-[10.5px] font-semibold text-emerald-700 flex items-center gap-1 cursor-pointer"
            title={(q.repeated || []).map((r) => r.label).join('\n')}
          >
            <Layers className="h-2.5 w-2.5" />
            Repeated ×{q.repeated_count}
          </button>
        )}
      </div>
    </td>

    {/* Topics */}
    <td className="py-3.5 px-4 align-top">
      <div className="flex flex-col gap-2">
        {(q.topic_paths && q.topic_paths.length > 0) ? (
          <>
            {q.topic_paths.slice(0, 3).map((p, pi) => (
              <div key={pi} className="flex flex-col gap-1">
                <div className="flex flex-wrap items-center gap-1 text-[11px]">
                  <span className="rounded-md bg-amber-50 border border-amber-200 px-2 py-0.5 font-semibold text-amber-800">{p.l1}</span>
                  {p.l2 && <><span className="text-slate-300">›</span><span className="font-medium text-slate-600">{p.l2}</span></>}
                </div>
                {p.keywords && p.keywords.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {p.keywords.slice(0, 5).map((k) => (
                      <button
                        key={k}
                        type="button"
                        onClick={() => setTagFilter(tagFilter.toLowerCase() === k.toLowerCase() ? '' : k)}
                        className={`rounded px-1.5 py-0.5 text-[10.5px] font-medium cursor-pointer ${tagFilter.toLowerCase() === k.toLowerCase() ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
                        title={`Filter by ${k}`}
                      >
                        {k}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {q.topic_paths.length > 3 && <span className="text-[11px] text-slate-400">+{q.topic_paths.length - 3} more</span>}
          </>
        ) : (
          <>
            <span className="rounded-md bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] font-semibold text-amber-800 w-fit">{q.topic && q.topic !== "Uncategorised" ? q.topic : "General Knowledge"}</span>
            {q.subtopic && !q.subtopic.toLowerCase().includes('round') && !q.subtopic.toLowerCase().includes('closing bell') && !q.subtopic.toLowerCase().includes('market indicators') && (
              <span className="text-[11px] text-slate-500 font-medium">{q.subtopic.replace(/^[–—\-:\s]+/, '')}</span>
            )}
          </>
        )}
      </div>
    </td>

    <td className="py-3.5 px-4 text-right align-top">
      <div className="flex items-center justify-end gap-3">
        {q.duplicate_status === 'POSSIBLE_DUPLICATE' ? (
          <button
            onClick={() => setSelectedDuplicateForReview(q)}
            className="inline-flex items-center gap-1 rounded bg-amber-600 px-2 py-1 text-[11px] font-bold text-white hover:bg-amber-700 cursor-pointer"
          >
            <span>Resolve</span>
          </button>
        ) : (
          <>
            {activeTab === 'review' && (
              <button
                disabled={resolvingId === q.id}
                onClick={() => handleAcceptReview(q.id)}
                className="inline-flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 text-[11px] font-bold text-white hover:bg-emerald-700 cursor-pointer disabled:opacity-50"
                title="Confirm the AI tag as-is"
              >
                <CheckCircle className="h-3 w-3" />
                <span>Accept</span>
              </button>
            )}
            <button
              onClick={() => setSelectedQuestionForTags(q)}
              className="inline-flex items-center gap-1 text-[12px] font-semibold text-indigo-600 hover:text-indigo-700 cursor-pointer"
              title={activeTab === 'review' ? 'Correct the tag (confirms a human label)' : 'View and edit every tag on this question'}
            >
              <Tag className="h-3 w-3" />
              <span>{activeTab === 'review' ? 'Correct' : 'Edit tags'}</span>
            </button>
            <button
              onClick={() => handleViewSlide(q)}
              className="inline-flex items-center gap-1 text-[12px] font-semibold text-blue-600 hover:text-blue-700 cursor-pointer"
            >
              <span>View slide</span>
              <ExternalLink className="h-3 w-3" />
            </button>
          </>
        )}
      </div>
    </td>
    </tr>
  );
}

export default React.memo(QuestionRow);
