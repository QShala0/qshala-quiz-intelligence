'use client';

import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { Loader2 } from 'lucide-react';

type Variant = 'primary' | 'secondary' | 'success' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

// Soft theme: primary is a calm sky tint with charcoal text; no hard shadows.
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-sky-100 text-slate-900 border border-sky-200 shadow-sticker-sm hover:bg-sky-200',
  secondary: 'bg-white text-slate-800 border border-slate-200 shadow-sticker-sm hover:bg-sky-50 hover:border-sky-200',
  success: 'bg-emerald-600 text-white border border-emerald-700 shadow-sticker-sm hover:bg-emerald-700',
  danger: 'bg-red-600 text-white border border-red-700 shadow-sticker-sm hover:bg-red-700',
  ghost: 'bg-transparent text-slate-800 border-2 border-transparent hover:bg-sky-50 hover:border-slate-900/10',
};

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px] gap-1.5',
  md: 'h-10 px-4 text-sm gap-2',
  lg: 'h-12 px-6 text-base gap-2.5',
};

const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading = false, disabled, className = '', children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={`qt-press inline-flex items-center justify-center rounded-pill font-bold whitespace-nowrap
        disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none
        ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...rest}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

export default Button;
