'use client';

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Decorative art (e.g. a <QT/>) peeking over the top-right corner. */
  mascot?: ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl';
}

const WIDTH = { sm: 'max-w-sm', md: 'max-w-lg', lg: 'max-w-3xl', xl: 'max-w-5xl' } as const;

/** Shared modal shell: Escape to close, backdrop click, scroll lock, focus moved in and restored. */
export default function Modal({ open, onClose, title, children, footer, mascot, size = 'md' }: ModalProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-ink/45 p-4 animate-fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
        className={`relative flex max-h-[90vh] w-full flex-col rounded-card border border-slate-200 bg-white shadow-sticker-lg animate-pop focus:outline-none ${WIDTH[size]}`}
      >
        {mascot && <div className="pointer-events-none absolute -top-9 right-6">{mascot}</div>}
        {title && (
          <div className="flex items-center justify-between gap-4 border-b-2 border-ink/10 px-6 py-4">
            <h2 id={titleId} className="text-lg font-extrabold">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-full p-1.5 text-ink/60 hover:bg-sun-50 hover:text-ink"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        )}
        <div className="overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t-2 border-ink/10 px-6 py-4">{footer}</div>}
      </div>
    </div>
  );
}
