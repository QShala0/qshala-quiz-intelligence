"""
Custom PPT Template Extractor

Analyzes a sample PowerPoint deck (.pptx) and extracts its visual template characteristics:
- Slide dimensions & Aspect Ratio (16:9 widescreen vs 4:3)
- Typography: Font families, font sizes, colors for Title, Headers, and Question Body
- Slide Layouts & Roles: Title/Cover Slide, Question Slide, Answer Slide, Divider Slide
- Geometry: Bounding boxes for question prompts, options, answers, and media placement
- Backgrounds & Artwork: Master layout backgrounds or slide backdrop pictures
- Generates a reusable clean master presentation (master.pptx) and preview thumbnail
"""

from __future__ import annotations

import io
import os
import re
import uuid
import zipfile
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from xml.etree import ElementTree as ET

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

logger = logging.getLogger(__name__)

# Standard directory where custom template files and master decks are stored
CUSTOM_TEMPLATES_DIR = Path("backend/storage/custom_templates")


def _rgb_to_hex(rgb: Optional[RGBColor]) -> Optional[str]:
    if rgb is None:
        return None
    try:
        return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    except Exception:
        return None


def _extract_theme_fonts(pptx_source: io.BytesIO | str | Path) -> Dict[str, str]:
    """Inspects ppt/theme/theme1.xml to discover major (header) and minor (body) font schemes."""
    theme_fonts = {"major": "", "minor": ""}
    try:
        with zipfile.ZipFile(pptx_source, "r") as z:
            theme_files = [n for n in z.namelist() if n.startswith("ppt/theme/theme") and n.endswith(".xml")]
            if not theme_files:
                return theme_fonts
            xml_content = z.read(theme_files[0])
            root = ET.fromstring(xml_content)
            ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
            
            major = root.find(".//a:fontScheme/a:majorFont/a:latin", ns)
            if major is not None and "typeface" in major.attrib:
                theme_fonts["major"] = major.attrib["typeface"]
                
            minor = root.find(".//a:fontScheme/a:minorFont/a:latin", ns)
            if minor is not None and "typeface" in minor.attrib:
                theme_fonts["minor"] = minor.attrib["typeface"]
    except Exception as e:
        logger.warning(f"Failed to read theme fonts from PPTX: {e}")
    return theme_fonts


def _is_fullscreen_picture(shape, slide_width_pts: float, slide_height_pts: float) -> bool:
    """Checks whether a shape is a full-bleed picture covering the slide (likely a background image)."""
    if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
        return False
    w_ratio = shape.width / slide_width_pts
    h_ratio = shape.height / slide_height_pts
    # covers at least 85% in width and height
    return w_ratio >= 0.85 and h_ratio >= 0.85


