'use client';

/**
 * Toasts + confirmation dialogs, replacing native alert()/confirm().
 *
 *   const toast = useToast();           toast.success('Quiz exported'); toast.error('Upload failed');
 *   const confirm = useConfirm();       if (await confirm({ title, message, danger: true })) { ... }
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { X } from 'lucide-react';
import QT, { type QTPose, PawIcon } from './QT';
import QTAnim from './QTAnim';
import Modal from './Modal';
import Button from './Button';
import Confetti from './Confetti';

type ToastKind = 'success' | 'error' | 'info';
interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  celebrate?: boolean;
  /** Batch notices / announcements: shows the megaphone QT. */
  announce?: boolean;
}

interface ConfirmOptions {
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive action: red button + dizzy-free, calm cat. */
  danger?: boolean;
}

interface Api {
  toast: {
    success: (message: string, opts?: { celebrate?: boolean }) => void;
    error: (message: string) => void;
    info: (message: string, opts?: { announce?: boolean }) => void;
  };
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
}

const FeedbackContext = createContext<Api | null>(null);

const KIND_STYLE: Record<ToastKind, { bg: string; pose: QTPose }> = {
  success: { bg: 'bg-leaf-100', pose: 'happy' },
  error: { bg: 'bg-berry-50', pose: 'dizzy' },
  info: { bg: 'bg-sky-100', pose: 'sit' },
};

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [dialog, setDialog] = useState<(ConfirmOptions & { resolve: (v: boolean) => void }) | null>(null);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  const push = useCallback(
    (kind: ToastKind, message: string, celebrate = false, announce = false) => {
      const id = nextId.current++;
      setToasts((t) => [...t.slice(-3), { id, kind, message, celebrate, announce }]);
      // errors linger a bit longer so they can be read
      window.setTimeout(() => dismiss(id), kind === 'error' ? 7000 : 4000);
    },
    [dismiss],
  );

  const api = useMemo<Api>(
    () => ({
      toast: {
        success: (m, o) => push('success', m, o?.celebrate),
        error: (m) => push('error', m),
        info: (m, o) => push('info', m, false, o?.announce),
      },
      confirm: (opts) => new Promise<boolean>((resolve) => setDialog({ ...opts, resolve })),
    }),
    [push],
  );

  const closeDialog = (value: boolean) => {
    dialog?.resolve(value);
    setDialog(null);
  };

  return (
    <FeedbackContext.Provider value={api}>
      {children}

      <div
        className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-[min(92vw,360px)] flex-col gap-2"
        role="region"
        aria-label="Notifications"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === 'error' ? 'alert' : 'status'}
            className="pointer-events-auto relative flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-md shadow-slate-200/50 animate-toast-in"
          >
            {t.celebrate && <Confetti />}
            {t.celebrate ? (
              <QTAnim anim="celebrate" fallbackPose="professor" height={40} />
            ) : t.announce ? (
              <QTAnim anim="megaphone" fallbackPose="happy" height={40} />
            ) : t.kind === 'success' ? (
              <span className="flex h-7 w-7 items-center justify-center rounded-full bg-amber-100/70 text-amber-600 shrink-0">
                <PawIcon className="h-4 w-4 fill-amber-500" />
              </span>
            ) : (
              <QT pose={KIND_STYLE[t.kind].pose} height={34} />
            )}
            <p className="flex-1 text-sm font-semibold text-slate-800">{t.message}</p>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>

      <Modal
        open={!!dialog}
        onClose={() => closeDialog(false)}
        title={dialog?.title}
        size="sm"
        mascot={<QT pose={dialog?.danger ? 'wide-eyed' : 'professor'} height={64} />}
        footer={
          <>
            <Button variant="ghost" onClick={() => closeDialog(false)}>
              {dialog?.cancelLabel ?? 'Cancel'}
            </Button>
            <Button variant={dialog?.danger ? 'danger' : 'primary'} onClick={() => closeDialog(true)}>
              {dialog?.confirmLabel ?? (dialog?.danger ? 'Delete' : 'Confirm')}
            </Button>
          </>
        }
      >
        {dialog?.message && <div className="text-sm text-ink/80">{dialog.message}</div>}
      </Modal>
    </FeedbackContext.Provider>
  );
}

function useFeedback(): Api {
  const ctx = useContext(FeedbackContext);
  if (!ctx) throw new Error('useToast/useConfirm must be used inside <FeedbackProvider>');
  return ctx;
}

export const useToast = () => useFeedback().toast;
export const useConfirm = () => useFeedback().confirm;
