import { HistoricalQuestion } from '../../lib/types';
import { isPlaceholderIcon } from '../../lib/placeholderIcons';
import { hasStructuredView } from '../../lib/formatPayloads';
import StructuredFormatBody from '../formats/StructuredFormatBody';

// Renders a question's body differently depending on its question_format (QShala priority plan
// P3-1: extracted unchanged from knowledge-base/page.tsx, where it was a standalone function
// component living inside the same 1173-line file as everything else on the page).
export default function QuestionFormatDisplay({
  q,
  getImageSrc,
  setPreviewImage,
  handleImgError,
  onQuestionUpdated,
}: {
  q: HistoricalQuestion;
  getImageSrc: (url: string) => string;
  setPreviewImage: (url: string) => void;
  handleImgError: (url: string) => void;
  onQuestionUpdated?: (q: HistoricalQuestion) => void;
}) {
  const fmt = q.question_format;
  const payload = q.format_data || q.format_payload;

  // Format-aware ingestion stored a structured payload: show the format's own view.
  if (hasStructuredView(q.format_payload)) {
    return (
      <StructuredFormatBody
        q={q}
        getImageSrc={getImageSrc}
        handleImgError={handleImgError}
        setPreviewImage={setPreviewImage}
        onQuestionUpdated={onQuestionUpdated}
      />
    );
  }

  if (fmt === 'CONNECT_4' || fmt === 'CONNECT') {
    const rawImgs = (q.question_images && q.question_images.length > 0 ? q.question_images : q.image_refs || [])
      .filter(x => !isPlaceholderIcon(x));
    return (
      <div className="space-y-2 mt-1">
        {q.question_text && q.question_text.trim() && (
          <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">
            {q.question_text}
          </div>
        )}
        {rawImgs.length > 0 && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
            {rawImgs.slice(0, 4).map((imgUrl, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setPreviewImage(imgUrl)}
                className="relative rounded-lg overflow-hidden border border-blue-200 bg-slate-50 aspect-video hover:border-blue-400 hover:shadow-xs transition-all cursor-pointer group"
                title={`Clue ${i + 1}: click to view`}
              >
                <img
                  src={getImageSrc(imgUrl)}
                  onError={() => handleImgError(imgUrl)}
                  alt={`Clue ${i + 1}`}
                  className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                />
                <span className="absolute top-1 left-1 rounded bg-blue-700/80 px-1.5 py-0.5 text-[8.5px] font-bold text-white uppercase">
                  Clue {i + 1}
                </span>
              </button>
            ))}
          </div>
        )}
        <div className="text-[12.5px] text-slate-700 font-normal">
          <span className="font-bold text-blue-700">Common Link:</span>{' '}
          <span className="font-semibold text-slate-900">{q.answer}</span>
        </div>
      </div>
    );
  }

  if (fmt === 'STEPPED_CLUE' || fmt === 'THREE_CLUE') {
    const clues = payload?.clues;
    return (
      <div className="space-y-1.5 mt-1">
        {clues && Array.isArray(clues) && clues.length > 0 ? (
          <div className="space-y-1.5 pt-1">
            {clues.map((clue: any, i: number) => {
              const imgUrl = clue.image || clue.image_url;
              return (
                <div key={i} className="flex items-start gap-2.5 rounded-lg bg-amber-50/70 border border-amber-200/80 px-3 py-2 text-[12.5px]">
                  <span className="shrink-0 rounded bg-amber-200 text-amber-900 font-mono text-[11px] font-bold px-2 py-0.5">
                    {clue.points || `Clue ${clue.step || i + 1}`}
                  </span>
                  <div className="min-w-0 flex-1">
                    {clue.difficulty_label && (
                      <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-800/70 block mb-0.5">
                        {clue.difficulty_label}
                      </span>
                    )}
                    <span className="text-slate-800 leading-snug">{clue.text || q.question_text}</span>
                  </div>
                  {imgUrl && (
                    <button
                      type="button"
                      onClick={() => setPreviewImage(imgUrl)}
                      className="shrink-0 rounded-md overflow-hidden border border-amber-300 bg-white hover:border-amber-500 transition cursor-pointer"
                      title="Click to preview clue image"
                    >
                      <img
                        src={getImageSrc(imgUrl)}
                        onError={() => handleImgError(imgUrl)}
                        alt={`Clue ${i + 1}`}
                        className="h-10 w-14 object-contain"
                      />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">
            {q.question_text}
          </div>
        )}
        <div className="text-[12.5px] text-slate-700 font-normal pt-0.5">
          <span className="font-bold text-amber-800">Answer:</span>{' '}
          <span className="font-semibold text-slate-900">{q.answer}</span>
        </div>
      </div>
    );
  }

  if (fmt === 'ARRANGE_ORDER') {
    const items = payload?.items;
    return (
      <div className="space-y-2 mt-1">
        <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">
          {q.question_text}
        </div>
        {items && Array.isArray(items) && items.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {items.map((item: any, i: number) => (
              <span key={i} className="inline-flex items-center gap-1.5 rounded-lg border border-purple-200 bg-purple-50/60 px-2.5 py-1 text-[12px] text-slate-800 font-medium">
                <span className="font-bold text-purple-700">[{item.key || String.fromCharCode(65 + i)}]</span>
                <span>{item.text || item.label}</span>
              </span>
            ))}
          </div>
        )}
        <div className="text-[12.5px] text-slate-700 font-normal pt-0.5">
          <span className="font-bold text-purple-700">Correct Sequence:</span>{' '}
          <span className="font-semibold text-slate-900">{q.answer}</span>
        </div>
      </div>
    );
  }

  // STANDARD_QA default
  return (
    <>
      <div className="font-semibold text-[13.5px] text-slate-900 leading-snug">
        {q.question_text}
      </div>
      <div className="mt-1 text-[12px] text-slate-600 font-normal">
        Answer: <span className="font-semibold text-slate-800">{q.answer}</span>
      </div>
    </>
  );
}
