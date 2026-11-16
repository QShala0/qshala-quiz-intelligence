'use client';

import React, { useRef, useState } from 'react';
import {
  X,
  Image as ImageIcon,
  Volume2,
  Video,
  FileText,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Maximize2,
  AlertTriangle
} from 'lucide-react';
import { HistoricalQuestion } from '../lib/types';
import { api, resolveMediaUrl, getFallbackMediaUrl } from '../lib/api';
import QuestionImageEditor, {
  ALLOWED_IMAGE_TYPES, DraftImage, MAX_IMAGE_MB, isConnectQuestion, initialDraft, newDraftId,
} from './knowledge-base/QuestionImageEditor';
import ImageEditCanvas from './knowledge-base/ImageEditCanvas';

interface MediaViewerModalProps {
  question: HistoricalQuestion | null;
  onClose: () => void;
  // "Edit images": open straight into the picture editor. onQuestionUpdated receives the saved question.
  startInEditMode?: boolean;
  onQuestionUpdated?: (q: HistoricalQuestion) => void;
}

export default function MediaViewerModal({ question, onClose, startInEditMode = false, onQuestionUpdated }: MediaViewerModalProps) {
  const editing = startInEditMode && Boolean(onQuestionUpdated);
  const [draft, setDraft] = useState<DraftImage[]>(() => (editing && question ? initialDraft(question) : []));
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [originalSignature] = useState<string>(() =>
    JSON.stringify((editing && question ? initialDraft(question) : []).map((d) => [d.url, d.role])),
  );
  const [originalUrls] = useState<string[]>(() =>
    (editing && question ? initialDraft(question) : []).map((d) => d.url as string),
  );
  const previewUrls = useRef<string[]>([]);
  React.useEffect(() => {
    const created = previewUrls.current;
    return () => created.forEach((u) => URL.revokeObjectURL(u));
  }, []);
  // The picture open in the picture editor: edited in place, or a copy that is added as a new Question picture.
  const [canvasTarget, setCanvasTarget] = useState<{ item: DraftImage; mode: 'edit' | 'copy' } | null>(null);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  const [fullscreenImage, setFullscreenImage] = useState<string | null>(null);
  const [imageErrors, setImageErrors] = useState<Record<string, boolean>>({});
  const [fallbackUrls, setFallbackUrls] = useState<Record<string, string>>({});
  const [videoError, setVideoError] = useState(false);
  const [videoSrc, setVideoSrc] = useState<string>('');
  const [videoFallbackAttempted, setVideoFallbackAttempted] = useState(false);
  const [, setVideoPlaying] = useState(false);

  const isDirty = JSON.stringify(draft.map((d) => [d.file ? null : d.url, d.role])) !== originalSignature
    || draft.some((d) => d.file);
  // Pictures that were on the question and are no longer in the draft: removing them also deletes the stored file.
  // A picture replaced by its edited version is counted separately (it is replaced, not just removed).
  const replacedUrls = new Set(draft.map((d) => d.replaces).filter((u): u is string => Boolean(u)));
  const removedCount = originalUrls.filter((u) => !draft.some((d) => d.url === u) && !replacedUrls.has(u)).length;
  const editedCount = originalUrls.filter((u) => replacedUrls.has(u) && !draft.some((d) => d.url === u)).length;

  const handleFilesChosen = (files: File[]) => {
    setEditError(null);
    const accepted: DraftImage[] = [];
    const problems: string[] = [];
    for (const f of files) {
      if (!ALLOWED_IMAGE_TYPES.includes(f.type)) {
        problems.push(`${f.name} is not a PNG, JPG or WebP image`);
      } else if (f.size > MAX_IMAGE_MB * 1024 * 1024) {
        problems.push(`${f.name} is larger than ${MAX_IMAGE_MB} MB`);
      } else {
        const preview = URL.createObjectURL(f);
        previewUrls.current.push(preview);
        accepted.push({ id: newDraftId(), url: null, file: f, preview, role: 'question' });
      }
    }
    if (accepted.length > 0) setDraft((prev) => [...prev, ...accepted]);
    if (problems.length > 0) setEditError(problems.join('. ') + '.');
  };

  const handleRemoved = (item: DraftImage) => {
    setEditError(null);
    setDraft((prev) => prev.filter((d) => d.id !== item.id));
  };

  // The picture editor gets the original bytes: a file chosen in this draft is used as it is; a stored picture is
  // fetched through the API (see api.getQuestionImageSource).
  const loadForCanvas = React.useCallback((): Promise<Blob> => {
    const target = canvasTarget;
    if (!target || !question) return Promise.reject(new Error('Nothing to edit'));
    if (target.item.file) return Promise.resolve(target.item.file);
    if (!target.item.url) return Promise.reject(new Error('This picture has no stored file'));
    return api.getQuestionImageSource(question.id, target.item.url);
  }, [canvasTarget, question]);

  const handleCanvasSave = (file: File) => {
    const target = canvasTarget;
    setCanvasTarget(null);
    if (!target) return;
    setEditError(null);
    const preview = URL.createObjectURL(file);
    previewUrls.current.push(preview);
    if (target.mode === 'edit') {
      // Same place, same role. Remember which stored picture it replaces (an edit of an edit keeps the first one).
      setDraft((prev) => prev.map((d) => (d.id === target.item.id
        ? { ...d, url: null, file, preview, replaces: d.replaces ?? d.url ?? null }
        : d)));
    } else {
      // A new Question picture right after the one it was copied from; the original is untouched.
      const copy: DraftImage = { id: newDraftId(), url: null, file, preview, role: 'question', replaces: null };
      setDraft((prev) => {
        const i = prev.findIndex((d) => d.id === target.item.id);
        const next = [...prev];
        next.splice(i < 0 ? next.length : i + 1, 0, copy);
        return next;
      });
    }
  };

  const handleCancel = () => {
    if (isDirty && !window.confirm('Discard your image changes?')) return;
    onClose();
  };

  const handleSave = async () => {
    if (!question || !onQuestionUpdated || saving) return;
    setSaving(true);
    setEditError(null);
    try {
      let items = draft;
      const toUpload = draft.filter((d) => d.file);
      if (toUpload.length > 0) {
        const urls = await api.uploadQuestionImages(question.id, toUpload.map((d) => d.file as File));
        if (urls.length !== toUpload.length) throw new Error('The server stored a different number of images than were sent.');
        const byId = new Map(toUpload.map((d, i) => [d.id, urls[i]]));
        // Remember what was stored, so a retry after a failed save does not upload the same files again.
        items = draft.map((d) => (byId.has(d.id) ? { ...d, url: byId.get(d.id) as string, file: null } : d));
        setDraft(items);
      }
      const q = items.filter((d) => d.role === 'question').map((d) => d.url as string);
      const a = items.filter((d) => d.role === 'answer').map((d) => d.url as string);
      // For a Connect question the "Question" pictures are written into the question's numbered clue pictures.
      const updated = await api.updateQuestionImages(question.id, q, a, isConnectQuestion(question));
      onQuestionUpdated(updated);
      onClose();
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Could not save the images. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const getImageSrc = (rawUrl: string) => {
    if (!rawUrl) return '';
    if (fallbackUrls[rawUrl]) {
      return fallbackUrls[rawUrl];
    }
    return resolveMediaUrl(rawUrl);
  };

  const handleImageError = (rawUrl: string) => {
    const current = getImageSrc(rawUrl);
    const altUrl = getFallbackMediaUrl(current);
    if (altUrl && !fallbackUrls[rawUrl]) {
      setFallbackUrls((prev) => ({ ...prev, [rawUrl]: altUrl }));
    } else {
      setImageErrors((prev) => ({ ...prev, [rawUrl]: true }));
    }
  };

  if (!question) return null;

  const PLACEHOLDER_ICONS = new Set([
    'slide_41_img_1.png', 'slide_40_img_1.png', 'slide_49_img_1.png',
    'slide_53_img_1.png', 'slide_120_img_1.png', 'slide_122_img_1.png',
    'image16.png'
  ]);

  const isPlaceholderIcon = (url?: string | null) => {
    if (!url) return false;
    const name = url.replace(/\\/g, '/').split('/').pop()?.toLowerCase();
    return name ? PLACEHOLDER_ICONS.has(name) : false;
  };

  const rawQImages = question.question_images && question.question_images.length > 0
    ? question.question_images
    : (question.image_refs || []);
  const questionImages = rawQImages.filter(x => !isPlaceholderIcon(x));
  const answerImages = (question.answer_images || []).filter(x => !isPlaceholderIcon(x));
  const images = questionImages.concat(answerImages.filter(x => !questionImages.includes(x)));
  const imageRole = (url: string) => (questionImages.includes(url) ? 'Question image' : 'Answer image');
  const roleCls = (url: string) => (questionImages.includes(url) ? 'bg-sky-700 text-white' : 'bg-emerald-700 text-white');
  const hasImages = images.length > 0;

  const questionAudioList = question.question_audio && question.question_audio.length > 0
    ? question.question_audio
    : (question.audio_refs || []);
  const answerAudioList = question.answer_audio || [];
  const rawAudioList = (question.raw_media_refs || []).filter(r => r.endsWith('.mp3') || r.endsWith('.wav') || r.endsWith('.m4a') || r.endsWith('.aac') || r.includes('audio'));
  const allAudios = Array.from(new Set([...questionAudioList, ...answerAudioList, ...rawAudioList]));

  // Is this media a timer (not a trivia video clue)?
  const TIMER_NAMES = new Set(['media7.mp4', 'media11.mp4', 'media16.mp4']);

  // Is this media a timer (not a trivia video clue)?
  const isTimerMedia = (url?: string | null) => {
    if (!url) return false;
    const l = url.toLowerCase();
    const filename = l.split('/').pop()?.split('?')[0] || '';
    if (l.includes('timer') || l.includes('1min') || l.includes('countdown') || TIMER_NAMES.has(filename)) {
      return true;
    }
    const isVideoClueFormat = question?.question_format === 'VIDEO_CLUE' || question?.question_type === 'VIDEO_CLUE';
    if (!isVideoClueFormat && (l.endsWith('.mp4') || l.endsWith('.webm') || l.includes('/media/media'))) {
      const mentionsVideo = Boolean(
        question?.video_transcript ||
        question?.question_text?.toLowerCase().includes('video') ||
        question?.question_text?.toLowerCase().includes('clip') ||
        question?.question_text?.toLowerCase().includes('watch')
      );
      if (!mentionsVideo) return true;
    }
    return false;
  };

  // Find video media URL from video_refs, question_videos, raw_media_refs, or video_transcript (excluding countdown timers)
  const videoCandidate = (question.video_refs && question.video_refs.length > 0 ? question.video_refs[0] : null)
    || (question.question_videos && question.question_videos.length > 0 ? question.question_videos[0] : null)
    || (question.raw_media_refs || []).find(r => (r.endsWith('.mp4') || r.endsWith('.webm') || r.endsWith('.mov') || r.includes('/media/media')) && !isTimerMedia(r))
    || (question.video_transcript && (question.video_transcript.includes('http') || question.video_transcript.includes('youtu')) ? question.video_transcript.trim() : null);

  const videoRef = (videoCandidate && !isTimerMedia(videoCandidate)) ? videoCandidate : null;

  const hasAudio = Boolean(allAudios.length > 0 || question.audio_transcript);
  const hasVideo = Boolean(videoRef || question.video_transcript);
  const hasVisualClues = Boolean(question.visual_clues);

  const isYouTubeUrl = (url: string) => {
    return url.includes('youtube.com') || url.includes('youtu.be');
  };

  const getYouTubeEmbedUrl = (url: string) => {
    try {
      if (url.includes('youtu.be/')) {
        const id = url.split('youtu.be/')[1]?.split('?')[0];
        return `https://www.youtube.com/embed/${id}`;
      }
      if (url.includes('watch?v=')) {
        const id = new URL(url).searchParams.get('v');
        return `https://www.youtube.com/embed/${id}`;
      }
    } catch {
      return url;
    }
    return url;
  };

  const resolvedVideoSrc = videoRef ? getImageSrc(videoRef) : '';

  React.useEffect(() => {
    if (resolvedVideoSrc) {
      setVideoSrc(resolvedVideoSrc);
    } else {
      setVideoSrc('');
    }
    setVideoFallbackAttempted(false);
    setVideoError(false);
  }, [resolvedVideoSrc, question?.id]);

  // Poster frame from question clue images
  const posterRef = (question.question_images && question.question_images.length > 0 ? question.question_images[0] : null)
    || (question.image_refs && question.image_refs.length > 0 ? question.image_refs[0] : null);
  const posterSrc = posterRef ? getImageSrc(posterRef) : undefined;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-xs p-4 animate-in fade-in duration-150">
      <div
        className="relative w-full max-w-4xl max-h-[90vh] flex flex-col rounded-2xl bg-white shadow-2xl border border-slate-200 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/70 px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-100 text-blue-700 font-bold text-sm">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-[15px] font-bold text-slate-900 leading-tight">
                Multimodal Question Media
              </h3>
              <p className="text-[12px] text-slate-500 font-normal mt-0.5 flex items-center gap-1.5">
                <span>Source:</span>
                <strong className="text-slate-700 font-medium truncate max-w-md">
                  {question.document_title || 'Unknown Presentation'}
                </strong>
                {question.source_slide_range && (
                  <span className="rounded bg-slate-200/80 px-1.5 py-0.2 text-[10.5px] font-semibold text-slate-700">
                    {question.source_slide_range}
                  </span>
                )}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 transition-colors cursor-pointer"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="overflow-y-auto p-6 space-y-6 flex-1">
          {/* Question Text & Answer Bar */}
          <div className="rounded-xl border border-slate-200/80 bg-slate-50/50 p-4 space-y-2">
            <div className="text-[14px] font-semibold text-slate-900 leading-relaxed">
              {question.question_text}
            </div>
            {question.options && question.options.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                {question.options.map((opt, idx) => (
                  <div key={idx} className="rounded bg-white border border-slate-200 px-3 py-1.5 text-[12px] font-medium text-slate-700">
                    {opt}
                  </div>
                ))}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-200/60 text-[12.5px]">
              <span className="text-slate-500">Verified Answer:</span>
              <span className="font-bold text-emerald-800 bg-emerald-100/70 border border-emerald-200 px-2.5 py-0.5 rounded-md">
                {question.answer}
              </span>
              {question.explanation && (
                <span className="text-slate-500 text-[12px] italic pl-2 border-l border-slate-200">
                  {question.explanation.replace(/^(?:Clue|Hint):\s*/i, 'Instruction: ')}
                </span>
              )}
            </div>
          </div>

          {/* Section: Video Clue (placed prominently at top of media viewer) */}
          {hasVideo && (
            <div className="rounded-xl border border-violet-200 bg-violet-50/40 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-bold text-violet-900 uppercase tracking-wider flex items-center gap-1.5">
                  <Video className="h-4 w-4 text-violet-600" />
                  Embedded Video Clue
                </span>
                {videoRef && (
                  <span className="text-[11px] font-mono text-violet-700 bg-violet-100/60 px-2 py-0.5 rounded">
                    {videoRef.split('/').pop()}
                  </span>
                )}
              </div>

              {videoRef && isYouTubeUrl(videoRef) ? (
                <div className="pt-1 aspect-video w-full rounded-lg overflow-hidden bg-black shadow-md">
                  <iframe
                    src={getYouTubeEmbedUrl(videoRef)}
                    title="Embedded Video Clue"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowFullScreen
                    className="w-full h-full"
                  />
                </div>
              ) : videoRef ? (
                <div className="pt-1 space-y-2">
                  <div className="relative rounded-lg overflow-hidden bg-black shadow-md">
                    <video
                      key={videoSrc}
                      src={videoSrc}
                      controls
                      playsInline
                      preload="auto"
                      poster={posterSrc}
                      className="w-full max-h-72 object-contain mx-auto"
                      onPlay={() => setVideoPlaying(true)}
                      onPause={() => setVideoPlaying(false)}
                      onError={() => {
                        const fallback = getFallbackMediaUrl(videoSrc);
                        if (fallback && !videoFallbackAttempted && fallback !== videoSrc) {
                          console.log('[MediaViewer] Video load failed, trying resilient fallback:', fallback);
                          setVideoFallbackAttempted(true);
                          setVideoSrc(fallback);
                        } else {
                          setVideoError(true);
                        }
                      }}
                    >
                      Your browser does not support HTML5 video playback.
                    </video>
                  </div>

                  {videoError && (
                    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-[12.5px] text-slate-700 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0" />
                        <span className="font-medium text-slate-800">Video player encountered a streaming issue.</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            setVideoError(false);
                            setVideoFallbackAttempted(false);
                            setVideoSrc(resolvedVideoSrc);
                          }}
                          className="px-2.5 py-1 text-xs font-semibold rounded bg-white border border-slate-300 hover:bg-slate-100 transition-colors cursor-pointer"
                        >
                          Retry Stream
                        </button>
                        <a
                          href={videoSrc || resolvedVideoSrc}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded bg-violet-600 hover:bg-violet-700 text-white transition-colors cursor-pointer"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                          <span>Open Video Directly</span>
                        </a>
                      </div>
                    </div>
                  )}
                </div>
              ) : null}

              {question.video_transcript && !isYouTubeUrl(question.video_transcript) && (
                <div className="bg-white/80 rounded-lg p-3 border border-violet-100 text-[13px] text-violet-950">
                  <div className="font-semibold text-violet-800 text-[11px] uppercase tracking-wider mb-1">
                    Video Audio Transcript:
                  </div>
                  <div className="italic leading-relaxed">"{question.video_transcript}"</div>
                </div>
              )}
            </div>
          )}

          {/* Section: Slide Images & Visual Clues */}
          {editing ? (
            <QuestionImageEditor
              items={draft}
              onChange={setDraft}
              onFilesChosen={handleFilesChosen}
              onRemoved={handleRemoved}
              onEdit={(item) => setCanvasTarget({ item, mode: 'edit' })}
              onCopy={(item) => setCanvasTarget({ item, mode: 'copy' })}
              getImageSrc={getImageSrc}
              onZoom={setFullscreenImage}
              onImageError={handleImageError}
              connect={isConnectQuestion(question)}
              removedCount={removedCount}
              editedCount={editedCount}
              error={editError}
              busy={saving}
            />
          ) : hasImages ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[12.5px] font-bold text-slate-800 uppercase tracking-wider flex items-center gap-1.5">
                  <ImageIcon className="h-4 w-4 text-sky-600" />
                  Slide Images ({images.length})
                </span>
                {images.length > 1 && (
                  <span className="text-[12px] text-slate-500">
                    Image {activeImageIndex + 1} of {images.length}
                  </span>
                )}
              </div>

              {/* Main Image Display */}
              <div className="relative rounded-xl border border-slate-200 bg-slate-900/5 overflow-hidden flex items-center justify-center min-h-[260px] max-h-[420px]">
                {imageErrors[images[activeImageIndex]] ? (
                  <div className="flex flex-col items-center justify-center p-8 text-center text-slate-500 min-h-[220px]">
                    <ImageIcon className="h-10 w-10 text-slate-400 mb-2 stroke-1" />
                    <span className="text-[13px] font-semibold text-slate-700">Slide Visual Reference</span>
                    <span className="text-[11px] text-slate-400 mt-0.5 font-mono">
                      {images[activeImageIndex].split('/').pop()}
                    </span>
                    <span className="text-[11.5px] text-slate-500 mt-2 bg-slate-100 rounded-md px-2.5 py-1">
                      {question.source_slide_range ? `From Slides ${question.source_slide_range}` : 'Extracted Question Slide'}
                    </span>
                  </div>
                ) : (
                  <img
                    src={getImageSrc(images[activeImageIndex])}
                    alt={`Slide media ${activeImageIndex + 1}`}
                    className="max-h-[400px] w-auto max-w-full object-contain cursor-zoom-in"
                    onError={() => handleImageError(images[activeImageIndex])}
                    onClick={() => setFullscreenImage(getImageSrc(images[activeImageIndex]))}
                  />
                )}

                {images.length > 1 && (
                  <>
                    <button
                      type="button"
                      onClick={() => setActiveImageIndex((prev) => (prev > 0 ? prev - 1 : images.length - 1))}
                      className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-white/90 shadow-md p-1.5 text-slate-700 hover:bg-white hover:text-black cursor-pointer transition-all"
                    >
                      <ChevronLeft className="h-5 w-5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setActiveImageIndex((prev) => (prev < images.length - 1 ? prev + 1 : 0))}
                      className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/90 shadow-md p-1.5 text-slate-700 hover:bg-white hover:text-black cursor-pointer transition-all"
                    >
                      <ChevronRight className="h-5 w-5" />
                    </button>
                  </>
                )}

                <span className={`absolute left-2 top-2 rounded-md px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide shadow ${roleCls(images[activeImageIndex])}`}>{imageRole(images[activeImageIndex])}</span>

                {!imageErrors[images[activeImageIndex]] && (
                  <button
                    type="button"
                    onClick={() => setFullscreenImage(getImageSrc(images[activeImageIndex]))}
                    className="absolute bottom-2 right-2 rounded-lg bg-black/60 text-white p-1.5 hover:bg-black/80 transition-colors cursor-pointer"
                    title="Expand Fullscreen"
                  >
                    <Maximize2 className="h-4 w-4" />
                  </button>
                )}
              </div>

              {/* Thumbnails row if multiple images */}
              {images.length > 1 && (
                <div className="flex items-center gap-2 overflow-x-auto pb-1">
                  {images.map((img, idx) => {
                    const isErr = imageErrors[img];
                    return (
                      <button
                        key={idx}
                        type="button"
                        onClick={() => setActiveImageIndex(idx)}
                        className={`relative shrink-0 rounded-lg overflow-hidden border-2 h-16 w-20 transition-all cursor-pointer ${
                          activeImageIndex === idx ? 'border-blue-600 ring-2 ring-blue-400' : 'border-slate-200 opacity-70 hover:opacity-100'
                        }`}
                      >
                        {isErr ? (
                          <div className="flex flex-col items-center justify-center h-full w-full bg-slate-100 text-slate-400 text-[10px]">
                            <ImageIcon className="h-4 w-4 mb-0.5" />
                            <span>Img {idx + 1}</span>
                          </div>
                        ) : (
                          <img
                            src={getImageSrc(img)}
                            alt={`thumb ${idx}`}
                            className="h-full w-full object-cover"
                            onError={() => handleImageError(img)}
                          />
                        )}
                        <span className={`absolute inset-x-0 bottom-0 text-center text-[8px] font-bold uppercase leading-3 ${roleCls(img)}`}>{questionImages.includes(img) ? 'Question' : 'Answer'}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ) : null}

          {/* Section: OCR & Visual Clues Description */}
          {hasVisualClues && (
            <div className="rounded-xl border border-indigo-200 bg-indigo-50/40 p-4 space-y-1.5">
              <span className="text-[12px] font-bold text-indigo-900 uppercase tracking-wider flex items-center gap-1.5">
                <FileText className="h-3.5 w-3.5 text-indigo-600" />
                Extracted Visual OCR & Vision Description
              </span>
              <p className="text-[13px] text-indigo-950 whitespace-pre-wrap leading-relaxed">
                {question.visual_clues}
              </p>
            </div>
          )}

          {/* Section: Audio Clue & Playback */}
          {hasAudio && (
            <div className="rounded-xl border border-teal-200 bg-teal-50/40 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-bold text-teal-900 uppercase tracking-wider flex items-center gap-1.5">
                  <Volume2 className="h-4 w-4 text-teal-600" />
                  Embedded Audio ({allAudios.length} {allAudios.length === 1 ? 'Track' : 'Tracks'})
                </span>
                {allAudios.length > 0 && (
                  <span className="text-[11px] font-mono text-teal-700 bg-teal-100/60 px-2 py-0.5 rounded">
                    Audio Clips Detected
                  </span>
                )}
              </div>

              {/* Question Audio Clues */}
              {questionAudioList.length > 0 && (
                <div className="space-y-2 pt-1">
                  <div className="text-[11px] font-bold text-teal-800 uppercase tracking-wider">
                    Question Audio Clues
                  </div>
                  {questionAudioList.map((aUrl, idx) => (
                    <div key={`q-aud-${idx}`} className="flex flex-col sm:flex-row sm:items-center gap-2 p-2 rounded-lg bg-white border border-teal-200">
                      <span className="text-[11.5px] font-semibold text-teal-900 shrink-0">
                        Clue #{idx + 1}
                      </span>
                      <audio
                        controls
                        src={getImageSrc(aUrl)}
                        onError={() => handleImageError(aUrl)}
                        className="w-full h-8 text-xs accent-teal-600"
                        preload="metadata"
                      />
                    </div>
                  ))}
                </div>
              )}

              {/* Answer Reveal Audio */}
              {answerAudioList.length > 0 && (
                <div className="space-y-2 pt-1">
                  <div className="text-[11px] font-bold text-emerald-800 uppercase tracking-wider">
                    Answer Reveal Audio
                  </div>
                  {answerAudioList.map((aUrl, idx) => (
                    <div key={`ans-aud-${idx}`} className="flex flex-col sm:flex-row sm:items-center gap-2 p-2 rounded-lg bg-emerald-50 border border-emerald-200">
                      <span className="text-[11.5px] font-semibold text-emerald-900 shrink-0">
                        Reveal #{idx + 1}
                      </span>
                      <audio
                        controls
                        src={getImageSrc(aUrl)}
                        onError={() => handleImageError(aUrl)}
                        className="w-full h-8 text-xs accent-emerald-600"
                        preload="metadata"
                      />
                    </div>
                  ))}
                </div>
              )}

              {/* Other detected raw audio */}
              {questionAudioList.length === 0 && answerAudioList.length === 0 && rawAudioList.length > 0 && (
                <div className="space-y-2 pt-1">
                  {rawAudioList.map((aUrl, idx) => (
                    <div key={`raw-aud-${idx}`} className="flex flex-col sm:flex-row sm:items-center gap-2 p-2 rounded-lg bg-white border border-teal-200">
                      <span className="text-[11.5px] font-semibold text-teal-900 shrink-0">
                        Track #{idx + 1}
                      </span>
                      <audio
                        controls
                        src={getImageSrc(aUrl)}
                        onError={() => handleImageError(aUrl)}
                        className="w-full h-8 text-xs accent-teal-600"
                        preload="metadata"
                      />
                    </div>
                  ))}
                </div>
              )}

              {question.audio_transcript && (
                <div className="bg-white/80 rounded-lg p-3 border border-teal-100 text-[13px] text-teal-950">
                  <div className="font-semibold text-teal-800 text-[11px] uppercase tracking-wider mb-1">
                    Speech & Clue Transcript:
                  </div>
                  <div className="italic leading-relaxed">"{question.audio_transcript}"</div>
                </div>
              )}
            </div>
          )}



          {/* Empty state if somehow opened on text-only question */}
          {!editing && !hasImages && !hasAudio && !hasVideo && !hasVisualClues && (
            <div className="p-8 text-center text-slate-400 text-[13px]">
              This question does not contain extracted visual or audio media files.
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/70 px-6 py-3">
          <div className="text-[12px] text-slate-400">
            {question.grade_min && question.grade_max
              ? `Calibrated for Grades ${question.grade_min}–${question.grade_max}`
              : 'General Audience'}
          </div>
          {editing ? (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCancel}
                disabled={saving}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !isDirty}
                className="rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-semibold text-white hover:bg-blue-700 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
            >
              Close
            </button>
          )}
        </div>
      </div>

      {canvasTarget && (
        <ImageEditCanvas
          title={canvasTarget.mode === 'edit' ? 'Edit picture' : 'Edit a copy (added as a new Question picture)'}
          load={loadForCanvas}
          maxBytes={MAX_IMAGE_MB * 1024 * 1024}
          requireChange={canvasTarget.mode === 'edit'}
          onCancel={() => setCanvasTarget(null)}
          onSave={handleCanvasSave}
        />
      )}

      {/* Fullscreen image overlay */}
      {fullscreenImage && (
        <div
          className="fixed inset-0 z-60 flex items-center justify-center bg-black/90 p-4 cursor-zoom-out"
          onClick={() => setFullscreenImage(null)}
        >
          <img
            src={fullscreenImage}
            alt="Fullscreen view"
            className="max-h-[95vh] max-w-[95vw] object-contain rounded-lg"
            onError={() => {
              const altUrl = getFallbackMediaUrl(fullscreenImage);
              if (altUrl && altUrl !== fullscreenImage) {
                setFullscreenImage(altUrl);
              }
            }}
          />
        </div>
      )}
    </div>
  );
}
