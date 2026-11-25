---
name: render-check
description: Verify an exported quiz PPTX or a UI change by actually rendering it and looking (LibreOffice, pdftoppm, pdffonts). Use before claiming slides or UI are done.
---

Never claim slides are right from code alone. For a PPTX:

1. Export the deck from the running app or the exporter in `backend/app/services/export`, into the scratchpad dir, never the repo.
2. `soffice --headless --convert-to pdf <file.pptx>` then `pdftoppm -png -r 80 <file.pdf> <prefix>`; open the PNGs and look at the cover, a question slide, an answer slide, the Answer divider, and any STEPPED_CLUE / CONNECT slides.
3. `pdffonts <file.pdf>`: Causten Round (and Causten Round Bold for headings) must be the fonts used. Report any fallback font.

Check against the domain rules: the answer never appears on a question slide, answer text is black not green, artwork comes from the slide master (not pictures on slides), STEPPED_CLUE has one clue per slide, CONNECT grid is on one slide with the answer on its own slide, timer media is never a clue.

For UI: run the frontend (`cd frontend && npm run dev`), open it in the built-in browser, screenshot desktop and phone width, collapsed and open states, and compare with `docs/DESIGN.md` tokens (warm paper surfaces, no pure white or black, minimal, no AI-looking bubble cards). Also run `npx tsc --noEmit` and eslint on touched files with no new findings.

Report what you looked at and anything you could not verify (for example fonts not installed on this machine).
