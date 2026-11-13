'use client';

import { useState } from 'react';
import {
  Check,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Trash2,
  Edit2,
  Bookmark,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Image as ImageIcon,
  X,
  ZoomIn,
  Eye,
  Volume2,
  Headphones,
  Video,
} from 'lucide-react';
import { GeneratedQuestion, RetrievalSource } from '../lib/types';
import { resolveMediaUrl, getFallbackMediaUrl } from '../lib/api';
import { hasStructuredView } from '../lib/formatPayloads';
import StructuredFormatBody from './formats/StructuredFormatBody';

interface Props {
  question: GeneratedQuestion;
  onUpdate: (id: string, updated: Partial<GeneratedQuestion>) => Promise<void>;
  onAction: (id: string, action: 'regenerate' | 'make_easier' | 'make_harder' | 'generate_similar') => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onViewSource: (src: RetrievalSource) => void;
}

export default function QuestionCard({
  question,
  onUpdate,
  onAction,
  onDelete,
  onViewSource,
}: Props) {
  const [isEditing, setIsEditing] = useState(false);
  const [qText, setQText] = useState(question.question_text);
  const [options, setOptions] = useState<string[]>(question.options || []);
  const [answer, setAnswer] = useState(question.answer);
  const [explanation, setExplanation] = useState(question.explanation || '');
  const [showSource, setShowSource] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [activeImageModal, setActiveImageModal] = useState<string | null>(null);
  const [imgFallbackUrls, setImgFallbackUrls] = useState<Record<string, string>>({});

  const getImageSrc = (rawUrl: string | null | undefined) => {
    if (!rawUrl) return '';
    if (imgFallbackUrls[rawUrl]) {
      return imgFallbackUrls[rawUrl];
    }
    return resolveMediaUrl(rawUrl);
  };

  const handleImgError = (rawUrl: string) => {
    const current = getImageSrc(rawUrl);
    const nextFallback = getFallbackMediaUrl(current);
    if (nextFallback && nextFallback !== current) {
      setImgFallbackUrls((prev) => ({ ...prev, [rawUrl]: nextFallback }));
    }
  };

  const handleSave = async () => {
    try {
      await onUpdate(question.id, {
        question_text: qText,
        options,
        answer,
        explanation,
      });
      setIsEditing(false);
    } catch {
      // The page shows why it failed; keep the editor open so nothing typed is lost.
    }
  };

  const handleAction = async (action: 'regenerate' | 'make_easier' | 'make_harder' | 'generate_similar') => {
    setActionLoading(action);
    try {
      await onAction(question.id, action);
    } finally {
      setActionLoading(null);
    }
  };

  const handleOptionChange = (idx: number, val: string) => {
    const next = [...options];
    next[idx] = val;
    setOptions(next);
  };

  const primarySource = question.retrieval_sources?.[0];

  return (
    <div
      className={`rounded-xl border bg-white p-6 transition-all shadow-sm shadow-slate-100/50 ${
        question.is_approved ? 'border-slate-200/80' : 'border-slate-200 opacity-60 bg-slate-50/50'
      }`}
    >
      {/* Top Meta Bar */}
      <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
        <div className="flex items-center gap-3">
          <span className="text-[14px] font-bold text-slate-900">
            {question.order_index}.
          </span>
          <span className="text-[12px] font-medium text-slate-500">
            {question.difficulty}
          </span>
          {question.topic && (
            <>
              <span className="text-slate-300">•</span>
              <span className="text-[12px] text-slate-500 font-medium">
                {question.topic}
              </span>
            </>
          )}
          {((question.question_audio && question.question_audio.length > 0) ||
            (question.audio_refs && question.audio_refs.length > 0)) && (
            <>
              <span className="text-slate-300">•</span>
              <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal-700 bg-teal-50 border border-teal-200 px-1.5 py-0.5 rounded">
                <Volume2 className="h-3 w-3 text-teal-600" />
                Audio Clue
              </span>
            </>
          )}
          {Boolean(
            (question.video_refs && question.video_refs.length > 0) ||
            (question.question_video && question.question_video.length > 0) ||
            (question.raw_media_refs && question.raw_media_refs.some(r => r.endsWith('.mp4') || r.includes('video')))
          ) && (
            <>
              <span className="text-slate-300">•</span>
              <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-violet-700 bg-violet-50 border border-violet-200 px-1.5 py-0.5 rounded">
                <Video className="h-3 w-3 text-violet-600" />
                Video Clue
              </span>
            </>
          )}
        </div>

        {/* Status Indicator & Approval */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-[12px] text-slate-500">
            <span
              className={`h-2 w-2 rounded-full ${
                question.validation_status === 'PASSED'
                  ? 'bg-emerald-500'
                  : question.validation_status === 'WARNING'
                  ? 'bg-amber-500'
                  : 'bg-red-500'
              }`}
            />
            <span className="font-medium text-[12px] text-slate-600">
              {question.validation_status === 'PASSED' ? 'Validated' : 'Needs Review'}
            </span>
          </div>

          <button
            onClick={() => onUpdate(question.id, { is_approved: !question.is_approved }).catch(() => {})}
            className={`rounded-md px-2.5 py-1 text-[12px] font-semibold transition-colors ${
              question.is_approved
                ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            {question.is_approved ? 'Approved' : 'Rejected'}
          </button>
        </div>
      </div>

      {/* Question Content */}
      {/* Question Content: QShala Slide Pair Presentation */}
      <div className="space-y-3.5">
        {/* Slide 1: Question Slide */}
        <div className="rounded-lg border border-slate-200/80 bg-slate-50/50 p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
              Slide 1 · Question
            </span>
          </div>

          {isEditing ? (
            <textarea
              value={qText}
              onChange={(e) => setQText(e.target.value)}
              rows={2}
              className="w-full rounded-lg border border-slate-200 bg-white p-3 text-[14px] text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          ) : (
            <h3 className="text-[15px] sm:text-[16px] font-semibold text-slate-900 leading-relaxed">
              {question.question_text}
            </h3>
          )}

          {/* The question's own format (List It, Image Reveal, Connect, Stepped Clue, Arrange, MCQ) */}
          {!isEditing && hasStructuredView(question.format_payload) && (
            <div className="mt-3">
              <StructuredFormatBody
                q={{ id: question.id, format_payload: question.format_payload, question_videos: question.question_video }}
                getImageSrc={getImageSrc}
                handleImgError={handleImgError}
                setPreviewImage={setActiveImageModal}
                readOnly
              />
            </div>
          )}

          {/* Clue Images Thumbnails */}
          {((question.question_images && question.question_images.length > 0) ||
            (question.image_refs && question.image_refs.length > 0)) && (
            <div className="mt-3 pt-2.5 border-t border-slate-200/60">
              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 block mb-2 flex items-center gap-1.5">
                <ImageIcon className="h-3.5 w-3.5 text-blue-500" />
                Visual Clues & Reference Assets
              </span>
              <div className="flex flex-wrap gap-2.5 items-center">
                {(question.question_images && question.question_images.length > 0
                  ? question.question_images
                  : question.image_refs || []
                ).map((imgUrl, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setActiveImageModal(imgUrl)}
                    className="relative group rounded-lg overflow-hidden border border-slate-200 bg-white hover:border-blue-400 shadow-sm transition-all cursor-pointer"
                  >
                    <img
                      src={getImageSrc(imgUrl)}
                      onError={() => handleImgError(imgUrl)}
                      alt={`Question Clue ${i + 1}`}
                      className="h-16 w-24 object-cover group-hover:scale-105 transition-transform duration-200"
                    />
                    <div className="absolute inset-0 bg-slate-950/20 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                      <ZoomIn className="h-4 w-4 text-white drop-shadow" />
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Visual Clues / OCR description badge */}
          {question.visual_clues && (
            <div className="rounded-md bg-indigo-50/80 border border-indigo-100 p-2 text-[11.5px] text-indigo-900 flex items-start gap-1.5 mt-2">
              <Eye className="h-3.5 w-3.5 text-indigo-600 mt-0.5 shrink-0" />
              <div className="leading-snug">
                <span className="font-semibold text-indigo-950">Vision OCR / Context: </span>
                <span className="text-indigo-800 line-clamp-2">{question.visual_clues}</span>
              </div>
            </div>
          )}

          {/* Audio Clues Player */}
          {((question.question_audio && question.question_audio.length > 0) ||
            (question.audio_refs && question.audio_refs.length > 0)) && (
            <div className="mt-3 pt-2.5 border-t border-slate-200/60">
              <span className="text-[11px] font-bold uppercase tracking-wider text-teal-700 block mb-2 flex items-center gap-1.5">
                <Volume2 className="h-3.5 w-3.5 text-teal-600" />
                Audio Clues & Sound Snippets
              </span>
              <div className="space-y-2">
                {(question.question_audio && question.question_audio.length > 0
                  ? question.question_audio
                  : question.audio_refs || []
                ).map((audioUrl, i) => (
                  <div key={i} className="flex flex-col sm:flex-row sm:items-center gap-2.5 p-2 rounded-lg bg-teal-50/70 border border-teal-200/80">
                    <div className="flex items-center gap-1.5 text-[12px] font-semibold text-teal-900 shrink-0">
                      <Headphones className="h-3.5 w-3.5 text-teal-600" />
                      <span>Audio Clue {i + 1}</span>
                    </div>
                    <audio
                      controls
                      src={getImageSrc(audioUrl)}
                      onError={() => handleImgError(audioUrl)}
                      className="w-full h-8 text-xs accent-teal-600"
                      preload="none"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Video Clues Player */}
          {Boolean(
            (question.video_refs && question.video_refs.length > 0) ||
            (question.question_video && question.question_video.length > 0) ||
            (question.raw_media_refs && question.raw_media_refs.some(r => r.endsWith('.mp4') || r.includes('video')))
          ) && (
            <div className="mt-3 pt-2.5 border-t border-slate-200/60">
              <span className="text-[11px] font-bold uppercase tracking-wider text-violet-700 block mb-2 flex items-center gap-1.5">
                <Video className="h-3.5 w-3.5 text-violet-600" />
                Video Clue & Tournament Motion Clip
              </span>
              {(() => {
                const vUrl = (question.video_refs && question.video_refs[0]) ||
                  (question.question_video && question.question_video[0]) ||
                  (question.raw_media_refs || []).find(r => r.endsWith('.mp4') || r.includes('video'));
                const posterUrl = (question.question_images && question.question_images[0]) ||
                  (question.image_refs && question.image_refs[0]);
                return vUrl ? (
                  <div className="rounded-lg overflow-hidden border border-violet-200/80 bg-black max-w-md shadow-sm">
                    <video
                      controls
                      playsInline
                      preload="metadata"
                      poster={posterUrl ? getImageSrc(posterUrl) : undefined}
                      src={getImageSrc(vUrl)}
                      onError={() => handleImgError(vUrl)}
                      className="w-full max-h-56 object-contain mx-auto"
                    >
                      Your browser does not support HTML5 video playback.
                    </video>
                  </div>
                ) : null;
              })()}
            </div>
          )}
        </div>

        {/* Options List (Legacy format support) */}
        {options.length > 0 && (
          <div className="space-y-2 pl-1">
            {options.map((opt, idx) => {
              const isCorrect =
                opt.trim().toLowerCase() === question.answer.trim().toLowerCase() ||
                question.answer.includes(opt.slice(0, 2));

              return (
                <div
                  key={idx}
                  className={`flex items-center justify-between rounded-lg border px-3.5 py-2 text-[13px] transition-all ${
                    isCorrect
                      ? 'border-emerald-200 bg-emerald-50/70 text-emerald-900 font-medium'
                      : 'border-slate-200/80 bg-white text-slate-700'
                  }`}
                >
                  <div className="flex items-center gap-2.5 flex-1 min-w-0">
                    <span
                      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                        isCorrect
                          ? 'bg-emerald-600 text-white'
                          : 'border border-slate-300 text-slate-600'
                      }`}
                    >
                      {String.fromCharCode(65 + idx)}
                    </span>

                    {isEditing ? (
                      <input
                        type="text"
                        value={opt}
                        onChange={(e) => handleOptionChange(idx, e.target.value)}
                        className="w-full bg-transparent text-[13px] text-slate-900 focus:outline-none"
                      />
                    ) : (
                      <span className="truncate">{opt}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Slide 2: Answer & Explanation Slide */}
        <div className="rounded-lg border border-emerald-100 bg-emerald-50/40 p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-700 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              Slide 2 · Answer & Explanation
            </span>
          </div>

          <div className="flex items-start gap-2">
            <span className="text-[12px] font-bold uppercase tracking-wider text-emerald-800 shrink-0 mt-0.5">
              Answer:
            </span>
            {isEditing ? (
              <input
                type="text"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                className="w-full rounded-md border border-emerald-200 bg-white px-2.5 py-1 text-[13.5px] font-semibold text-emerald-900 focus:outline-none"
              />
            ) : (
              <span className="text-[14px] font-bold text-emerald-900 bg-emerald-100/70 px-2 py-0.5 rounded">
                {question.answer}
              </span>
            )}
          </div>

          {/* Answer Images Thumbnails */}
          {question.answer_images && question.answer_images.length > 0 && (
            <div className="mt-2.5 pt-2 border-t border-emerald-100/80">
              <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-700 block mb-2 flex items-center gap-1.5">
                <ImageIcon className="h-3.5 w-3.5 text-emerald-600" />
                Answer Reveal Images
              </span>
              <div className="flex flex-wrap gap-2.5 items-center">
                {question.answer_images.map((imgUrl, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setActiveImageModal(imgUrl)}
                    className="relative group rounded-lg overflow-hidden border border-emerald-200 bg-white hover:border-emerald-500 shadow-sm transition-all cursor-pointer"
                  >
                    <img
                      src={getImageSrc(imgUrl)}
                      onError={() => handleImgError(imgUrl)}
                      alt={`Answer Image ${i + 1}`}
                      className="h-16 w-24 object-cover group-hover:scale-105 transition-transform duration-200"
                    />
                    <div className="absolute inset-0 bg-slate-950/20 opacity-0 group-hover:opacity-100 flex items-center justify-center transition-opacity">
                      <ZoomIn className="h-4 w-4 text-white drop-shadow" />
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Answer Reveal Audio Player */}
          {question.answer_audio && question.answer_audio.length > 0 && (
            <div className="mt-2.5 pt-2 border-t border-emerald-100/80">
              <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-700 block mb-2 flex items-center gap-1.5">
                <Volume2 className="h-3.5 w-3.5 text-emerald-600" />
                Answer Reveal Audio
              </span>
              <div className="space-y-2">
                {question.answer_audio.map((audioUrl, i) => (
                  <div key={i} className="flex flex-col sm:flex-row sm:items-center gap-2.5 p-2 rounded-lg bg-emerald-100/50 border border-emerald-200">
                    <div className="flex items-center gap-1.5 text-[12px] font-semibold text-emerald-900 shrink-0">
                      <Headphones className="h-3.5 w-3.5 text-emerald-600" />
                      <span>Reveal Audio {i + 1}</span>
                    </div>
                    <audio
                      controls
                      src={getImageSrc(audioUrl)}
                      onError={() => handleImgError(audioUrl)}
                      className="w-full h-8 text-xs accent-emerald-600"
                      preload="none"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {(question.explanation || isEditing) && (
            <div className="pt-2 border-t border-emerald-100/80 text-[13px] text-slate-600 leading-relaxed space-y-1">
              <span className="font-semibold text-slate-700 block text-[11.5px] uppercase tracking-wider">
                Explanation & Context:
              </span>
              {isEditing ? (
                <textarea
                  value={explanation}
                  onChange={(e) => setExplanation(e.target.value)}
                  rows={2}
                  className="w-full rounded-md border border-slate-200 bg-white p-2 text-[13px] text-slate-800"
                />
              ) : (
                <p>{question.explanation}</p>
              )}
            </div>
          )}
        </div>

        {/* Expandable Source Information */}
        {primarySource && (
          <div className="rounded-lg border border-slate-100 bg-slate-50/60">
            <button
              type="button"
              onClick={() => setShowSource(!showSource)}
              className="w-full flex items-center justify-between px-3.5 py-2 text-[12.5px] text-slate-600 hover:text-slate-900 transition-colors"
            >
              <div className="flex items-center gap-2">
                <Bookmark className="h-3.5 w-3.5 text-blue-600" />
                <span>
                  Source: <strong className="font-semibold text-slate-800">{primarySource.document_title || 'QShala Archive'}</strong> · Slide {primarySource.slide_number || 'N/A'}
                </span>
              </div>
              {showSource ? (
                <ChevronUp className="h-3.5 w-3.5 text-slate-400" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
              )}
            </button>

            {showSource && (
              <div className="border-t border-slate-100 p-3.5 text-[12px] text-slate-600 space-y-2">
                <p className="font-mono text-[12px] bg-white p-2.5 rounded-md border border-slate-200 text-slate-700 leading-relaxed">
                  {primarySource.source_quote || 'Historical evidence indexed in knowledge base.'}
                </p>
                <div className="flex justify-end">
                  <button
                    onClick={() => onViewSource(primarySource)}
                    className="flex items-center gap-1 text-[12px] font-semibold text-blue-600 hover:text-blue-700 transition-colors"
                  >
                    <span>View Original Slide</span>
                    <ExternalLink className="h-3 w-3" />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Action Toolbar */}
      <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3.5 text-[13px]">
        <div className="flex items-center gap-2">
          {isEditing ? (
            <button
              onClick={handleSave}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 font-semibold text-white hover:bg-blue-700 transition-colors"
            >
              <Check className="h-3.5 w-3.5" />
              <span>Save</span>
            </button>
          ) : (
            <button
              onClick={() => setIsEditing(true)}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <Edit2 className="h-3.5 w-3.5 text-slate-400" />
              <span>Edit</span>
            </button>
          )}

          <button
            onClick={() => handleAction('regenerate')}
            disabled={actionLoading !== null}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`h-3.5 w-3.5 text-slate-400 ${actionLoading === 'regenerate' ? 'animate-spin text-blue-600' : ''}`} />
            <span>Regenerate</span>
          </button>

          <button
            onClick={() => handleAction('make_easier')}
            disabled={actionLoading !== null}
            className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <TrendingDown className="h-3.5 w-3.5 text-slate-400" />
            <span>Make easier</span>
          </button>

          <button
            onClick={() => handleAction('make_harder')}
            disabled={actionLoading !== null}
            className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            <TrendingUp className="h-3.5 w-3.5 text-slate-400" />
            <span>Make harder</span>
          </button>
        </div>

        <button
          onClick={() => onDelete(question.id).catch(() => {})}
          className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 transition-colors"
          title="Delete Question"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {/* Lightbox Image Zoom Modal */}
      {activeImageModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-sm p-4 cursor-pointer"
          onClick={() => setActiveImageModal(null)}
        >
          <div
            className="relative max-w-4xl max-h-[90vh] bg-white rounded-xl shadow-2xl p-2 border border-slate-700 overflow-hidden cursor-default"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setActiveImageModal(null)}
              className="absolute top-3 right-3 z-10 rounded-full bg-slate-900/70 p-1.5 text-white hover:bg-slate-900 transition-colors cursor-pointer"
            >
              <X className="h-5 w-5" />
            </button>
            <img
              src={getImageSrc(activeImageModal)}
              onError={() => activeImageModal && handleImgError(activeImageModal)}
              alt="Enlarged Visual Clue"
              className="max-h-[82vh] w-auto object-contain rounded-lg mx-auto"
            />
          </div>
        </div>
      )}
    </div>
  );
}
