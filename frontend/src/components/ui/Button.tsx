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

// Yellow only ever carries black text (white-on-yellow fails contrast).
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-sun text-ink border-2 border-ink shadow-sticker-sm hover:bg-sun-200',
  secondary: 'bg-sky text-ink border-2 border-ink shadow-sticker-sm hover:bg-sky-200',
  success: 'bg-leaf text-ink border-2 border-ink shadow-sticker-sm hover:bg-leaf-100',
  danger: 'bg-berry text-white border-2 border-ink shadow-sticker-sm hover:brightness-95',
  ghost: 'bg-transparent text-ink border-2 border-transparent hover:bg-sun-50 hover:border-ink/15',
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
