'use client';

import React, { useRef } from 'react';
import { Image as ImageIcon, Plus, X, AlertTriangle, Info, ChevronLeft, ChevronRight } from 'lucide-react';
import { HistoricalQuestion } from '../../lib/types';
import { isPlaceholderIcon } from '../../lib/placeholderIcons';
import { isConnect } from '../../lib/formatPayloads';

// The picture editor shown inside the Multimodal Question Media popup ("Edit images"). It edits a local draft only:
// nothing is uploaded or saved until the popup's Save button is pressed, and Cancel throws the draft away.
// For most questions it edits the question / answer picture lists. For a Connect question the pictures marked
// "Question" ARE the numbered clue pictures (1, 2, 3 ... in the order shown), so saving rewrites them in the
// question itself, not just in the strip under it.

export const MAX_IMAGE_MB = 4; // keep in step with QUESTION_IMAGE_MAX_BYTES on the server (default 4 MiB)
export const ALLOWED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

export interface DraftImage {
  id: string;
  url: string | null;       // already stored (or just uploaded)
  file: File | null;        // chosen from disk, not uploaded yet
  preview: string | null;   // local preview for a chosen file
  role: 'question' | 'answer';
}

let draftCounter = 0;
export function newDraftId(): string {
  draftCounter += 1;
  return `draft-${draftCounter}`;
}

// A Connect question keeps its numbered pictures in its own data (items 1..n), which can differ from the flat strip.
export function isConnectQuestion(q: HistoricalQuestion): boolean {
  return isConnect(q.format_payload);
}

// The pictures, in the same order and with the same roles, that the viewer shows for this question. For a Connect
// question the "Question" pictures are its numbered clue pictures.
export function initialDraft(q: HistoricalQuestion): DraftImage[] {
  const connectItems = isConnect(q.format_payload) ? q.format_payload.items : null;
  const rawQ = connectItems
    ? [...connectItems].sort((a, b) => a.order - b.order).map((it) => it.image)
    : (q.question_images && q.question_images.length > 0 ? q.question_images : (q.image_refs || []));
  const qs = rawQ.filter((x) => Boolean(x) && !isPlaceholderIcon(x));
  const as = (q.answer_images || []).filter((x) => !isPlaceholderIcon(x) && !qs.includes(x));
  return [
    ...qs.map((url) => ({ id: newDraftId(), url, file: null, preview: null, role: 'question' as const })),
    ...as.map((url) => ({ id: newDraftId(), url, file: null, preview: null, role: 'answer' as const })),
  ];
}

interface Props {
  items: DraftImage[];
  onChange: (items: DraftImage[]) => void;
  onFilesChosen: (files: File[]) => void;
  onRemoved: (item: DraftImage) => void;
  getImageSrc: (url: string) => string;
  onImageError?: (url: string) => void;   // lets the popup try the backend's /api/v1/static path for a local file
  onZoom: (src: string) => void;
  connect: boolean;       // a Connect question: "Question" pictures are the numbered clue pictures
  removedCount: number;   // stored pictures taken off the question in this draft
  error: string | null;
  busy: boolean;
}