class TemplateExtractor:
    """
    Parses a sample PowerPoint deck, extracts typography, layout anchors, and background artwork,
    and produces a clean master template record.
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
        self.theme_fonts = _extract_theme_fonts(io.BytesIO(self.raw_bytes))

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

        # 3. Extract or identify background assets
        background_meta = self._extract_backgrounds(dest_dir)

        # 4. Generate clean master PPTX
        master_pptx_path = dest_dir / "master.pptx"
        self._generate_clean_master(master_pptx_path)

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
        for i in range(min(total_slides, 10)):
            slide = self.prs.slides[i]
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
                            sz = float(run.font.size.pt)
                            if sz >= 36.0:
                                title_sizes.append(sz)
                            elif 18.0 <= sz <= 32.0:
                                body_sizes.append(sz)
                        if run.font.color and run.font.color.type == 1:
                            hex_col = _rgb_to_hex(run.font.color.rgb)
                            if hex_col:
                                colors_seen[hex_col] = colors_seen.get(hex_col, 0) + 1

        # Determine dominant fonts
        sorted_fonts = sorted(font_counts.items(), key=lambda kv: kv[1], reverse=True)
        dominant_font = sorted_fonts[0][0] if sorted_fonts else (self.theme_fonts.get("minor") or "Causten Round Regular")
        major_theme = self.theme_fonts.get("major") or dominant_font

        # Title slide inspection (slide 0)
        title_color = "#1E293B"
        if total_slides > 0:
            first_slide = self.prs.slides[0]
            for s in first_slide.shapes:
                if s.has_text_frame and s.text_frame.text.strip():
                    for par in s.text_frame.paragraphs:
                        for run in par.runs:
                            if run.font.name:
                                title_font = run.font.name
                            if run.font.color and run.font.color.type == 1:
                                h = _rgb_to_hex(run.font.color.rgb)
                                if h:
                                    title_color = h
                            break
                        if title_font:
                            break
                if title_font:
                    break

        title_font = title_font or major_theme or "Causten Round"
        body_font = dominant_font or "Causten Round Regular"
        header_font = title_font or "Causten Round Bold"

        # Font sizes: user specifies uniform 28pt body
        avg_title_size = round(sum(title_sizes) / len(title_sizes), 1) if title_sizes else 44.0
        body_size = 28.0  # Style Guide Standard: uniform 28pt body

        # Most common color
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
        """Detects layout roles and safe geometry for question boxes and media areas."""
        layouts: Dict[str, Any] = {
            "cover_layout": None,
            "question_layout": None,
            "answer_layout": None,
            "geometry": {
                "question_box": {"left": 1.0, "top": 1.2, "width": 11.33, "height": 4.5},
                "media_box": {"left": 6.8, "top": 1.2, "width": 5.5, "height": 4.5},
                "answer_box": {"left": 1.0, "top": 1.2, "width": 11.33, "height": 4.5},
            }
        }

        total_slides = len(self.prs.slides)
        # Search for Question Slide (usually slide 2 or contains "Question")
        for i in range(min(total_slides, 8)):
            slide = self.prs.slides[i]
            slide_txt = ""
            for s in slide.shapes:
                if s.has_text_frame:
                    slide_txt += " " + s.text_frame.text.lower()
            
            # Check for question slide
            if "question" in slide_txt and not layouts["question_layout"]:
                layouts["question_layout"] = slide.slide_layout.name
                # Detect media box if an image exists
                for s in slide.shapes:
                    if s.shape_type == MSO_SHAPE_TYPE.PICTURE and not _is_fullscreen_picture(s, self.prs.slide_width, self.prs.slide_height):
                        layouts["geometry"]["media_box"] = {
                            "left": round(float(s.left.inches), 2),
                            "top": round(float(s.top.inches), 2),
                            "width": round(float(s.width.inches), 2),
                            "height": round(float(s.height.inches), 2),
                        }
                        break
            # Check for answer slide
            elif "answer" in slide_txt and not layouts["answer_layout"]:
                layouts["answer_layout"] = slide.slide_layout.name

        if total_slides > 0 and not layouts["cover_layout"]:
            layouts["cover_layout"] = self.prs.slides[0].slide_layout.name

        return layouts

    def _extract_backgrounds(self, dest_dir: Path) -> Dict[str, Any]:
        """Extracts fullscreen background graphics if present on slides."""
        backgrounds: Dict[str, Any] = {}
        total_slides = len(self.prs.slides)

        for i, role in enumerate(["cover_bg", "question_bg", "answer_bg"]):
            if i >= total_slides:
                break
            slide = self.prs.slides[i]
            for shape in slide.shapes:
                if _is_fullscreen_picture(shape, self.prs.slide_width, self.prs.slide_height):
                    try:
                        image = shape.image
                        ext = image.ext or "png"
                        filename = f"{role}.{ext}"
                        out_path = dest_dir / filename
                        with open(out_path, "wb") as f:
                            f.write(image.blob)
                        backgrounds[role] = filename
                        break
                    except Exception as e:
                        logger.warning(f"Could not extract background image from slide {i+1}: {e}")
        return backgrounds

    def _generate_clean_master(self, output_path: Path) -> None:
        """
        Creates a clean presentation retaining all slide masters, slide layouts,
        embedded fonts, color schemes, and vector artwork, without user questions.
        """
        clean_prs = Presentation(io.BytesIO(self.raw_bytes))
        
        # Remove transient question/answer slides if deck has more than 1 slide,
        # but keep slide masters and layouts pristine.
        # If slides have full-bleed layout art, we keep slide 0 as a cover reference.
        while len(clean_prs.slides) > 1:
            rId = clean_prs.slides._sldIdLst[1].rId
            clean_prs.part.drop_rel(rId)
            del clean_prs.slides._sldIdLst[1]

        # In slide 0 (cover), clear temporary test text while keeping shapes & formatting
        if len(clean_prs.slides) > 0:
            slide0 = clean_prs.slides[0]
            for shape in slide0.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    # Clear sample title text
                    shape.text_frame.text = ""

        clean_prs.save(str(output_path))

    def _generate_preview(self, dest_dir: Path, preview_path: Path) -> Optional[str]:
        """Extracts slide 1 picture or cover background to provide a visual thumbnail."""
        if (dest_dir / "cover_bg.png").exists():
            try:
                im = Image.open(dest_dir / "cover_bg.png")
                im.thumbnail((480, 270))
                im.save(preview_path, "PNG")
                return "preview.png"
            except Exception:
                pass

        # Check any picture on slide 0
        if len(self.prs.slides) > 0:
            slide0 = self.prs.slides[0]
            for shape in slide0.shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        im = Image.open(io.BytesIO(shape.image.blob))
                        im.thumbnail((480, 270))
                        im.save(preview_path, "PNG")
                        return "preview.png"
                    except Exception:
                        pass
        return None
