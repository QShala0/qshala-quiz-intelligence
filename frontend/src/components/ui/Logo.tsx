import Image from 'next/image';

export interface LogoProps {
  /** Variant of the official QShala lockup */
  variant?: 'horizontal' | 'stacked' | 'mark' | 'white' | 'sky' | 'grass' | 'sun';
  /** Cat mark only (used when the sidebar is collapsed or for compact avatar) */
  collapsed?: boolean;
  /** Show the tagline (defaults to true in the official lockups) */
  tagline?: boolean;
  /** Height in pixels (default: 34) */
  height?: number;
  className?: string;
  priority?: boolean;
}

/**
 * Official QShala® Brand Lockup (June 2026 Brand Guidelines):
 * - Primary unit: black cat above wordmark + tagline
 * - Alt Stack (horizontal): black cat left + wordmark + tagline
 * - Always includes the registered trademark ® mark
 * - Preserves official clear space (height of "Q" on all sides)
 */
export default function Logo({
  variant = 'horizontal',
  collapsed = false,
  tagline = true,
  height = 34,
  className = '',
  priority = true,
}: LogoProps) {
  if (collapsed || variant === 'mark') {
    return (
      <span className={`inline-flex items-center justify-center select-none ${className}`}>
        <Image
          src="/brand/qt-mark.svg"
          alt="QT the QShala companion cat"
          width={Math.round(height * 1.36)}
          height={height}
          priority={priority}
          className="h-auto w-auto max-h-[36px] object-contain transition-transform hover:scale-105"
        />
      </span>
    );
  }

  const logoSrc = {
    horizontal: tagline ? '/brand/logo-horizontal-black.png' : '/brand/logo-black.png',
    stacked: tagline ? '/brand/logo-tagline-black.png' : '/brand/logo-black.png',
    white: '/brand/logo-white.png',
    sky: '/brand/logo-sky.png',
    grass: '/brand/logo-grass.png',
    sun: '/brand/logo-sun.png',
  }[variant] || '/brand/logo-horizontal-black.png';

  const width = variant === 'stacked'
    ? Math.round(height * 3.19)
    : Math.round(height * 4.63);

  return (
    <span className={`inline-flex items-center select-none ${className}`}>
      <Image
        src={logoSrc}
        alt="QShala® — Questions • Quriosity • Quests"
        width={width}
        height={height}
        priority={priority}
        className="h-auto w-auto object-contain max-h-[44px]"
      />
    </span>
  );
}
