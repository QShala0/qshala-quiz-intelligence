# Custom PPT templates (extract from a sample deck)

Flow: upload a sample .pptx -> `TemplateExtractor` -> template folder -> saved to R2 -> row in `custom_ppt_templates`.
On export the folder is used as the deck's master (layouts, theme, artwork).

## Where the files live
- Local cache: `backend/storage/custom_templates/<id>/` (override with `CUSTOM_TEMPLATES_DIR`). Wiped on every Cloud Run restart/deploy.
- Durable copy: R2 `custom-templates/<id>/<file>` (same bucket as media). `layout_metadata.stored_files` lists the files.
- Anything that reads a template calls `template_store.ensure_local_dir(tpl)`, which restores the folder from R2 when the cache is gone.
- Without R2 configured (local dev) only local disk is used and a warning is logged.
- Templates saved before this change have no `stored_files`; if their local folder is gone they report `files_ok: false` and
  must be re-uploaded (exports return 409 "Upload the sample deck again" instead of silently using the default deck).

## Rules the extractor follows
- Master = layouts + theme + artwork with ZERO slides (a 90 MB sample deck used to give a 37 MB master).
- Layout roles come from how the sample deck uses its layouts (most-used non-cover layout = question layout; the slide that only
  says "Answer" = divider). Layouts are searched across ALL slide masters (`prs.slide_layouts` only lists the first).
- Background art: layout/master/`<p:bg>` first; a picture on a slide only counts if it repeats across slides (or is the one-off
  cover/divider), so a big question photo is never mistaken for the background. Images are saved as PNG/JPEG, max 2560 px.

## Rules the exporter follows
- A chosen `template_id` that is missing -> 404; files unrecoverable or master unreadable -> 409. Never a silent default deck.
- A custom template never inherits the built-in QShala/ZVQ artwork.
- Slides are built on a 13.333 x 7.5 in canvas, then `slide_fit.fit_deck_to_slide` maps them onto the template's real size
  (10x5.625, 4:3, ...): uniform scale + letterbox, backgrounds snapped to full bleed, font sizes scaled, nothing left outside the slide.
- Empty placeholders ("Click to add title") are removed.

Tests: `backend/tests/test_template_robustness.py`.
