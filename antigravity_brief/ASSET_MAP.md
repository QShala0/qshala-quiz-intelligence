# Asset map: brand file -> where it is used

Root of raw files: `OneDrive_2026-11-24/Brand Elements/` (call it `$BE`). Never reference `$BE` from the app; the prep script outputs web-ready copies.

## Logos (`$BE/QT/QShala Updated Logo/`)
| Source | Output | Use |
|---|---|---|
| `QShala Logo with tag and QT.png` (black, horizontal, cat left) | `public/brand/logo-horizontal-black.png` (1200 w) | Sidebar/topbar on cream, login-style screens |
| `QShala Logo.png` (black wordmark + cat) | `public/brand/logo-black.png` (800 w) | Compact header, exports, print |
| `QShala Logo with tag.png` | `public/brand/logo-tagline-black.png` (1000 w) | Home hero, About, footers |
| `QShala Logo W_1.png` (all white) | `public/brand/logo-white.png` (1000 w) | On Sky/Sun/Green/black fills. NOTE: the cat is white here too; guideline's coloured-background version keeps the cat black. For those, build `logo-on-colour.svg` by recolouring: cat black, wordmark+tagline white. |
| Coloured units (Sky/Green/Sun wordmark, black cat; p.9-11 of PDF) | `public/brand/logo-sky.png`, `logo-grass.png`, `logo-sun.png` | Generate by recolouring the black wordmark pixels (everything not in the cat's bounding region) from `QShala Logo with tag.png`; or extract from PDF pages 9-11 with `pdfimages`/`pdftoppm -r 300` and crop. Used for section headers/themed screens. |
| Cat alone | `public/brand/qt-mark.svg` (from `QT SVG/QT normal.svg` or `QT 1.svg`) | Favicon, avatar, loading spinner |
| Favicon | `frontend/src/app/icon.png` (512), `favicon.ico`, `apple-icon.png` (180) | Cat on Sun-yellow rounded square |
| OG image | `public/brand/og.png` (1200x630) | Cream bg, primary logo with tagline, yellow bar |

## QT mascot SVGs (`$BE/QT/QT SVG/`, 68 files). Copy chosen ones, lowercase-kebab names, to `public/qt/svg/`
Prefer SVG (tiny, crisp). Existing `public/qt/*.webp` poses stay as fallbacks; migrate `ui/QT.tsx` to the SVG set.

| UI moment | Source SVG | Notes |
|---|---|---|
| Default/idle, sidebar footer | `QT normal.svg`, `QT happy.svg` | |
| Empty states (no results, empty bank) | `QT lookign up.svg` (sic), `QT cute thinking.svg`, `QT question with normal face.svg`, `QT tail Question mark.svg` | "The cat looked everywhere" copy |
| Search zero results | `QT sherlock.svg` | detective |
| Loading / generating quiz | `QT playing.svg`, `QT playing 2.svg`, `QT playing with butterfly.svg`, `QT quizzing.svg`, `QT puzzle.svg` | rotate; static if reduced-motion |
| Upload / ingesting | `QT reading.svg`, `QT with headphone.svg`, `QT holding camera.svg` | |
| Success / export done | `QT jumping.svg`, `Qt jumping 2.svg`, `Qt winner.svg`, `QT with trophy.svg`, `QT superhero.svg` | with confetti |
| Error / failed import | `QT scared.svg`, `QT shock.svg`, `QT not happy.svg`, `QT sad Holi.svg` (rename to `qt-sad`; ignore Holi) | gentle copy |
| 404 | `QT peaking.svg`, `Qt peaking 2.svg` | "peeking from the door" |
| Idea / tip / AI suggestion | `QT Idea.svg` | |
| Sleeping / idle timeout / no active quiz | `QT Sleeping.svg`, `Qt Laydown.svg`, `QT laydown 2.svg` | |
| Announcements / banners | `QT anounce.svg`, `QT taling.svg` | |
| Settings / admin | `QT professional.svg`, `QT lawyer.svg` | professor with tie |
| Present mode (live quiz) | `QT quizzing.svg`, `Qt winner.svg`, `QT with trophy.svg` | corner only, never over question |
| Onboarding / welcome | `Qt with kid.svg`, `Qt travel.svg`, `QT yoga.svg` | |
| Do NOT use by default | `QT christman`, `Qt onam`, `QT salute for india`, `QT with 75th india`, `QT sad Holi`, `QT angry`, `QT mad at`, `QT holding money` | seasonal / off-tone |

The 26 "QT in action-NN.png" and 84 "All QT/Artboard N.png" are rasters of the same family; use SVGs first, rasters only if a pose is missing (resize <= 480 px, WebP).

## Animations (`$BE/QT/QT Animations/`, 72 MB total: too heavy to ship as GIF)
`QT Celebrate.gif`, `QT Letsget started.gif`, `QT Magnify.gif`, `QT Megaphone.gif`, `QT Animations correct answer (1).gif`, `QShala Logo-Green.gif`, `QShala Logo-Yellow.gif`.
Convert each to animated WebP or MP4/WebM (<= 400 KB, <= 480 px) into `public/qt/anim/`. Use: Celebrate = quiz exported / all done; Magnify = search loading; Letsget started = Generate page first-run; Megaphone = presenter start; Correct answer = answer reveal in StagePresenter. Provide static poster fallback and honour `prefers-reduced-motion`.

## Landing illustrations (`$BE/QT/QT for landing page_*.png`, 6251x4501)
Three scenes (L&D, Onboarding, People & Culture). Resize to 1600 w WebP; use on Home hero / empty dashboard / Settings. Pick one per page, never two.

## Fonts (`$BE/Rebranded Fonts/`, 19 OTF)
Already converted: `frontend/src/fonts/CaustenRound-{400,500,600,700,800,900}.woff2` (400 Regular, 500 Medium, 600 SemiBold, 700 Bold, 800 ExtraBold, 900 Black). Add italics only if a component needs them. `HVD Fonts - Mikado Black.otf` -> convert to woff2 with fontTools only if used (display, few words).

## Do not use from the PDF
Photography pages (22-25) are reference for tone only; they are stock photos, not licensed assets for the app.
