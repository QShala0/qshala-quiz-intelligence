'use client';

import { useState } from 'react';
import { X, Bookmark, Image as ImageIcon } from 'lucide-react';
import { resolveMediaUrl, getFallbackMediaUrl } from '../lib/api';
import { HistoricalQuestion } from '../lib/types';
import { hasStructuredView, formatStyle } from '../lib/formatPayloads';
import StructuredFormatBody from './formats/StructuredFormatBody';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  slide: {
    document_title?: string | null;
    slide_number?: number | null;
    quote?: string | null;
    extracted_text?: string;
    speaker_notes?: string | null;
    slide_type?: string;
    image_paths?: string[];
    /** The question the slide belongs to: when its format was read from the slide, show that view too. */
    question?: HistoricalQuestion | null;
  } | null;
}

export default function SlideViewerModal({ isOpen, onClose, slide }: Props) {
  const [fallbackUrls, setFallbackUrls] = useState<Record<string, string>>({});
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  if (!isOpen || !slide) return null;

  const getImageSrc = (rawUrl: string) => {
    if (!rawUrl) return '';
    if (fallbackUrls[rawUrl]) {
      return fallbackUrls[rawUrl];
    }
    return resolveMediaUrl(rawUrl);
  };

  const handleImgError = (rawUrl: string) => {
    const current = getImageSrc(rawUrl);
    const nextFallback = getFallbackMediaUrl(current);
    if (nextFallback && nextFallback !== current) {
      setFallbackUrls((prev) => ({ ...prev, [rawUrl]: nextFallback }));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm animate-in fade-in">
      <div className="relative w-full max-w-2xl overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
              <Bookmark className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-[16px] font-bold text-slate-900">
                {slide.document_title || 'Historical QShala Document'}
              </h3>
              <p className="text-[12px] text-slate-500 font-normal">
                Original Slide #{slide.slide_number || 1}
                {slide.slide_type && (
                  <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-700">
                    {slide.slide_type}
                  </span>
                )}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors cursor-pointer"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Content Body */}
        <div className="max-h-[70vh] overflow-y-auto p-6 space-y-4 text-[13px]">
          {/* How the format was read from this slide (List It ticks, reveal covers, Arrange order...) */}
          {slide.question && hasStructuredView(slide.question.format_payload) && (
            <div data-testid="slide-structured">
              <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
                {formatStyle(slide.question.question_format).label} · as read from the slide
              </span>
              <div className="mt-1.5 rounded-lg border border-slate-200 bg-white p-3">
                <StructuredFormatBody
                  q={slide.question}
                  getImageSrc={getImageSrc}
                  handleImgError={handleImgError}
                  setPreviewImage={setPreviewUrl}
                />
              </div>
              {previewUrl && (
                <button type="button" className="mt-2 block w-full" onClick={() => setPreviewUrl(null)} title="Close picture">
                  <img src={getImageSrc(previewUrl)} alt="Selected picture" className="max-h-72 w-full rounded-lg border border-slate-200 object-contain bg-slate-50" />
                </button>
              )}
            </div>
          )}

          {/* Slide Text Content */}
          <div>
            <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
              Original Presentation Content
            </span>
            <div className="mt-1.5 whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-4 text-slate-800 font-mono text-[12.5px] leading-relaxed">
              {slide.extracted_text || slide.quote || 'No text extracted from this slide.'}
            </div>
          </div>

          {/* Speaker Notes */}
          {slide.speaker_notes && (
            <div>
              <span className="text-[12px] font-semibold uppercase tracking-wider text-amber-700">
                Speaker Notes & Answer Reference
              </span>
              <div className="mt-1.5 whitespace-pre-wrap rounded-lg border border-amber-200 bg-amber-50/60 p-3.5 text-amber-900 text-[13px] leading-relaxed">
                {slide.speaker_notes}
              </div>
            </div>
          )}

          {/* Extracted Images */}
          {slide.image_paths && slide.image_paths.length > 0 && (
            <div>
              <span className="text-[12px] font-semibold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
                <ImageIcon className="h-3.5 w-3.5 text-blue-600" />
                Extracted Visuals ({slide.image_paths.length})
              </span>
              <div className="mt-2 grid grid-cols-2 gap-3">
                {slide.image_paths.map((img, idx) => (
                  <img
                    key={idx}
                    src={getImageSrc(img)}
                    onError={() => handleImgError(img)}
                    alt={`Slide asset ${idx + 1}`}
                    className="rounded-lg border border-slate-200 bg-slate-50 object-contain h-32 w-full"
                  />
                ))}
              </div>
            </div>
          )}

        </div>

        {/* Footer */}
        <div className="flex justify-end border-t border-slate-100 bg-slate-50/50 px-6 py-3">
          <button
            onClick={onClose}
            className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-[13px] sm:text-[14px] font-semibold text-slate-700 hover:bg-slate-50 transition-colors cursor-pointer"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
