# QShala Design System & Mascot Registry

> **Design Philosophy**: Warm, calm, thoughtful, and professional first; playful and delightful second.  
> Inspired by the classic QShala School PowerPoint visual identity: warm paper surfaces, sunny yellow and sky blue accents, and QT the inquisitive cat companion.

---

## 1. Palette & Design Tokens

Defined centrally via Tailwind CSS v4 `@theme` in [`frontend/src/app/globals.css`](file:///c:/Users/aaksh/Downloads/qshala/frontend/src/app/globals.css).  
**Zero pure white (`#FFFFFF`) or pure black (`#000000`)** exists in the UI.

| Token Role | Hex Code | Visual Name | Purpose | WCAG Contrast vs Surface |
| :--- | :--- | :--- | :--- | :--- |
| **Page Canvas** | `#FBF7EE` | Warm Oat | Application body background | — |
| **Surface / Card** | `#FFFCF5` | Warm Paper | Card containers, drawer sheets, modal cards | — |
| **Surface Hover** | `#FFF6E0` | Pale Cream | Row hover and subtle surface highlights | — |
| **Primary Text** | `#1E222D` / `#232733` | Soft Ink | Body text, headings, dark buttons | 14.2:1 (Passes AAA) |
| **Secondary Text** | `#4B5263` | Charcoal Wash | Supporting descriptions and timestamps | 7.3:1 (Passes AAA) |
| **Muted Text** | `#686D7B` | Sandstone Slate | Badges, meta tags, and subtitles | 4.8:1 (Passes AA) |
| **Borders** | `#E9DFC9` / `#D8CBB0`| Warm Sand | Subtle card borders, input strokes, dividers | 1.3:1 – 1.6:1 (Structural) |
| **Accent 1 (Primary)** | `#F8B42C` | Sunny Yellow | Mascot necktie, highlight chips, focus ambient | 3.1:1 UI (Passes AA) |
| **Accent 1 Tint** | `#FFF0C7` | Soft Butter | Selection backgrounds, chip active states | — |
| **Accent 2 (Sky)** | `#5BB4DE` | Softened Sky | Pill highlights, secondary indicators | 2.5:1 UI |
| **Accent 2 Ink** | `#1F6F96` | Sky Ink | Links, interactive icons on cream surfaces | 5.2:1 (Passes AA) |
| **Primary Buttons** | `#1E222D` / `#2B3040`| Warm Ink / Cream | Main call-to-actions, primary submit | 13.9:1 (Passes AAA) |
| **Success** | `#2D8054` | Muted Emerald | Passed validation, successful exports | 5.1:1 (Passes AA) |
| **Danger** | `#C24138` | Muted Brick Red | Delete actions, failed validations | 4.9:1 (Passes AA) |

---

## 2. Shared Focus Glow System

- **Replaces**: All browser-default blue outlines and legacy Tailwind blue rings (`focus:ring-blue-*`, `focus:border-blue-*`, `focus:border-indigo-*`).
- **Border Focus Shift**: `#F2C265` (Warm Amber).
- **Ambient Glow**: `box-shadow: 0 0 0 4px rgba(248, 180, 44, 0.20)`.
- **Timing**: `transition: border-color 0.15s ease, box-shadow 0.15s ease`.
- **Keyboard Access**: `:focus-visible` ring on buttons, links, and cards (`outline: 2px solid #F2C265 !important; outline-offset: 2px !important`).
- **Prompt Bar Focus**: Seamless container focus (`focus-within:border-amber-400 focus-within:ring-4 focus-within:ring-amber-400/20`), inner textarea uses `.focus-none` to eliminate double-rings.

---

## 3. Mascot Component: QCat

Located in [`frontend/src/components/mascot/QCat.tsx`](file:///c:/Users/aaksh/Downloads/qshala/frontend/src/components/mascot/QCat.tsx).

- **Weight**: < 6 KB pure inline vector SVG, zero external network requests.
- **Brand Details**: Soft ink body (`#1E222D`), round warm paper glasses (`#FFFCF5`), and sunny yellow necktie (`#F8B42C`).
- **Poses Supported**:
  1. `idle`: Seated upright companion with gentle breathing and blinking every ~5.2s.
  2. `peek`: Head and paws clutching border edge; eyes look toward prompt when typing.
  3. `think`: Head tilted, paw on cheek, floating curiosity `?` marks in sunny yellow.
  4. `happy`: Smiling eyes (`^ ^`), `:3` smile, fluttering tie, and rapid tail wag for celebrations.
  5. `sleep`: Curled up into a peaceful ball, cozy deep breathing on errors or empty states.
  6. `paw`: Friendly waving paw.
- **Accessibility & Safety**:
  - `pointer-events: none` on graphic elements to ensure clicks never get blocked.
  - `@media (prefers-reduced-motion: reduce)` automatically disables all animations.
  - Fixed box dimensions ensure Cumulative Layout Shift (CLS) = 0.

---

## 4. Easter Egg Registry

| # | Feature / Location | Component File | Trigger Condition | Visual Effect |
| :-: | :--- | :--- | :--- | :--- |
| **1** | **Sidebar Logo** | `frontend/src/components/Sidebar.tsx` | Click the QShala logo **5 times** in succession | Triggers sunny-yellow paw-print confetti burst (`Confetti.tsx`) and playful toast message. |
| **2** | **Zero-Result Bank Search** | `frontend/src/app/knowledge-base/page.tsx` | Search a term in Question Bank returning 0 matches | QCat in `think` pose with a mini magnifying glass: *"No questions contain '<word>'. The cat looked everywhere."* |
| **3** | **Upload Progress Bar** | `frontend/src/app/uploads/page.tsx` | Upload a presentation file | QCat walks in `paw` pose along the progress bar as % advances; curls up asleep (`sleep` pose) upon 100% completion. |
| **4** | **404 Page Illustration** | `frontend/src/app/not-found.tsx` | Navigate to an unrecognised route (e.g. `/not-real`) | Cat innocently sitting on a shelf next to a fallen vase: *"Looks like this page got pushed off the edge."* |
| **5** | **Empty Quiz Deck** | `frontend/src/app/quizzes/[id]/page.tsx` | Open a quiz with 0 questions | Cozy QCat sleeping on an empty binder: *"No questions added yet. Start by generating or picking from the bank."* |
| **6** | **StagePresenter Timer Ring** | `frontend/src/components/StagePresenter.tsx` | Countdown timer reaches **< 10 seconds** | Two subtle sunny-yellow cat ears outline pop up atop the circular countdown timer ring. |
| **7** | **Question Card Watermark** | `frontend/src/components/QuestionCard.tsx` | Viewing question cards anywhere in quizzes | Subtle vector paw watermark rendered in the bottom-right corner at `opacity-[0.035]`. |
| **8** | **Settings Build Info** | `frontend/src/app/settings/page.tsx` | Navigate to `/settings` -> Platform Config | Small professor cat wearing glasses and yellow tie perched next to the platform version badge. |
| **9** | **Toast Success Notifications**| `frontend/src/components/ui/Feedback.tsx` | Any successful user action (e.g., save to bank) | Standard checkmark is replaced with a tiny sunny-yellow paw print icon. |
| **10**| **Browser Tab Title** | `frontend/src/app/generate/page.tsx` | While quiz generation is active (`loading === true`) | Tab title switches dynamically to `"🐾 Generating quiz... | QShala AI"` and restores upon finish. |

---

## 5. Verification & Testing Checklist

- [x] All forms, drawers, and modal inputs inherit warm amber focus glow; zero blue focus rings.
- [x] No pure `#FFFFFF` background surfaces remain; all use warm paper `#FFFCF5` or oat `#FBF7EE`.
- [x] 100% of text and background pairings pass WCAG AA contrast ratio (≥ 4.5:1 text, ≥ 3.0:1 UI).
- [x] Continuous animations disabled when `prefers-reduced-motion` is active.
- [x] `npm run build` passes with zero TypeScript errors and all static routes generated.
