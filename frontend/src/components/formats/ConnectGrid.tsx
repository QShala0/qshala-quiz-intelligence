import { ConnectPayload } from '../../lib/formatPayloads';

interface Props {
  payload: ConnectPayload;
  hasVideo: boolean;
  getImageSrc: (url: string) => string;
  handleImgError: (url: string) => void;
  setPreviewImage: (url: string) => void;
}

// Connect: up to nine pictures (GIFs included) with an optional text hint, optional clue video and the
// common link. Labels come from the slide's speaker notes when the deck has them.
export default function ConnectGrid({ payload, hasVideo, getImageSrc, handleImgError, setPreviewImage }: Props) {
  const items = payload.items;
  return (
    <div className="space-y-2 mt-1" data-testid="connect-view">
      {payload.text_hint && (
        <div className="text-[13px] text-slate-900">
          <span className="font-bold text-blue-700">Hint:</span> <span className="font-semibold">{payload.text_hint}</span>
        </div>
      )}
      {items.length > 0 ? (
        <ul className="grid grid-cols-3 gap-2 max-w-xl">
          {items.map((it, i) => (
            <li key={it.order} className="min-w-0">
              <button
                type="button"
                onClick={() => setPreviewImage(it.image)}
                className="relative block w-full aspect-video overflow-hidden rounded-lg border border-blue-200 bg-slate-50 hover:border-blue-400 cursor-pointer group"
                title={`Picture ${it.order}`}
              >
                <img
                  src={getImageSrc(it.image)}
                  onError={() => handleImgError(it.image)}
                  alt={`Picture ${it.order}`}
                  className="h-full w-full object-contain bg-white group-hover:scale-105 transition-transform"
                />
                <span className="absolute left-1 top-1 rounded bg-blue-700/85 px-1.5 py-0.5 text-[9px] font-bold text-white">{it.order}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-[12px] text-slate-400">{hasVideo ? 'Clue video only (no pictures).' : 'No pictures stored.'}</div>
      )}
      {payload.item_labels.length > 0 && (
        // The notes list the clues in the order the presenter wrote them, which need not be the order of the pictures on
        // the slide, so they are shown together instead of under individual pictures.
        <div className="text-[11px] text-slate-500" data-testid="connect-labels">
          <span className="font-semibold text-slate-600">From the speaker notes:</span> {payload.item_labels.join(' · ')}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <span className="rounded-full bg-blue-50 border border-blue-200 px-2 py-0.5 text-[11px] font-semibold text-blue-700">
          {items.length} picture{items.length === 1 ? '' : 's'}{hasVideo ? ' + video' : ''}
        </span>
        {payload.scoring?.correct != null && (
          <span className="text-[11px] font-mono text-slate-600">
            +{payload.scoring.correct}{payload.scoring.wrong != null ? ` / −${Math.abs(payload.scoring.wrong)}` : ''}
          </span>
        )}
        <span><span className="font-bold text-blue-700">Common link:</span> <span className="font-semibold text-slate-900">{payload.answer}</span></span>
      </div>
    </div>
  );
}
