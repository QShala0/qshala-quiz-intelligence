import Image from 'next/image';

/** Every pose available in /public/qt (built by scripts/build_qt_assets.py): [width, height]. */
const POSES = {
  sit: [480, 374],
  'chase-blue': [480, 236],
  'chase-yellow': [480, 236],
  'chase-green': [480, 236],
  professor: [480, 343],
  'box-blue': [480, 320],
  'box-yellow': [480, 320],
  'box-green': [480, 320],
  curled: [480, 456],
  fishbowl: [480, 206],
  stretch: [480, 208],
  round: [480, 362],
  whiskers: [480, 328],
  sprawl: [480, 216],
  'wide-eyed': [480, 362],
  fluffy: [480, 342],
  alert: [480, 296],
  'yarn-mat': [480, 414],
  'yarn-green': [480, 414],
  'yarn-blue': [480, 414],
  'yarn-yellow': [480, 414],
  happy: [480, 296],
  sleepy: [480, 357],
  dizzy: [475, 480],
  'door-green': [315, 480],
  'door-yellow': [315, 480],
  'door-blue': [315, 480],
} as const;

export type QTPose = keyof typeof POSES;
export const QT_POSES = Object.keys(POSES) as QTPose[];

const ALT: Partial<Record<QTPose, string>> = {
  sit: 'QT the QShala cat sitting',
  professor: 'QT the cat wearing professor glasses and a tie',
  dizzy: 'QT the cat looking dizzy',
  happy: 'QT the cat looking happy',
  sleepy: 'QT the cat looking sleepy',
};

type Motion = 'none' | 'bob' | 'breathe' | 'wiggle';

interface QTProps {
  pose: QTPose;
  /** Rendered height in px (width follows the pose's aspect ratio). */
  height?: number;
  /** Decorative cats are hidden from screen readers (default). Pass `alt` to make one meaningful. */
  alt?: string;
  motion?: Motion;
  className?: string;
  priority?: boolean;
}

const MOTION_CLASS: Record<Motion, string> = {
  none: '',
  bob: 'animate-bob',
  breathe: 'animate-breathe',
  wiggle: 'hover:animate-wiggle',
};

/** QT, the QShala cat. Static pose images keep the page light; motion is CSS-only. */
export default function QT({ pose, height = 96, alt, motion = 'none', className = '', priority = false }: QTProps) {
  const [w, h] = POSES[pose];
  const width = Math.round((height * w) / h);
  const label = alt ?? ALT[pose];
  return (
    <Image
      src={`/qt/${pose}.webp`}
      width={width}
      height={height}
      alt={alt === '' || !label ? '' : label}
      aria-hidden={alt === '' || !label ? true : undefined}
      priority={priority}
      unoptimized
      draggable={false}
      className={`select-none object-contain ${MOTION_CLASS[motion]} ${className}`}
      style={{ width, height }}
    />
  );
}
