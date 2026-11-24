# QShala Brand Spec (distilled from "Brand Guideline 2026.pdf", June 2026, 26 pages)

## 1. Who/why
QShala = "Curiosity Learning Platform": learning that encourages creativity, curiosity, compassion for school children, parents and teachers. Taglines: **Questions • Quriosity • Quests** (primary), **Curiosity Learning Platform** (alt). Note the intentional spelling "Quriosity".
Value pillars: Growth via Curiosity (adventurous, imaginative, fun, enthusiastic); Innovative & Modern Thinking (compassion, helpful, good listener, knowledgeable); Community Building (empathy, patience, charming, modern).

## 2. Brand voice (dial positions on the guideline's spectrum)
Leans: Casual, Friendly, Spontaneous, High-energy, Fun. Fully: Modern, Accessible, Relational, Open.
In UI copy: short, warm, playful, plain words; second person; no jargon, no corporate stiffness, no sarcasm; curiosity metaphors (quests, questions, "the cat looked everywhere"). Errors are gentle and helpful, never blaming. This is an internal tool for quiz masters, so stay efficient: personality in empty/loading/success states and headings, not in dense working screens.

## 3. Colour (exact)
| Name | Role | HEX | CMYK |
|---|---|---|---|
| Qurious Sky | blue | `#30B2E7` | C67 M10 Y0 K0 |
| Joy of Quest | yellow (hero colour) | `#FDB913` | C0 M30 Y100 K0 |
| Always Questioning | green | `#75B543` | C60 M7 Y100 K0 |
| Ink | logo cat, text, headings | `#000000` | |
| Cream | page background used throughout the guideline (sample from PDF render, approx `#FFF6E1`) | | |
| White | cards on cream, logo on coloured backgrounds | `#FFFFFF` | |
Grey for secondary text in the guideline is a mid warm grey (approx `#8C8C93`); ensure body secondary text still passes 4.5:1 on cream (darken to ~`#5F5F66` if needed).
Rules: only brand colours for logo; logo wordmark may be black, Sky, Sun, Green, or white (on Sky/Sun/Green backgrounds). The cat in the logo is always black. Yellow is the dominant accent (rules, bars, highlights); Sky and Green are supporting and for categorising (e.g. the 3-colour rotation on chips/cards/poses).
Signature device: a **full-width Sun-yellow bar** next to a black ALL-CAPS heading (section dividers), and a thin yellow rule under page titles. Rounded cards (radius ~20 px) with a thin yellow border on cream.

## 4. Typography
- **Mikado Black** (`HVD Fonts - Mikado Black.otf`): "primary font for schools", heavy display face. Optional, only for school-facing display moments; keep to a few words.
- **Causten Round Black**: primary font for everything else (headings, buttons, labels, ALL-CAPS section titles).
- **Causten Round (Regular/Medium)**: secondary, body copy, tables, forms, letterheads, web.
- Convert OTFs to woff2 (already done: `frontend/src/fonts/CaustenRound-400..900.woff2`; Mikado not converted, convert only if used).
- Headings: Black weight, tight but not cramped; section labels ALL CAPS. Body 16 px min, line-height 1.5+. Large-screen/presenter text: much larger, high contrast.

## 5. Logo
- Primary unit: black cat above black "QSHALA®" wordmark + tagline below. Alt tagline version. Coloured units (Sky/Green/Sun wordmark, black cat) on white/cream; Alt versions: white wordmark on Sky/Green/Sun fill. "Alt Stack": wordmark left, cat right (horizontal, for headers).
- Files: `QT/QShala Updated Logo/` (`QShala Logo.png` black wordmark+cat, `QShala Logo with tag.png`, `QShala Logo with tag and QT.png` horizontal with cat, `QShala Logo W_1.png` white).
- Clear space = height of the letter "Q" on every side (applies with and without tagline).
- Don'ts (guideline p.17): drop shadow, stretch/squeeze, wrong colours (e.g. pink), rotate, low opacity, removing the ® symbol, special effects, crowding.
- Minimum practical size: wordmark must stay legible; drop the tagline below ~120 px wide.

## 6. Mascot: QT the cat
Named QT, inspired by the form of letter Q: a round black body with a question-mark/curl tail. Curious, exploratory, creative. Solid black silhouette with white eyes/whiskers/mouth; accessories and props use only brand colours: blue box/door/yarn/mat/fishbowl, yellow necktie/fish, green variants. Poses (guideline p.12): peeking from door, sitting, in box, standing, stretched, playing with yarn, professor (glasses+tie), dizzy, sleepy, with fishbowl, etc. 68 SVGs in `QT/QT SVG/` and ~84 PNGs in `QT/All QT/`, 26 "QT in action" PNGs, 5 animated GIFs in `QT/QT Animations/`.
Usage: QT is the personality of empty states, loaders, success/celebration, errors, 404, onboarding. Never distort, recolour the body, add shadows, or put QT on top of dense data. One QT per view max. Respect `prefers-reduced-motion` (use static art).
Seasonal/festival SVGs (Christmas, Holi, Onam, 75th India, salute for india) exist: not for default UI; optional for a themed banner only if owner asks.

## 7. Photography (if any photo is ever used)
Warm, candid, Indian children/families/teachers, real emotion, natural light, bright colourful classrooms and homes; younger-child, older-child, parent and teacher-centric sets. No stock-posed corporate imagery. (The app is mostly quiz content, so this is rarely needed.)

## 8. School slide content style (separate School Content Style Guide PDF, for PPT export, informational for the app copy)
Causten Round; title 44 pt bold; content 28 pt, clues bold; centre-aligned; no bullets unless the question needs them; video icon beside videos; videos <= 40 s, full-screen; highest quality media; source/image links go in slide notes. Already implemented in the exporter; do not change.

## 9. Do / Don't summary
Do: black + cream + one dominant yellow, generous whitespace, round shapes (cards, buttons, chips radius large), thick friendly type, QT with a purpose, ® on every logo.
Don't: grey/blue-slate "enterprise" look, thin light fonts, hairline gradients, pure red/purple/pink accents, shadows on logo, new mascot drawings, invented taglines.