export default function QuestionImageEditor({
  items, onChange, onFilesChosen, onRemoved, getImageSrc, onImageError, onZoom, connect, removedCount, error, busy,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);

  const setRole = (id: string, role: DraftImage['role']) =>
    onChange(items.map((it) => (it.id === id ? { ...it, role } : it)));

  // Order matters for Connect (it is the numbering), so a picture can be moved one place left or right.
  const move = (index: number, delta: -1 | 1) => {
    const to = index + delta;
    if (to < 0 || to >= items.length) return;
    const next = [...items];
    [next[index], next[to]] = [next[to], next[index]];
    onChange(next);
  };
  const clueNumber = (id: string): number | null => {
    if (!connect) return null;
    const qs = items.filter((it) => it.role === 'question');
    const pos = qs.findIndex((it) => it.id === id);
    return pos >= 0 ? pos + 1 : null;
  };

  return (
    <div className="space-y-3" data-testid="question-image-editor">
      <div className="flex items-center justify-between">
        <span className="text-[12.5px] font-bold text-slate-800 uppercase tracking-wider flex items-center gap-1.5">
          <ImageIcon className="h-4 w-4 text-sky-600" />
          Edit images ({items.length})
        </span>
        <span className="text-[11.5px] text-slate-500">PNG, JPG or WebP, up to {MAX_IMAGE_MB} MB each</span>
      </div>

      {connect && (
        <div className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-[12px] text-sky-900" data-testid="connect-note">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-sky-600" />
          <span>
            Connect question: the pictures marked <strong>Question</strong> are its numbered clue pictures (1, 2, 3 …) in
            the order shown, and saving changes them in the question itself. Use the arrows to reorder. Pictures marked
            <strong> Answer</strong> show with the common link.
          </span>
        </div>
      )}

      {removedCount > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-900" data-testid="delete-warning">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-amber-600" />
          <span>
            {removedCount} picture{removedCount === 1 ? '' : 's'} will be removed. When you save, the stored file is
            deleted too, unless another question, quiz or slide still uses it. This cannot be undone.
          </span>
        </div>
      )}

      {error && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-800">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-red-600" />
          <span>{error}</span>
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
        {items.map((it, idx) => {
          const src = it.preview || (it.url ? getImageSrc(it.url) : '');
          return (
            <div key={it.id} className="relative rounded-xl border border-slate-200 bg-white overflow-hidden shadow-xs" data-testid="draft-image">
              <button
                type="button"
                onClick={() => src && onZoom(src)}
                className="block h-28 w-full bg-slate-100 cursor-zoom-in"
                title="Click to enlarge"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={src}
                  alt={`Image ${idx + 1}`}
                  className="h-full w-full object-cover"
                  onError={() => { if (it.url && onImageError) onImageError(it.url); }}
                />
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onRemoved(it)}
                className="absolute right-1.5 top-1.5 rounded-full bg-black/65 p-1 text-white hover:bg-red-600 transition-colors cursor-pointer disabled:opacity-50"
                title="Remove this image from the question"
                aria-label={`Remove image ${idx + 1}`}
              >
                <X className="h-3.5 w-3.5" />
              </button>
              {clueNumber(it.id) !== null && (
                <span
                  className="absolute left-1.5 top-1.5 rounded bg-blue-700/90 px-1.5 py-0.5 text-[10px] font-bold text-white"
                  data-testid="clue-number"
                  title="Clue picture number"
                >
                  {clueNumber(it.id)}
                </span>
              )}
              {it.file && (
                <span className="absolute left-1.5 top-[5.3rem] rounded bg-amber-500 px-1.5 py-0.5 text-[9px] font-bold uppercase text-white">New</span>
              )}
              <div className="absolute right-1.5 top-[5.3rem] flex gap-1">
                <button
                  type="button"
                  disabled={busy || idx === 0}
                  onClick={() => move(idx, -1)}
                  className="rounded bg-black/60 p-0.5 text-white hover:bg-black/80 disabled:opacity-30 cursor-pointer"
                  title="Move earlier"
                  aria-label={`Move image ${idx + 1} earlier`}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  disabled={busy || idx === items.length - 1}
                  onClick={() => move(idx, 1)}
                  className="rounded bg-black/60 p-0.5 text-white hover:bg-black/80 disabled:opacity-30 cursor-pointer"
                  title="Move later"
                  aria-label={`Move image ${idx + 1} later`}
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="flex text-[10.5px] font-bold uppercase" role="group" aria-label={`Image ${idx + 1} role`}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setRole(it.id, 'question')}
                  aria-pressed={it.role === 'question'}
                  className={`flex-1 py-1 cursor-pointer ${it.role === 'question' ? 'bg-sky-700 text-white' : 'bg-slate-50 text-slate-500 hover:bg-slate-100'}`}
                >
                  Question
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setRole(it.id, 'answer')}
                  aria-pressed={it.role === 'answer'}
                  className={`flex-1 py-1 cursor-pointer ${it.role === 'answer' ? 'bg-emerald-700 text-white' : 'bg-slate-50 text-slate-500 hover:bg-slate-100'}`}
                >
                  Answer
                </button>
              </div>
            </div>
          );
        })}

        <button
          type="button"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
          className="flex h-[calc(7rem+1.65rem)] flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed border-slate-300 text-slate-500 hover:border-blue-400 hover:text-blue-600 hover:bg-blue-50/40 transition-colors cursor-pointer disabled:opacity-50"
          data-testid="add-image-tile"
        >
          <Plus className="h-5 w-5" />
          <span className="text-[12px] font-semibold">Add image</span>
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={ALLOWED_IMAGE_TYPES.join(',')}
          className="hidden"
          data-testid="image-file-input"
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            e.target.value = ''; // let the same file be chosen again later
            if (files.length > 0) onFilesChosen(files);
          }}
        />
      </div>

      {items.length === 0 && (
        <p className="text-[12px] text-slate-400">No images on this question yet. Use Add image to attach some.</p>
      )}
    </div>
  );
}
