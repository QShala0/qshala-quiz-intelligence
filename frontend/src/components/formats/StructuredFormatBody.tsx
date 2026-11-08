import { HistoricalQuestion } from '../../lib/types';
import { isArrange, isConnect, isImageReveal, isListIt, isMcq, isStepped } from '../../lib/formatPayloads';
import ListItView from './ListItView';
import ImageRevealView from './ImageRevealView';
import ConnectGrid from './ConnectGrid';
import SteppedClueView from './SteppedClueView';
import ArrangeView from './ArrangeView';
import McqView from './McqView';

/** The bits of a question the views read: a saved question or a generated one both fit. */
export interface StructuredSource {
  id: string;
  format_payload?: unknown;
  question_videos?: string[];
}

export interface StructuredFormatBodyProps {
  q: StructuredSource;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage: (url: string) => void;
  onQuestionUpdated?: (q: HistoricalQuestion) => void;
  /** No editing (the Arrange order editor is hidden), e.g. for generated quiz questions. */
  readOnly?: boolean;
}

// The format-aware view of a question, chosen from its stored payload (not from guessing at its text).
// Callers check hasStructuredView(payload) first; older rows without the new payload keep their previous rendering.
export default function StructuredFormatBody({ q, getImageSrc, handleImgError, setPreviewImage, onQuestionUpdated, readOnly }: StructuredFormatBodyProps) {
  const p: unknown = q.format_payload;
  if (isListIt(p)) return <ListItView payload={p} />;
  if (isImageReveal(p)) return <ImageRevealView payload={p} getImageSrc={getImageSrc} handleImgError={handleImgError} setPreviewImage={setPreviewImage} />;
  if (isConnect(p)) {
    const hasVideo = (q.question_videos || []).length > 0;
    return <ConnectGrid payload={p} hasVideo={hasVideo} getImageSrc={getImageSrc} handleImgError={handleImgError} setPreviewImage={setPreviewImage} />;
  }
  if (isStepped(p)) return <SteppedClueView payload={p} getImageSrc={getImageSrc} handleImgError={handleImgError} setPreviewImage={setPreviewImage} />;
  if (isArrange(p)) {
    return <ArrangeView questionId={q.id} payload={p} getImageSrc={getImageSrc} handleImgError={handleImgError} setPreviewImage={setPreviewImage} onOrderSaved={onQuestionUpdated} readOnly={readOnly} />;
  }
  if (isMcq(p)) return <McqView payload={p} />;
  return null;
}
