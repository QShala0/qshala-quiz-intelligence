'use client';

import React, { useEffect, useRef } from 'react';
import { AlertTriangle, AlertCircle, X, Loader2 } from 'lucide-react';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  isDestructive?: boolean;
  isLoading?: boolean;
  error?: string | null;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  isDestructive = false,
  isLoading = false,
  error = null,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const cancelBtnRef = useRef<HTMLButtonElement>(null);

  // Focus on Cancel by default when dialog opens
  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => {
        cancelBtnRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [open]);

  // Handle escape key
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isLoading) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose, isLoading]);

  if (!open) return null;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Safety against accidental keypress: Enter does NOT confirm destructive actions
    if (e.key === 'Enter' && isDestructive) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/45 p-4 backdrop-blur-xs animate-fade-in"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      onKeyDown={handleKeyDown}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !isLoading) {
          onClose();
        }
      }}
    >
      <div className="relative w-full max-w-md rounded-2xl border border-[#E9DFC9] bg-[#FFFCF5] p-6 shadow-2xl transition-all">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
                isDestructive
                  ? 'bg-red-100 text-[#C24138]'
                  : 'bg-amber-100 text-amber-800'
              }`}
            >
              {isDestructive ? (
                <AlertTriangle className="h-5 w-5" />
              ) : (
                <AlertCircle className="h-5 w-5" />
              )}
            </div>
            <h3
              id="confirm-dialog-title"
              className="text-[17px] font-bold text-[#1E222D]"
            >
              {title}
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={isLoading}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Message Body */}
        <div className="mt-3.5 text-[13.5px] leading-relaxed text-[#232733]">
          {message}
        </div>

        {/* Error Alert Box inside dialog */}
        {error && (
          <div className="mt-4 flex items-start gap-2.5 rounded-xl border border-red-200 bg-red-50/90 p-3.5 text-[13px] text-red-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
            <div className="flex-1 font-medium leading-snug">{error}</div>
          </div>
        )}

        {/* Actions Footer */}
        <div className="mt-6 flex items-center justify-end gap-3 border-t border-[#E9DFC9]/80 pt-4">
          <button
            ref={cancelBtnRef}
            type="button"
            onClick={onClose}
            disabled={isLoading}
            className="rounded-xl border border-[#E9DFC9] bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 shadow-2xs hover:bg-slate-50 focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-60"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isLoading}
            className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-[13px] font-semibold shadow-xs transition-colors focus:border-amber-400 focus:outline-none focus:ring-4 focus:ring-amber-400/20 disabled:opacity-60 ${
              isDestructive
                ? 'bg-[#C24138] text-white hover:bg-[#a6342d]'
                : 'bg-[#1E222D] text-[#FFFCF5] hover:bg-[#2B3040]'
            }`}
          >
            {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            <span>{confirmLabel}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
