"""
Custom PPT Template Extractor

Analyzes a sample PowerPoint deck (.pptx) and extracts its visual template characteristics:
- Slide dimensions & Aspect Ratio (16:9 widescreen vs 4:3)
- Typography: Font families, font sizes, colors for Title, Headers, and Question Body
- Slide Theme & Palette: Full color scheme (dk1, lt1, accents 1-6) and font scheme from theme1.xml
- Slide Layouts & Roles: Title/Cover Slide, Question Slide, Answer Slide, Section Divider Slide
- Geometry: Bounding boxes for question prompts, options, answers, and media placement
- Backgrounds & Artwork: Master layout backgrounds, slide backgrounds, and full-bleed graphics
- Generates a reusable clean master presentation (master.pptx) and preview thumbnails
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import zipfile
import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from xml.etree import ElementTree as ET

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE

logger = logging.getLogger(__name__)

from backend.app.services.export.template_store import CUSTOM_TEMPLATES_DIR  # noqa: E402  (re-exported)

MAX_BG_PX = 2560          # longest side kept for extracted background art
MAX_SCAN_SLIDES = 60      # sample decks can be 100+ slides; the template is evident in the first dozens

NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _rgb_to_hex(rgb: Optional[RGBColor]) -> Optional[str]:
    if rgb is None:
        return None
    try:
        return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    except Exception:
        return None


def _extract_theme_info(raw_bytes: bytes) -> Dict[str, Any]:
    """Inspects ppt/theme/theme1.xml to discover theme colors (dk1, lt1, accents) and fonts."""
    theme_data: Dict[str, Any] = {
        "theme_name": "Office Theme",
        "color_scheme": {},
        "fonts": {"major": "", "minor": ""},
    }
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as z:
            theme_files = [n for n in z.namelist() if n.startswith("ppt/theme/theme") and n.endswith(".xml")]
            if not theme_files:
                return theme_data

            root = ET.fromstring(z.read(theme_files[0]))
            for cs in root.iter(f"{NS_A}clrScheme"):
                theme_data["theme_name"] = cs.attrib.get("name", "Office Theme")
                for child in cs:
                    tag = child.tag.split("}")[-1]
                    srgb = child.find(f"{NS_A}srgbClr")
                    sys = child.find(f"{NS_A}sysClr")
                    if srgb is not None and "val" in srgb.attrib:
                        theme_data["color_scheme"][tag] = "#" + srgb.attrib["val"]
                    elif sys is not None:
                        last_clr = sys.attrib.get("lastClr")
                        theme_data["color_scheme"][tag] = "#" + (last_clr if last_clr else "FFFFFF")

            for fs in root.iter(f"{NS_A}fontScheme"):
                maj = fs.find(f"{NS_A}majorFont/{NS_A}latin")
                min_ = fs.find(f"{NS_A}minorFont/{NS_A}latin")
                if maj is not None and "typeface" in maj.attrib:
                    theme_data["fonts"]["major"] = maj.attrib["typeface"]
                if min_ is not None and "typeface" in min_.attrib:
                    theme_data["fonts"]["minor"] = min_.attrib["typeface"]
    except Exception as e:
        logger.warning(f"Failed to read theme info from PPTX: {e}")
    return theme_data


def _extract_picture_blob(shape) -> Optional[Tuple[bytes, str]]:
    """Extracts raw image bytes and file extension from a shape (PICTURE or shape with blipFill)."""
    try:
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            return shape.image.blob, shape.image.ext or "png"
    except Exception:
        pass

    try:
        blip = shape._element.find(f".//{NS_A}blip")
        if blip is not None:
            rId = blip.attrib.get(f"{NS_R}embed")
            if rId and hasattr(shape.part, "related_part"):
                rel_part = shape.part.related_part(rId)
                ext = getattr(rel_part.partname, "ext", "png") or "png"
                return rel_part.blob, ext
    except Exception:
        pass
    return None


def _find_fullscreen_picture_in_shapes(shapes, slide_width_pts: float, slide_height_pts: float, threshold: float = 0.70) -> Optional[Tuple[bytes, str]]:
    """Finds any picture shape that covers at least threshold of slide width and height."""
    for s in shapes:
        try:
            if s.shape_type == MSO_SHAPE_TYPE.PICTURE:
                w_ratio = float(s.width) / float(slide_width_pts)
                h_ratio = float(s.height) / float(slide_height_pts)
                if w_ratio >= threshold and h_ratio >= threshold:
                    data = _extract_picture_blob(s)
                    if data:
                        return data
        except Exception:
            continue
    return None


def _blob_sha(blob: Tuple[bytes, str]) -> str:
    return hashlib.sha1(blob[0]).hexdigest()


def _slide_cover_pictures(slide, slide_width, slide_height, threshold: float = 0.90) -> List[Tuple[bytes, str]]:
    """Every picture ON the slide that covers (almost) the whole slide."""
    found: List[Tuple[bytes, str]] = []
    for s in slide.shapes:
        try:
            if s.shape_type == MSO_SHAPE_TYPE.PICTURE and \
                    float(s.width) / float(slide_width) >= threshold and float(s.height) / float(slide_height) >= threshold:
                data = _extract_picture_blob(s)
                if data:
                    found.append(data)
        except Exception:
            continue
    return found


def _template_art_blob(slide, slide_width, slide_height) -> Optional[Tuple[bytes, str]]:
    """Artwork owned by the TEMPLATE (slide layout / slide master shapes or a <p:bg> picture fill). This is the
    reliable source: it is design, not question content."""
    layout = getattr(slide, "slide_layout", None)
    master = getattr(layout, "slide_master", None) if layout is not None else None
    for holder in (layout, master):
        if holder is None:
            continue
        pic = _find_fullscreen_picture_in_shapes(holder.shapes, slide_width, slide_height)
        if pic:
            return pic
    for part_obj in (slide, layout, master):
        if part_obj is None or not hasattr(part_obj, "_element"):
            continue
        try:
            bg_blip = part_obj._element.find(f".//{NS_P}bg//{NS_A}blip")
            if bg_blip is not None:
                rId = bg_blip.attrib.get(f"{NS_R}embed")
                if rId and hasattr(part_obj.part, "related_part"):
                    rel = part_obj.part.related_part(rId)
                    ext = getattr(rel.partname, "ext", "png") or "png"
                    return rel.blob, ext
        except Exception:
            pass
    return None


def _get_slide_background_blob(slide, slide_width, slide_height, repeated: Optional[set] = None,
                               allow_unique: bool = False) -> Optional[Tuple[bytes, str]]:
    """Background art for a slide. Template-owned art (layout/master/<p:bg>) always wins. A full-slide picture sitting
    on the slide itself only counts when it is REPEATED across slides (so it is template design) or the caller
    explicitly allows a one-off (cover / divider slides). Otherwise a large question photo gets mistaken for the
    background."""
    art = _template_art_blob(slide, slide_width, slide_height)
    if art:
        return art
    for blob in _slide_cover_pictures(slide, slide_width, slide_height):
        if allow_unique or (repeated is not None and _blob_sha(blob) in repeated):
            return blob
    return None


def _normalise_image(blob_bytes: bytes) -> Optional[Tuple[bytes, str]]:
    """Make an extracted background loadable by python-pptx and the exporter: PNG or JPEG, at most MAX_BG_PX.
    GIF/WEBP/BMP/TIFF are converted; formats PIL cannot read (EMF/WMF) return None so the role is skipped
    instead of saving a file nothing can load."""
    try:
        with Image.open(io.BytesIO(blob_bytes)) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
            if fmt in ("PNG", "JPEG") and max(w, h) <= MAX_BG_PX:
                return blob_bytes, ("png" if fmt == "PNG" else "jpg")
            im.load()
            if max(w, h) > MAX_BG_PX:
                im.thumbnail((MAX_BG_PX, MAX_BG_PX), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            if fmt == "JPEG":
                im.convert("RGB").save(out, "JPEG", quality=92)
                return out.getvalue(), "jpg"
            has_alpha = "A" in im.getbands() or im.mode == "P"
            im.convert("RGBA" if has_alpha else "RGB").save(out, "PNG", optimize=True)
            return out.getvalue(), "png"
    except Exception as e:
        logger.warning("Background image could not be decoded (%s); skipping", e)
        return None


def _short_answer_slide(slide) -> bool:
    """A divider slide: the only text is the word 'Answer' (or 'Answers')."""
    txt = " ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame).strip().lower()
    return bool(txt) and len(txt) <= 30 and txt.startswith("answer")


class TemplateExtractor:
    """
    Parses a sample PowerPoint deck, extracts typography, layout anchors, theme palettes,
    and background artwork, and produces a clean master template record.
    """

    def __init__(self, pptx_data: bytes | io.BytesIO | str | Path):
        if isinstance(pptx_data, bytes):
            self.raw_bytes = pptx_data
            self.stream = io.BytesIO(pptx_data)
        elif isinstance(pptx_data, io.BytesIO):
            self.stream = pptx_data
            self.raw_bytes = pptx_data.getvalue()
        else:
            p = Path(pptx_data)
            with open(p, "rb") as f:
                self.raw_bytes = f.read()
            self.stream = io.BytesIO(self.raw_bytes)

        self.prs = Presentation(self.stream)
        self.slide_width_in = round(float(self.prs.slide_width.inches), 3)
        self.slide_height_in = round(float(self.prs.slide_height.inches), 3)
        ratio = self.slide_width_in / self.slide_height_in if self.slide_height_in else 1.777
        self.aspect_ratio = "16:9" if ratio >= 1.5 else "4:3"
        self.theme_info = _extract_theme_info(self.raw_bytes)
        self.theme_fonts = self.theme_info.get("fonts", {"major": "", "minor": ""})

    def extract(
        self,
        name: str,
        category: str = "qshala_school",
        description: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Runs the extraction pipeline, saves clean master files, and returns model-ready metadata.
        """
        template_id = str(uuid.uuid4())
        dest_dir = output_dir or (CUSTOM_TEMPLATES_DIR / template_id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 1. Analyze typography across deck
        typography = self._analyze_typography()

        # 2. Analyze slide layouts (Cover, Question, Answer)
        layouts_meta = self._analyze_layouts()

        # 3. Extract background assets from slides, layouts, and masters
        background_meta = self._extract_backgrounds(dest_dir, layouts_meta)

        # 4. Generate clean master PPTX
        master_pptx_path = dest_dir / "master.pptx"
        self._generate_clean_master(master_pptx_path, layouts_meta)

        # 5. Extract or create thumbnail preview
        preview_path = dest_dir / "preview.png"
        preview_rel_path = self._generate_preview(dest_dir, preview_path)

        # 6. Assemble complete template profile
        return {
            "id": template_id,
            "name": name.strip(),
            "category": category.strip().lower() if category else "qshala_school",
            "description": description.strip() if description else None,
            "storage_path": str(master_pptx_path.resolve()),
            "file_size_bytes": os.path.getsize(master_pptx_path),
            "preview_image_path": str(preview_path.resolve()) if preview_path.exists() else None,
            "aspect_ratio": self.aspect_ratio,
            "slide_width_in": self.slide_width_in,
            "slide_height_in": self.slide_height_in,
            "title_font": typography["title_font"],
            "title_size_pt": typography["title_size_pt"],
            "title_color_hex": typography["title_color_hex"],
            "body_font": typography["body_font"],
            "body_size_pt": typography["body_size_pt"],
            "body_color_hex": typography["body_color_hex"],
            "header_font": typography["header_font"],
            "header_size_pt": typography["header_size_pt"],
            "header_color_hex": typography["header_color_hex"],
            "layout_metadata": {
                "layouts": layouts_meta,
                "backgrounds": background_meta,
                "theme": self.theme_info,
                "theme_fonts": self.theme_fonts,
            },
        }

    def _analyze_typography(self) -> Dict[str, Any]:
        """Scans shapes across slides to find dominant font families, sizes, and colors."""
        font_counts: Dict[str, int] = {}
        title_sizes: List[float] = []
        body_sizes: List[float] = []
        colors_seen: Dict[str, int] = {}
        title_font = None
        body_font = None
        header_font = None

        total_slides = len(self.prs.slides)
        for i, slide in enumerate(self.prs.slides):
            if i >= 15:
                break
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                tf = shape.text_frame
                for par in tf.paragraphs:
                    txt = par.text.strip()
                    if not txt:
                        continue
                    for run in par.runs:
                        fn = run.font.name
                        if fn:
                            font_counts[fn] = font_counts.get(fn, 0) + len(run.text)
                        if run.font.size:
                            pt_size = round(run.font.size.pt, 1)
                            if pt_size >= 32.0:
                                title_sizes.append(pt_size)
                            elif 14.0 <= pt_size <= 30.0:
                                body_sizes.append(pt_size)
                        if run.font.color and getattr(run.font.color, "type", None) == 1:
                            hex_c = _rgb_to_hex(run.font.color.rgb)
                            if hex_c and hex_c not in ("#FFFFFF", "#000000"):
                                colors_seen[hex_c] = colors_seen.get(hex_c, 0) + 1

        # Determine dominant fonts
        sorted_fonts = sorted(font_counts.items(), key=lambda kv: kv[1], reverse=True)
        dominant_font = sorted_fonts[0][0] if sorted_fonts else (self.theme_fonts.get("minor") or "Causten Round Regular")
        major_theme = self.theme_fonts.get("major") or dominant_font

        # Title slide inspection
        title_color = "#1E293B"
        for i, slide in enumerate(self.prs.slides):
            if i >= 5:
                break
            layout_name = slide.slide_layout.name.lower()
            if "title" in layout_name or "cover" in layout_name or i == 0:
                for s in slide.shapes:
                    if s.has_text_frame and s.text_frame.text.strip():
                        for par in s.text_frame.paragraphs:
                            for run in par.runs:
                                if run.font.name and not title_font:
                                    title_font = run.font.name
                                if run.font.color and getattr(run.font.color, "type", None) == 1:
                                    h = _rgb_to_hex(run.font.color.rgb)
                                    if h:
                                        title_color = h
                                if title_font:
                                    break
                            if title_font:
                                break
                    if title_font:
                        break
            if title_font:
                break

        title_font = title_font or major_theme or "Causten Round"
        body_font = dominant_font or "Causten Round Regular"
        header_font = title_font or "Causten Round Bold"

        avg_title_size = round(sum(title_sizes) / len(title_sizes), 1) if title_sizes else 44.0
        body_size = 28.0  # Style Guide Standard: uniform 28pt body

        sorted_colors = sorted(colors_seen.items(), key=lambda kv: kv[1], reverse=True)
        dominant_color = sorted_colors[0][0] if sorted_colors else "#0F172A"

        return {
            "title_font": title_font,
            "title_size_pt": avg_title_size,
            "title_color_hex": title_color,
            "body_font": body_font,
            "body_size_pt": body_size,
            "body_color_hex": dominant_color,
            "header_font": header_font,
            "header_size_pt": 36.0,
            "header_color_hex": "#000000",
        }

    def _analyze_layouts(self) -> Dict[str, Any]:
        """Detects layout roles from how the sample deck actually uses its layouts (not from guessing at slide text)."""
        layouts: Dict[str, Any] = {
            "cover_layout": None,
            "question_layout": None,
            "answer_layout": None,
            "divider_layout": None,
            "geometry": {
                "question_box": {"left": 1.0, "top": 1.2, "width": 11.33, "height": 4.5},
                "media_box": {"left": 6.8, "top": 1.2, "width": 5.5, "height": 4.5},
                "answer_box": {"left": 1.0, "top": 1.2, "width": 11.33, "height": 4.5},
            },
        }
        slides = list(self.prs.slides)[:MAX_SCAN_SLIDES]
        all_layouts = self._all_layouts()

        # Cover: a layout named like a title slide; else whatever the first slide uses; else the first layout.
        for l in all_layouts:
            lname = l.name.lower()
            if "title slide" in lname or "cover" in lname:
                layouts["cover_layout"] = l.name
                break
        if not layouts["cover_layout"] and slides:
            layouts["cover_layout"] = slides[0].slide_layout.name
        if not layouts["cover_layout"] and all_layouts:
            layouts["cover_layout"] = all_layouts[0].name

        # Divider: the layout of a slide that only says "Answer".
        for sl in slides:
            if _short_answer_slide(sl):
                layouts["divider_layout"] = sl.slide_layout.name
                break

        # Question: the layout the deck uses MOST for its content slides (cover and divider layouts excluded).
        skip = {layouts["cover_layout"], layouts["divider_layout"]}
        usage = Counter(sl.slide_layout.name for sl in slides[1:] if sl.slide_layout.name not in skip)
        if usage:
            layouts["question_layout"] = usage.most_common(1)[0][0]
            media = self._first_content_picture_box(slides[1:], layouts["question_layout"])
            if media:
                layouts["geometry"]["media_box"] = media

        # Fallbacks by layout name.
        if not layouts["question_layout"]:
            for key in ("title and content", "title only", "blank", "content"):
                hit = next((l.name for l in all_layouts if key in l.name.lower()), None)
                if hit:
                    layouts["question_layout"] = hit
                    break
        if not layouts["divider_layout"]:
            for l in all_layouts:
                lname = l.name.lower()
                if "section" in lname or "divider" in lname or "header" in lname:
                    layouts["divider_layout"] = l.name
                    break
        for l in all_layouts:
            if "answer" in l.name.lower():
                layouts["answer_layout"] = l.name
                break
        return layouts

    def _all_layouts(self) -> list:
        """Layouts of EVERY slide master (prs.slide_layouts only returns the first master's)."""
        out = []
        for master in self.prs.slide_masters:
            out.extend(master.slide_layouts)
        return out

    def _first_content_picture_box(self, slides, layout_name: str) -> Optional[Dict[str, float]]:
        """Box (inches) of the first non-background picture on a slide that uses `layout_name`."""
        for sl in slides:
            if sl.slide_layout.name != layout_name:
                continue
            for s in sl.shapes:
                try:
                    if s.shape_type == MSO_SHAPE_TYPE.PICTURE and not _find_fullscreen_picture_in_shapes(
                            [s], self.prs.slide_width, self.prs.slide_height):
                        return {
                            "left": round(float(s.left.inches), 2),
                            "top": round(float(s.top.inches), 2),
                            "width": round(float(s.width.inches), 2),
                            "height": round(float(s.height.inches), 2),
                        }
                except Exception:
                    continue
        return None

    def _extract_backgrounds(self, dest_dir: Path, layouts_meta: Dict[str, Any]) -> Dict[str, str]:
        """
        Extracts background art for Cover, Question, Answer Divider and Answer Reveal.
        Template-owned art (layout / master / <p:bg>) is preferred; a picture on a slide is only trusted when it is
        repeated across slides (template design) or is the one-off cover / divider slide. Everything is saved as
        PNG or JPEG so the exporter can always load it.
        """
        backgrounds: Dict[str, str] = {}
        sw = self.prs.slide_width
        sh_ = self.prs.slide_height
        slides = list(self.prs.slides)[:MAX_SCAN_SLIDES]

        # Which full-slide pictures recur across slides? Those are template art, not question content.
        seen_on: Counter = Counter()
        for sl in slides:
            for sha in {_blob_sha(b) for b in _slide_cover_pictures(sl, sw, sh_)}:
                seen_on[sha] += 1
        repeated = {sha for sha, n in seen_on.items() if n >= 2}

        cover_name = layouts_meta.get("cover_layout")
        cover_blob: Optional[Tuple[bytes, str]] = None
        for i, sl in enumerate(slides[:8]):
            if i == 0 or sl.slide_layout.name == cover_name:
                cover_blob = _get_slide_background_blob(sl, sw, sh_, repeated, allow_unique=True)
                if cover_blob:
                    break
        cover_sha = _blob_sha(cover_blob) if cover_blob else None

        divider_blob: Optional[Tuple[bytes, str]] = None
        content_votes: Counter = Counter()
        content_blobs: Dict[str, Tuple[bytes, str]] = {}
        for i, sl in enumerate(slides):
            if _short_answer_slide(sl):
                if not divider_blob:
                    divider_blob = _get_slide_background_blob(sl, sw, sh_, repeated, allow_unique=True)
                continue
            if i == 0:
                continue
            blob = _get_slide_background_blob(sl, sw, sh_, repeated, allow_unique=False)
            if blob and _blob_sha(blob) != cover_sha:
                sha = _blob_sha(blob)
                content_votes[sha] += 1
                content_blobs[sha] = blob

        ranked = [content_blobs[sha] for sha, _ in content_votes.most_common()]
        question_blob = ranked[0] if ranked else None
        answer_reveal_blob = ranked[1] if len(ranked) > 1 else None

        # Fall back to artwork that lives on layouts directly.
        if not (cover_blob and question_blob and divider_blob):
            seen = {_blob_sha(b) for b in (cover_blob, question_blob, divider_blob) if b}
            for l in self._all_layouts():
                blob = _find_fullscreen_picture_in_shapes(l.shapes, sw, sh_)
                if not blob or _blob_sha(blob) in seen:
                    continue
                seen.add(_blob_sha(blob))
                lname = l.name.lower()
                if not cover_blob and ("title" in lname or "cover" in lname):
                    cover_blob = blob
                elif not divider_blob and ("section" in lname or "header" in lname or "divider" in lname):
                    divider_blob = blob
                elif not question_blob:
                    question_blob = blob

        role_map = [
            ("cover_bg", cover_blob),
            ("question_bg", question_blob or cover_blob),
            ("answer_intro_bg", divider_blob or question_blob or cover_blob),
            ("answer_bg", answer_reveal_blob or divider_blob or question_blob or cover_blob),
        ]
        for role_name, blob_data in role_map:
            if not blob_data:
                continue
            norm = _normalise_image(blob_data[0])
            if not norm:
                continue
            blob_bytes, ext = norm
            filename = f"{role_name}.{ext}"
            (dest_dir / filename).write_bytes(blob_bytes)
            backgrounds[role_name] = filename

            # Lightweight web-scaled JPEG (~80-150 KB) so the browser preview never freezes.
            try:
                with Image.open(io.BytesIO(blob_bytes)) as im:
                    im.thumbnail((1280, 720), Image.Resampling.LANCZOS)
                    im_rgb = im.convert("RGB")
                    base_role = role_name.replace("_bg", "").replace("_intro", "")
                    im_rgb.save(str(dest_dir / f"web_{base_role}.jpg"), "JPEG", quality=85)
                    if role_name == "answer_intro_bg":
                        im_rgb.save(str(dest_dir / "web_divider.jpg"), "JPEG", quality=85)
            except Exception as e:
                logger.debug(f"Could not generate web thumbnail for {role_name}: {e}")
        return backgrounds

    def _generate_clean_master(self, output_path: Path, layouts_meta: Dict[str, Any]) -> None:
        """
        A reusable master: every slide master, layout, theme, colour scheme and vector artwork, plus ONLY the two real
        slides the exporter reuses as they are: the title slide (cover) and the "Answer" divider. Every other sample
        slide is dropped, because keeping them dragged their photos, music and video into the file (a 90 MB sample
        deck produced a 37 MB master that was re-read on every export).

        The kept slides' positions are recorded in layouts_meta as cover_slide_index / divider_slide_index (positions
        in the saved master, or None). A template saved before this existed has neither key and exports from layouts.
        """
        clean_prs = Presentation(io.BytesIO(self.raw_bytes))
        slides = list(clean_prs.slides)

        divider_i = next((i for i, sl in enumerate(slides) if i > 0 and _short_answer_slide(sl)), None)
        cover_i = 0 if slides and not _short_answer_slide(slides[0]) and len(slides[0].shapes) > 0 else None
        keep = [i for i in (cover_i, divider_i) if i is not None]

        for i in range(len(slides) - 1, -1, -1):
            if i in keep:
                continue
            for sld_id in list(clean_prs.slides._sldIdLst):
                if clean_prs.part.related_part(sld_id.rId) is slides[i].part:
                    clean_prs.part.drop_rel(sld_id.rId)
                    clean_prs.slides._sldIdLst.remove(sld_id)
                    break

        layouts_meta["cover_slide_index"] = keep.index(cover_i) if cover_i is not None else None
        layouts_meta["divider_slide_index"] = keep.index(divider_i) if divider_i is not None else None
        clean_prs.save(str(output_path))

    def _generate_preview(self, dest_dir: Path, preview_path: Path) -> Optional[str]:
        """Creates a visual thumbnail preview for the template."""
        # Check cover_bg image first
        for name in ["cover_bg.png", "cover_bg.jpg", "cover_bg.jpeg"]:
            cand = dest_dir / name
            if cand.exists():
                try:
                    im = Image.open(cand)
                    im.thumbnail((480, 270))
                    im.save(preview_path, "PNG")
                    return "preview.png"
                except Exception as e:
                    logger.warning(f"Failed to generate preview from {name}: {e}")

        # Check any slide shape image
        for slide in self.prs.slides:
            for shape in slide.shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        im = Image.open(io.BytesIO(shape.image.blob))
                        im.thumbnail((480, 270))
                        im.save(preview_path, "PNG")
                        return "preview.png"
                    except Exception:
                        pass

        # Fallback: create a crisp 16:9 theme canvas
        try:
            bg_hex = (self.theme_info.get("color_scheme") or {}).get("lt1", "#FFFFFF") if self.theme_info else "#FFFFFF"
            if not bg_hex.startswith("#") or len(bg_hex) != 7:
                bg_hex = "#FFFFFF"
            r = int(bg_hex[1:3], 16)
            g = int(bg_hex[3:5], 16)
            b = int(bg_hex[5:7], 16)
            im = Image.new("RGB", (480, 270), (r, g, b))
            im.save(preview_path, "PNG")
            return "preview.png"
        except Exception as e:
            logger.warning(f"Failed to create fallback canvas preview: {e}")
        return None
