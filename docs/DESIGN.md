# QShala Brand Design System & Living Spec (June 2026 Guidelines)

> **Identity**: QShala = **Curiosity Learning Platform**.  
> Learning that encourages creativity, curiosity, and compassion for school children, parents, and teachers.  
> **Taglines**: **Questions • Quriosity • Quests** (primary), **Curiosity Learning Platform** (alt). *(Note intentional spelling "Quriosity".)*  
> **Brand Tone**: Casual, friendly, spontaneous, high-energy, fun, modern, accessible, relational, open.

---

## 1. Official Brand Palette & Design Tokens

Defined centrally via CSS variables and Tailwind CSS v4 `@theme` in [`frontend/src/app/globals.css`](file:///c:/Users/aaksh/Downloads/qshala/frontend/src/app/globals.css) and [`antigravity_brief/brand-tokens.css`](file:///c:/Users/aaksh/Downloads/qshala/antigravity_brief/brand-tokens.css).

### The Strict Palette Rule
**Never use colors outside the 3 official brand colors + black/white/cream (+ tints, + functional error red).** No gradients that blend brand colors, and no drop shadows on the logo or mascot.

| Brand Color | Role | HEX | CMYK | Usage in App |
| :--- | :--- | :--- | :--- | :--- |
| **Joy of Quest (Sun)** | Hero accent | `#FDB913` | C0 M30 Y100 K0 | Primary CTAs, active pills, section bars, title rules, focus rings |
| **Qurious Sky (Sky)** | Blue accent | `#30B2E7` | C67 M10 Y0 K0 | Supporting accent, progress bars, category tags, avatar accents |
| **Always Questioning (Green)** | Green accent | `#75B543` | C60 M7 Y100 K0 | Supporting accent, completed status, success confirmations |
| **Ink** | Black | `#000000` | — | Text, headings, card borders, logo wordmark, QT mascot body |
| **Cream** | Page canvas | `#FFF6E1` | — | Global page background across all screens |
| **White / Paper** | Card surfaces | `#FFFFFF` | — | Elevated cards, dialog sheets, tables, white logo wordmarks |
| **Warm Grey** | Secondary text | `#5F5F66` | — | Muted captions and labels (tested ≥ 4.5:1 contrast against cream) |
| **Functional Error** | Error / Destructive | `#D62828` | — | Critical validation errors and destructive actions only |

### Contrast Rules
- **On Sun (`#FDB913`)**: Always use **black text** (`#000000`). Never white.
- **On Sky (`#30B2E7`)**: Always use **black text** (`#000000`). Never white.
- **On Green (`#75B543`)**: White text permitted only at large display sizes (≥ 24 px bold); black text for body.
- **On Cream (`#FFF6E1`)**: Black text for headings and primary copy; `#5F5F66` for secondary descriptions.

---

## 2. Signature Brand Devices

1. **Section Divider Bar (`.section-bar`)**:
   A black ALL-CAPS heading (Causten Round Black 900) paired with a full-width Sun-yellow bar (`height: 3px; background: #FDB913; border-radius: 9999px`).
2. **Page Title Rule (`.page-title-rule` / `border-b-2 border-sun`)**:
   A crisp 2px Sun-yellow border running under top page titles.
3. **Rounded Brand Cards**:
   Cards with generous rounding (`rounded-[20px]`), crisp black or subtle Sun-yellow border (`border border-sun/30`), white fill on cream background, and sticker shadows (`shadow-sticker-sm`).
4. **Primary Action Buttons**:
   `bg-sun text-ink font-bold shadow-sticker-sm hover:brightness-105 active:scale-[0.99] rounded-xl px-4 py-2`.

---

## 3. Typography: Causten Round

The application uses **Causten Round** loaded locally via `next/font/local` (`frontend/src/fonts/CaustenRound-*.woff2`) as the sole UI typeface. Google Fonts (`Plus Jakarta Sans`) has been completely removed.

- **Causten Round Black (900) & ExtraBold (800)**: Page titles, section headings, numbers, and primary button labels.
- **Causten Round Bold (700) & SemiBold (600)**: Navigation links, table headers, subheadings, and badges.
- **Causten Round Medium (500) & Regular (400)**: Body text, tables, form inputs, speaker notes, and help copy.
- *(Note: Mikado Black is the school display font reserved for school presentation titles; the UI adheres uniformly to Causten Round).*

---

## 4. Logo Guidelines

Implemented in [`frontend/src/components/ui/Logo.tsx`](file:///c:/Users/aaksh/Downloads/qshala/frontend/src/components/ui/Logo.tsx).

- **Official Wordmark**: Clean, bold lettering accompanied by the registered trademark symbol (**®**).
- **Clear Space**: Equal to the height of the letter "Q" on all four sides.
- **Variants**:
  - `horizontal`: Horizontal lockup for the top navigation and sidebar on cream.
  - `stacked`: Primary stacked unit for headers and print.
  - `mark`: The iconic QT cat mark for compact views, favicons, and spinners.
  - `white`: All-white lockup on dark or brand-colored fills.
  - `sky`, `grass`, `sun`: Colored wordmark units with black cat on cream surfaces.
- **Strict Don'ts**: Never drop shadow, stretch, squeeze, rotate, recolor outside brand palette, lower opacity, or omit the ® symbol.

---

## 5. Mascot: QT the Cat

Managed via [`frontend/src/components/ui/QT.tsx`](file:///c:/Users/aaksh/Downloads/qshala/frontend/src/components/ui/QT.tsx) using official vector SVGs in `frontend/public/qt/svg/`.

- **Form**: Solid black silhouette with white eyes, whiskers, and smiling mouth. Curved question-mark tail.
- **Accessories**: Brand colors only (blue box/yarn, yellow necktie, green props).
- **Role in App**: Expressive personality for empty states, loaders, error recovery, celebration moments, and live present mode. Never placed on top of dense data. Maximum 1 QT per view.
- **Motion & Accessibility**: Always respects `prefers-reduced-motion` with static artwork. Fixed dimensions to ensure zero Cumulative Layout Shift (CLS).

### Key Moment Poses
- **Empty States / Zero Results**: `sherlock` (detective with magnifying glass), `looking-up`, `thinking`
- **Loading / Generating**: `quizzing`, `puzzle`, `playing`
- **Upload / Ingesting**: `reading` (with book/glasses), `sleeping` (after completion)
- **Success / Celebration**: `winner` (celebrating with trophy), `jumping`
- **Error / Tangled**: `scared`, `sad`
- **404 Page**: `peeking` (peeking from around the doorway)
- **Live Quiz Presenter**: `quizzing`, `winner`, `trophy` (positioned unobtrusively in the bottom-left corner)

---

## 6. Easter Egg Registry

| # | Feature / Location | Component File | Trigger Condition | Visual Effect |
| :-: | :--- | :--- | :--- | :--- |
| **1** | **Sidebar Logo Celebration** | `frontend/src/components/Sidebar.tsx` | Click the QShala logo **5 times** in succession | Triggers Sun-yellow paw confetti burst (`Confetti.tsx`) and celebration toast. |
| **2** | **Detective Search Empty State** | `frontend/src/app/knowledge-base/page.tsx` | Search returning 0 results | Detective QT (`sherlock` pose): *"Detective QT looked everywhere — no clues found!"* |
| **3** | **Ingestion Progress Mascot** | `frontend/src/app/uploads/page.tsx` | Uploading a presentation deck | QT reads (`reading` pose) along the progress bar; curls up asleep (`sleeping` pose) upon completion. |
| **4** | **404 Door Peeker** | `frontend/src/app/not-found.tsx` | Navigating to an invalid route | QT peeking curiously from behind a door (`peeking` pose) with button to return to base. |
| **5** | **Empty Quiz Library** | `frontend/src/app/quizzes/page.tsx` | Library with 0 generated quizzes | QT looking up curiously (`looking-up` pose): *"An empty library! Click 'New Quiz' to fill it up."* |
| **6** | **Presenter Countdown Ears** | `frontend/src/components/StagePresenter.tsx` | Timer drops below **10 seconds** | Playful Sun-yellow cat ears pop up atop the timer ring with high-contrast Sky accents. |
| **7** | **Interactive Speech Bubbles** | All pages with `<QT />` | Clicking or tapping any QT mascot | Interactive speech bubble with curious trivia quips and encouragement. |
| **8** | **Quiz Completion Trophy** | `frontend/src/app/generate/page.tsx` | Generation draft completes | QT switches to champion pose (`winner` pose) celebrating the new quiz deck. |
| **9** | **Error Recovery Mascot** | `frontend/src/app/error.tsx` | Client runtime error boundary | QT looking startled (`scared` pose) with gentle helpful recovery buttons. |

---

## 7. Verification Checklist

- [x] Causten Round 400-900 loaded as the sole UI font; Plus Jakarta Sans completely removed.
- [x] Zero stray old hex codes (`#F8B42C`, `#5BB4DE`) or legacy hand-drawn cat SVGs.
- [x] All primary buttons styled in Sun yellow (`#FDB913`) with black text (`#000000`) and sticker shadows.
- [x] All section titles utilize the signature `.section-bar` (black caps + Sun-yellow bar).
- [x] All cards styled on cream canvas (`#FFF6E1`) with rounded corners (`rounded-[20px]`).
- [x] 100% of text and background pairings pass WCAG AA contrast standards.
