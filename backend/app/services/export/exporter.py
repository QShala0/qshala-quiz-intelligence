import re
import csv
import io
import json
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from PIL import Image

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------------------------- media safety
# POST /quizzes/export takes media references straight from the request body, so the exporter must not be a way
# to make the server fetch internal URLs or embed arbitrary local files into the returned deck.

_HOST_CACHE: Dict[str, Tuple[float, bool]] = {}
_HOST_CACHE_TTL_S = 60.0


def _host_is_public(host: str) -> bool:
    """_resolve_host_is_public, remembered for a minute: an export checks the same few media hosts for every image
    (and getaddrinfo blocks with no timeout), so one lookup per host per minute instead of two per picture."""
    import time
    key = host.lower()
    now = time.monotonic()
    hit = _HOST_CACHE.get(key)
    if hit is not None and now - hit[0] < _HOST_CACHE_TTL_S:
        return hit[1]
    result = _resolve_host_is_public(host)
    _HOST_CACHE[key] = (now, result)
    return result


def _resolve_host_is_public(host: str) -> bool:
    """True when every address `host` resolves to is globally routable. Anything else (loopback, private,
    link-local such as the cloud metadata address, shared 100.64.0.0/10, reserved, multicast, unspecified) is not."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return False
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped                      # ::ffff:127.0.0.1 is judged as 127.0.0.1
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _url_allowed(url: str) -> bool:
    """Only http(s) URLs to public hosts. EXPORT_ALLOWED_MEDIA_HOSTS (comma separated) narrows this to exactly
    those hosts, e.g. your R2 public domain."""
    import os
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    allowed = [h.strip().lower() for h in os.getenv("EXPORT_ALLOWED_MEDIA_HOSTS", "").split(",") if h.strip()]
    if allowed:
        return parsed.hostname.lower() in allowed
    return _host_is_public(parsed.hostname)


def _make_safe_opener():
    import urllib.error
    import urllib.request

    class _SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not _url_allowed(newurl):
                raise urllib.error.URLError(f"redirect to a non-public or disallowed host refused: {newurl[:120]}")
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(_SafeRedirect)


_OPENER = _make_safe_opener()


def _cache_name(url: str, filename: str) -> str:
    """Temp-file name for a downloaded URL. The old cache was keyed by basename alone, so two decks that both have a
    slide_3_img_1.png shared one file and the second deck's export got the first deck's picture."""
    import hashlib
    return f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}_{filename}"


def _safe_sub(sub: str) -> bool:
    """A relative path below a media root must not climb out of it."""
    from pathlib import PurePosixPath
    parts = PurePosixPath(sub.replace("\\", "/")).parts
    return bool(parts) and ".." not in parts and not PurePosixPath(sub.replace("\\", "/")).is_absolute()


def _within_media_roots(path: Path) -> bool:
    """A direct local path is only embedded when it sits inside the app's own storage / public media folders."""
    from backend.app.config import settings
    repo = Path(__file__).resolve().parents[4]
    roots = [settings.STORAGE_DIR, settings.SLIDES_DIR, settings.UPLOAD_DIR,
             repo / "frontend" / "public", repo / "backend" / "storage", repo / "storage"]
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            r = Path(root).resolve()
        except (OSError, RuntimeError):
            continue
        if resolved == r or r in resolved.parents:
            return True
    return False


def _download_url_to_file(url: str, dest_dir: Path) -> Optional[Path]:
    """
    Downloads a URL (R2 public URL or any HTTPS/HTTP media URL) to a local temp file.
    Returns the local path or None on failure.
    """
    try:
        import urllib.request
        import urllib.error
        from urllib.parse import urlparse, unquote

        parsed = urlparse(url)
        filename = Path(unquote(parsed.path)).name
        if not filename:
            filename = "media_file"

        if not _url_allowed(url):
            logger.warning(f"Refusing to fetch media URL (not a public http(s) host): {url[:120]}")
            return None

        dest_path = dest_dir / _cache_name(url, filename)
        if dest_path.exists() and dest_path.stat().st_size > 0:
            return dest_path

        logger.info(f"Downloading media: {url} -> {dest_path}")
        # urlretrieve's default opener sends "User-Agent: Python-urllib/3.x" -- a signature
        # commonly blocked outright by CDN/edge bot-protection, including (per real-world
        # reports) Cloudflare's. R2's public pub-*.r2.dev URLs hit exactly this: every
        # download here was 403ing even with the bucket's Public Development URL access
        # enabled. A normal browser-like User-Agent is the standard fix for this class of
        # 403 and costs nothing to set.
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; QShalaExporter/1.0)"})
        with _OPENER.open(req, timeout=30) as resp, open(dest_path, "wb") as out_f:
            shutil.copyfileobj(resp, out_f)

        if dest_path.exists() and dest_path.stat().st_size > 0:
            return dest_path
        return None
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return None


def _resolve_media_path(rel_url: Optional[str], download_dir: Optional[Path] = None) -> Optional[Path]:
    """Resolves local filesystem paths for images, audio, and video files across storage and static directories."""
    if not rel_url or not isinstance(rel_url, str):
        return None
    from backend.app.config import settings
    clean_url = rel_url.strip()

    # 0. Handle HTTPS/HTTP URLs (R2 media, external URLs)
    if clean_url.startswith("https://") or clean_url.startswith("http://"):
        if not _url_allowed(clean_url):
            logger.warning(f"Refusing media URL (not a public http(s) host): {clean_url[:120]}")
            return None
        if download_dir:
            downloaded = _download_url_to_file(clean_url, download_dir)
            if downloaded and downloaded.exists() and downloaded.stat().st_size > 0:
                return downloaded
            # Direct S3 fallback if HTTP request failed (e.g. 403 or network issue)
            try:
                from backend.app.services.storage.r2_storage import get_media_storage
                r2 = get_media_storage()
                if r2 is not None:
                    dest = download_dir / _cache_name(clean_url, Path(clean_url.split("?")[0]).name)
                    return r2.download_to_file(clean_url, dest)
            except Exception as e:
                logger.warning(f"R2 direct S3 download fallback failed for {clean_url}: {e}")
            return None
        else:
            logger.warning(f"HTTPS URL found but no download_dir provided: {clean_url}")
            return None

    # 1. Slide extracts directory (/static/slides/...)
    if clean_url.startswith("/static/slides/"):
        sub = clean_url[len("/static/slides/"):].lstrip("/\\")
        if not _safe_sub(sub):
            return None
        candidates = [
            settings.SLIDES_DIR / sub,
            Path("frontend/public/static/slides") / sub,
            Path(__file__).resolve().parents[4] / "frontend" / "public" / "static" / "slides" / sub,
            Path(r"c:\Users\aaksh\Downloads\qshala-pq-worktree\frontend\public\static\slides") / sub,
            Path(r"c:\Users\aaksh\Downloads\qshala\frontend\public\static\slides") / sub,
            Path(r"c:\Users\aaksh\Downloads\qshala-pq-worktree\backend\storage\slides") / sub,
            Path(r"c:\Users\aaksh\Downloads\qshala\backend\storage\slides") / sub,
        ]
        for p_cand in candidates:
            if p_cand.exists() and p_cand.is_file():
                return p_cand
        # Fallback to R2 if not found locally
        if download_dir:
            try:
                from backend.app.services.storage.r2_storage import get_media_storage
                r2 = get_media_storage()
                if r2 is not None:
                    key = f"slides/{sub}"
                    dest = download_dir / _cache_name(key, Path(sub).name)
                    try:
                        p = r2.download_to_file(key, dest)
                        if p.exists() and p.stat().st_size > 0:
                            return p
                    except Exception:
                        pass
                    return _download_url_to_file(f"{r2.public_base_url}/{key}", download_dir)
            except Exception as e:
                logger.warning(f"R2 fallback failed for {clean_url}: {e}")
    # 2. Storage directory general (/static/...)
    elif clean_url.startswith("/static/"):
        sub = clean_url.lstrip("/static/").lstrip("/\\")
        if not _safe_sub(sub):
            return None
        candidates = [
            settings.STORAGE_DIR / sub,
            Path("frontend/public/static") / sub,
            Path(__file__).resolve().parents[4] / "frontend" / "public" / "static" / sub,
            Path(r"c:\Users\aaksh\Downloads\qshala-pq-worktree\frontend\public\static") / sub,
            Path(r"c:\Users\aaksh\Downloads\qshala\frontend\public\static") / sub,
        ]
        for p_cand in candidates:
            if p_cand.exists() and p_cand.is_file():
                return p_cand
            
    # 3. Public media videos directory (/media/videos/...)
    if clean_url.startswith("/media/videos/") or clean_url.startswith("media/videos/"):
        name = Path(clean_url).name
        candidates = [
            Path("frontend/public/media/videos") / name,
            Path(r"c:\Users\aaksh\Downloads\qshala\frontend\public\media\videos") / name,
            Path(r"c:\Users\aaksh\Downloads\qshala-pq-worktree\frontend\public\media\videos") / name,
        ]
        for p_cand in candidates:
            if p_cand.exists() and p_cand.is_file():
                return p_cand
    elif clean_url.startswith("/media/") or clean_url.startswith("media/"):
        name = Path(clean_url).name
        candidates = [
            Path("frontend/public/media") / name,
            Path(r"c:\Users\aaksh\Downloads\qshala\frontend\public\media") / name,
            Path(r"c:\Users\aaksh\Downloads\qshala-pq-worktree\frontend\public\media") / name,
        ]
        for p_cand in candidates:
            if p_cand.exists() and p_cand.is_file():
                return p_cand

    # 4. Direct filesystem path
    p_direct = Path(clean_url)
    if p_direct.exists() and p_direct.is_file() and _within_media_roots(p_direct):
        return p_direct
        
    # 5. Storage uploads directory check
    p_upload = settings.STORAGE_DIR / "uploads" / Path(clean_url).name
    if p_upload.exists() and p_upload.is_file():
        return p_upload
        
    # 6. Check within slides directory recursively by filename if needed
    filename = Path(clean_url).name
    if filename:
        for root_dir in [settings.SLIDES_DIR, Path("frontend/public/static/slides"), Path(r"c:\Users\aaksh\Downloads\qshala\frontend\public\static\slides")]:
            if root_dir.exists():
                matches = list(root_dir.glob(f"**/{filename}"))
                if matches and matches[0].is_file():
                    return matches[0]

    return None

def _add_scaled_picture(slide, img_path: Path, max_left, max_top, max_w, max_h):
    """Adds an image keeping exact aspect ratio without distortion or borders."""
    try:
        with Image.open(img_path) as pil_img:
            img_w, img_h = pil_img.size
        if img_w <= 0 or img_h <= 0:
            return None
        scale = min(float(max_w) / img_w, float(max_h) / img_h)
        scaled_w = int(img_w * scale)
        scaled_h = int(img_h * scale)
        final_left = int(max_left + (max_w - scaled_w) / 2)
        final_top = int(max_top + (max_h - scaled_h) / 2)
        return slide.shapes.add_picture(str(img_path), final_left, final_top, width=scaled_w, height=scaled_h)
    except Exception as img_err:
        logger.warning(f"Error adding picture {img_path} to slide: {img_err}")
        return None

def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to extract attribute from dict or object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _get_hist_q_media(q: Any) -> Dict[str, Any]:
    """Helper to retrieve raw media, audio, and videos from the source vault question or question object directly."""
    direct_q_imgs = list(_get_val(q, "question_images", []) or [])
    direct_a_imgs = list(_get_val(q, "answer_images", []) or [])
    direct_img_refs = list(_get_val(q, "image_refs", []) or [])
    direct_raw = list(_get_val(q, "raw_media_refs", []) or [])
    direct_qa = list(_get_val(q, "question_audio", []) or [])
    direct_qv = list(_get_val(q, "question_video", []) or [])

    sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", [])
    hist_id = _get_val(sources[0], "historical_question_id") if sources else None
    q_id = _get_val(q, "id")

    target_ids = [str(i) for i in [hist_id, q_id] if i]
    if target_ids:
        try:
            from backend.app.database import SessionLocal
            from backend.app.models.question import Question
            db = SessionLocal()
            hq = db.query(Question).filter(Question.id.in_(target_ids)).first()
            if hq:
                res = {
                    "raw_media_refs": list(getattr(hq, "raw_media_refs", []) or []) + direct_raw,
                    "question_audio": list(getattr(hq, "question_audio", []) or []) + direct_qa,
                    "audio_refs": list(getattr(hq, "audio_refs", []) or []),
                    "question_video": list(getattr(hq, "question_video", []) or []) + direct_qv,
                    "video_refs": list(getattr(hq, "video_refs", []) or []),
                    "question_images": list(getattr(hq, "question_images", []) or []) + direct_q_imgs,
                    "answer_images": list(getattr(hq, "answer_images", []) or []) + direct_a_imgs,
                    "image_refs": list(getattr(hq, "image_refs", []) or []) + direct_img_refs,
                }
                db.close()
                return res
            db.close()
        except Exception as e:
            logger.warning(f"Error fetching historical question media: {e}")

    return {
        "raw_media_refs": direct_raw,
        "question_audio": direct_qa,
        "audio_refs": [],
        "question_video": direct_qv,
        "video_refs": [],
        "question_images": direct_q_imgs,
        "answer_images": direct_a_imgs,
        "image_refs": direct_img_refs,
    }

def _clean_quiz_title(raw_title: str, topic: str) -> str:
    """Sanitizes raw prompt text into a clean presentation title."""
    if not raw_title or raw_title.lower() in ("qshala tournament quiz", "custom", "untitled quiz"):
        return f"{topic.title()} Quiz" if topic else "Bengaluru South Zone"
    t = raw_title.strip()
    prompt_patterns = [
        r"^(?:give\s+me|generate|create|make|pull)\s+\d*\s*",
        r"\s*(?:and\s+make\s+a\s+quiz|and\s+create\s+a\s+quiz|make\s+a\s+quiz|create\s+a\s+quiz|quiz\s+please|for\s+school|without\s+images?|text\s+only).*$",
        r"^questions?\s+(?:on|about|for)\s+",
    ]
    cleaned = t
    for pat in prompt_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned and len(cleaned) > 2 and cleaned.lower() != t.lower():
        return f"{cleaned.title()} Quiz"
    return t

class QuizExporter:
    @staticmethod
    def to_json(quiz: Any) -> str:
        quiz_id = _get_val(quiz, "id", "generated_quiz")
        title = _get_val(quiz, "title", "Tournament Quiz")
        topic = _get_val(quiz, "topic", "General")
        subtopic = _get_val(quiz, "subtopic")
        grade_min = _get_val(quiz, "grade_min")
        grade_max = _get_val(quiz, "grade_max")
        difficulty = _get_val(quiz, "difficulty", "Balanced")
        difficulty_distribution = _get_val(quiz, "difficulty_distribution", {})
        generation_mode = _get_val(quiz, "generation_mode", "NEW")
        created_at = _get_val(quiz, "created_at")
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()

        questions_raw = _get_val(quiz, "questions", [])
        questions_list = []
        for q in questions_raw:
            is_approved = _get_val(q, "is_approved", True)
            if not is_approved:
                continue

            sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", [])
            sources_list = []
            for src in sources:
                sources_list.append({
                    "document_title": _get_val(src, "document_title"),
                    "slide_number": _get_val(src, "slide_number"),
                    "quote": _get_val(src, "source_quote") or _get_val(src, "quote"),
                    "rationale": _get_val(src, "rationale")
                })

            q_min = _get_val(q, "grade_min")
            q_max = _get_val(q, "grade_max")
            grade_range = f"{q_min}-{q_max}" if q_min and q_max else "General"

            questions_list.append({
                "order": _get_val(q, "order_index") or _get_val(q, "order", 1),
                "question": _get_val(q, "question_text") or _get_val(q, "question", ""),
                "options": _get_val(q, "options", []),
                "answer": _get_val(q, "answer", ""),
                "explanation": _get_val(q, "explanation", ""),
                "difficulty": _get_val(q, "difficulty", "Medium"),
                "grade_range": grade_range,
                "question_type": _get_val(q, "question_type", "SLIDE_QA"),
                "image_refs": _get_val(q, "image_refs", []) or [],
                "question_images": _get_val(q, "question_images", []) or [],
                "answer_images": _get_val(q, "answer_images", []) or [],
                "visual_clues": _get_val(q, "visual_clues"),
                "audio_refs": _get_val(q, "audio_refs", []) or [],
                "question_audio": _get_val(q, "question_audio", []) or [],
                "answer_audio": _get_val(q, "answer_audio", []) or [],
                "video_refs": _get_val(q, "video_refs", []) or [],
                "question_video": _get_val(q, "question_video", []) or [],
                "raw_media_refs": _get_val(q, "raw_media_refs", []) or [],
                "validation_status": _get_val(q, "validation_status", "PASSED"),
                "sources": sources_list
            })

        data = {
            "quiz_id": quiz_id,
            "title": title,
            "topic": topic,
            "subtopic": subtopic,
            "grade_min": grade_min,
            "grade_max": grade_max,
            "difficulty": difficulty,
            "difficulty_distribution": difficulty_distribution,
            "generation_mode": generation_mode,
            "created_at": created_at,
            "questions": questions_list
        }
        return json.dumps(data, indent=2)

    @staticmethod
    def to_csv(quiz: Any) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Order",
            "Question",
            "Options",
            "Answer",
            "Explanation",
            "Difficulty",
            "Topic",
            "Primary Source"
        ])

        quiz_topic = _get_val(quiz, "topic", "General")
        questions_raw = _get_val(quiz, "questions", [])
        for q in questions_raw:
            if not _get_val(q, "is_approved", True):
                continue

            sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", [])
            primary_src = ""
            if sources:
                s = sources[0]
                doc_title = _get_val(s, "document_title", "Deck")
                slide_num = _get_val(s, "slide_number", "N/A")
                primary_src = f"{doc_title} (Slide {slide_num})"

            opts = _get_val(q, "options", [])
            options_str = " | ".join(opts) if opts else "N/A"

            writer.writerow([
                _get_val(q, "order_index") or _get_val(q, "order", 1),
                _get_val(q, "question_text") or _get_val(q, "question", ""),
                options_str,
                _get_val(q, "answer", ""),
                _get_val(q, "explanation", ""),
                _get_val(q, "difficulty", "Medium"),
                _get_val(q, "topic") or quiz_topic,
                primary_src
            ])

        return output.getvalue()

    @staticmethod
    def to_pptx(
        quiz: Any,
        quiz_type: str = "school",
        campus_edition: Optional[str] = "Bangalore Campus Edition",
        prize_money: Optional[str] = "3.5 lakh",
        custom_title: Optional[str] = None,
        include_social_slides: bool = True,
        theme: Optional[str] = None,
        custom_subtitle: Optional[str] = None,
        show_answer_divider: Optional[bool] = None,
        **kwargs: Any,
    ) -> bytes:
        """
        Exports the quiz into a crisp, high-typography presentation deck (16:9 widescreen)
        using the official QShala template backdrops for School and Corporate quizzes.
        
        Strictly observes:
        1. Background pairing:
           - Question Slide: school/question_bg.png (PPT.png, peach border) or corporate/question_bg.png
           - Answer Transition Slide: school/answer_intro_bg.png (PPT-3.png) or corporate/answer_intro_bg.png
           - Answer Reveal Slide: school/answer_bg.png (Red border 2.png, coral red border) or corporate/answer_bg.png
        2. Cover Slide:
           - School: Common Cover slide with dynamic '— {campus_edition} —' and 'Win prizes worth ₹{prize_money}!'
           - Zero redundant bottom text or round subtitles
        3. Mandatory 3-Slide Rhythm:
           Question Slide -> Answer Transition Slide ('Answer') -> Answer Reveal Slide
        4. South Zone image placement protocol (2-column question layout & centered answer diagrams)
        5. Inter typography strictly retained with WCAG AAA contrast (#0F172A / #059669)
        """
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        from pptx.enum.shapes import MSO_SHAPE

        # Backward compatibility for theme argument
        if theme:
            t_lower = str(theme).lower()
            if t_lower in ("corporate", "corp"):
                quiz_type = "corporate"
            elif t_lower in ("minimalist", "clean"):
                quiz_type = "minimalist"
            elif t_lower in ("qshala_school", "qshala", "qshala-school"):
                quiz_type = "qshala_school"
            else:
                quiz_type = "school"

        if quiz_type in ("qshala_school", "qshala", "qshala-school"):
            quiz_type = "qshala_school"

        media_tmp_dir = Path(tempfile.mkdtemp(prefix="qshala_export_media_"))
        logger.info(f"Export media temp dir: {media_tmp_dir}")
        try:
            return QuizExporter._build_pptx(
                quiz=quiz,
                quiz_type=quiz_type,
                campus_edition=campus_edition,
                prize_money=prize_money,
                custom_title=custom_title,
                include_social_slides=include_social_slides,
                media_tmp_dir=media_tmp_dir,
                custom_subtitle=custom_subtitle,
                show_answer_divider=show_answer_divider,
                **kwargs
            )
        finally:
            try:
                shutil.rmtree(media_tmp_dir, ignore_errors=True)
                logger.info(f"Cleaned up export media temp dir: {media_tmp_dir}")
            except Exception:
                pass


    @staticmethod
    def _build_pptx(
        quiz: Any,
        quiz_type: str,
        campus_edition: Optional[str],
        prize_money: Optional[str],
        custom_title: Optional[str],
        include_social_slides: bool,
        media_tmp_dir: Path,
        custom_subtitle: Optional[str] = None,
        show_answer_divider: Optional[bool] = None,
        **kwargs: Any,
    ) -> bytes:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        from pptx.enum.shapes import MSO_SHAPE
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank_layout = prs.slide_layouts[6]

        # -------------------------------------------------------------
        # Template Asset Directory Setup
        # -------------------------------------------------------------
        assets_base = Path(__file__).resolve().parent / "templates" / "assets"
        is_qshala_school = (quiz_type in ("qshala_school", "qshala"))
        if is_qshala_school:
            track_dir = assets_base / "qshala_school"
        elif quiz_type == "corporate":
            track_dir = assets_base / "corporate"
        else:
            track_dir = assets_base / "school"

        cover_bg = track_dir / "cover_bg.png"
        question_bg = track_dir / "question_bg.png"
        answer_intro_bg = track_dir / "answer_intro_bg.png"
        answer_bg = track_dir / "answer_bg.png"
        round_bg = track_dir / "round_bg.png"
        qr_varsity = track_dir / "qr_varsity.png"
        qr_insta = track_dir / "qr_instagram.jpg"
        qr_wa = track_dir / "qr_whatsapp.jpg"

        # Fallback to base assets if subdirectories not populated
        if not cover_bg.exists():
            cover_bg = assets_base / "title_bg.png"
        if not question_bg.exists():
            question_bg = assets_base / "question_bg.png"
        if not answer_intro_bg.exists():
            answer_intro_bg = assets_base / "answer_intro_bg.png"
        if not answer_bg.exists():
            answer_bg = assets_base / "answer_bg.png"
        if not qr_varsity.exists():
            qr_varsity = assets_base / "qr_varsity.png"
        if not qr_insta.exists():
            qr_insta = assets_base / "qr_instagram.jpg"
        if not qr_wa.exists():
            qr_wa = assets_base / "qr_whatsapp.jpg"

        is_minimalist = (quiz_type == "minimalist")

        # -------------------------------------------------------------
        # Typography & Color Standards (Inter for ZSV, Causten Round for QShala School)
        # -------------------------------------------------------------
        if is_qshala_school:
            FONT_HEAD = "Causten Round"
            FONT_BODY = "Causten Round"
        else:
            FONT_HEAD = "Inter"
            FONT_BODY = "Inter"

        C_WHITE = RGBColor(255, 255, 255)
        C_DARK_BG = RGBColor(15, 23, 42)          # #0F172A Slate 900
        C_TEXT_DARK = RGBColor(15, 23, 42)        # #0F172A Deep Charcoal / Black
        C_TEXT_BODY = RGBColor(51, 65, 85)        # #334155 Slate 700
        C_TEXT_MUTED = RGBColor(100, 116, 139)    # #64748B Slate 500
        C_ACCENT_ORANGE = RGBColor(194, 65, 12)   # #C2410C Tournament Burnt Orange
        C_ACCENT_GREEN = RGBColor(5, 150, 105)    # #059669 Emerald 600
        C_ACCENT_GOLD = RGBColor(217, 119, 6)     # #D97706 Amber 600
        C_CARD_BG = RGBColor(248, 250, 252)       # Slate 50
        C_CARD_BORDER = RGBColor(226, 232, 240)   # Slate 200

        raw_title = _get_val(quiz, "title", "")
        topic_text = _get_val(quiz, "topic", "General Knowledge")
        difficulty = _get_val(quiz, "difficulty", "Balanced")

        clean_topic_title = _clean_quiz_title(raw_title, topic_text)
        final_title = custom_title.strip() if (custom_title and custom_title.strip()) else clean_topic_title

        questions_raw = _get_val(quiz, "questions", [])
        approved_questions = [q for q in questions_raw if _get_val(q, "is_approved", True)]
        total_q = len(approved_questions)

        # -------------------------------------------------------------
        # 1. Cover Slide
        # -------------------------------------------------------------
        s_title = prs.slides.add_slide(blank_layout)

        if not is_minimalist and cover_bg.exists():
            s_title.shapes.add_picture(str(cover_bg), 0, 0, Inches(13.333), Inches(7.5))

            if is_qshala_school:
                # QShala School Template Cover Layout (Style Guide compliant):
                # Centered Title in 44pt bold Causten Round, centered Subtitle in 24pt
                tb_qshala_cov = s_title.shapes.add_textbox(Inches(1.50), Inches(2.20), Inches(10.333), Inches(3.20))
                tf_qcov = tb_qshala_cov.text_frame
                tf_qcov.word_wrap = True

                p_qc1 = tf_qcov.paragraphs[0]
                p_qc1.alignment = PP_ALIGN.CENTER
                p_qc1.text = final_title
                p_qc1.font.name = FONT_HEAD
                p_qc1.font.size = Pt(44)
                p_qc1.font.bold = True
                p_qc1.font.color.rgb = C_TEXT_DARK

                sub_display = (custom_subtitle or campus_edition or "Middle School Assembly").strip()
                if sub_display:
                    p_qc2 = tf_qcov.add_paragraph()
                    p_qc2.alignment = PP_ALIGN.CENTER
                    p_qc2.space_before = Pt(18)
                    p_qc2.text = sub_display
                    p_qc2.font.name = FONT_HEAD
                    p_qc2.font.size = Pt(24)
                    p_qc2.font.bold = False
                    p_qc2.font.color.rgb = C_TEXT_BODY
            elif quiz_type == "school":
                # Clean School Cover Layout (Common Cover slide):
                # Pre-printed at top: Zerodha Varsity Quiz / India's Premier Finance Quiz
                # Line 1: '— {campus_edition} —' centered between peach rules at y = 3.65"
                ed_raw = (campus_edition or "Bangalore Campus Edition").strip()
                if not ed_raw.startswith("—") and not ed_raw.startswith("-"):
                    edition_display = f"— {ed_raw} —"
                else:
                    edition_display = ed_raw

                tb_ed = s_title.shapes.add_textbox(Inches(1.50), Inches(3.60), Inches(10.333), Inches(0.65))
                tf_ed = tb_ed.text_frame
                tf_ed.word_wrap = True
                p_ed = tf_ed.paragraphs[0]
                p_ed.alignment = PP_ALIGN.CENTER
                p_ed.text = edition_display
                p_ed.font.name = FONT_HEAD
                p_ed.font.size = Pt(24)
                p_ed.font.bold = False
                p_ed.font.italic = True
                p_ed.font.color.rgb = C_ACCENT_ORANGE

                # Line 2: 'Win prizes worth ₹{prize_money}!' centered at y = 4.30"
                p_raw = (prize_money or "3.5 lakh").strip()
                # If raw integer like '1' or '5', format as '1 Lakh' or '5 Lakh'
                if p_raw.isdigit():
                    val = int(p_raw)
                    p_raw = f"{val} Lakh" if val < 100 else f"{val:,}"
                if not p_raw.startswith("₹") and not p_raw.lower().startswith("rs"):
                    p_disp = f"₹{p_raw}"
                else:
                    p_disp = p_raw
                prize_display = f"Win prizes worth {p_disp}!"

                tb_prz = s_title.shapes.add_textbox(Inches(1.50), Inches(4.30), Inches(10.333), Inches(0.65))
                tf_prz = tb_prz.text_frame
                tf_prz.word_wrap = True
                p_prz = tf_prz.paragraphs[0]
                p_prz.alignment = PP_ALIGN.CENTER
                p_prz.text = prize_display
                p_prz.font.name = FONT_HEAD
                p_prz.font.size = Pt(28)
                p_prz.font.bold = False
                p_prz.font.color.rgb = C_TEXT_BODY

                # NO redundant text below Line 2 (no duplicate bottom title, no round subtitle)
            else:
                # Corporate Cover Layout
                tb_corp = s_title.shapes.add_textbox(Inches(1.50), Inches(2.80), Inches(10.333), Inches(2.20))
                tf_c = tb_corp.text_frame
                tf_c.word_wrap = True

                p_c1 = tf_c.paragraphs[0]
                p_c1.alignment = PP_ALIGN.CENTER
                p_c1.text = final_title
                p_c1.font.name = FONT_HEAD
                p_c1.font.size = Pt(40)
                p_c1.font.bold = True
                p_c1.font.color.rgb = C_TEXT_DARK

                if campus_edition:
                    p_c2 = tf_c.add_paragraph()
                    p_c2.alignment = PP_ALIGN.CENTER
                    p_c2.space_before = Pt(12)
                    p_c2.text = campus_edition.strip()
                    p_c2.font.name = FONT_HEAD
                    p_c2.font.size = Pt(24)
                    p_c2.font.color.rgb = C_ACCENT_ORANGE
        else:
            # Minimalist fallback title slide
            bg_title = s_title.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
            bg_title.fill.solid()
            bg_title.fill.fore_color.rgb = C_DARK_BG
            bg_title.line.fill.background()
            tb_title = s_title.shapes.add_textbox(Inches(1.2), Inches(2.1), Inches(10.9), Inches(3.6))
            tf_title = tb_title.text_frame
            tf_title.word_wrap = True

            p_h1 = tf_title.paragraphs[0]
            p_h1.text = final_title
            p_h1.font.name = FONT_HEAD
            p_h1.font.size = Pt(46)
            p_h1.font.bold = True
            p_h1.font.color.rgb = C_WHITE

            p_sub = tf_title.add_paragraph()
            p_sub.space_before = Pt(16)
            p_sub.text = f"{campus_edition or 'Tournament Prelims'}   •   {total_q} Questions"
            p_sub.font.name = FONT_BODY
            p_sub.font.size = Pt(19)
            p_sub.font.color.rgb = RGBColor(148, 163, 184)

        # -------------------------------------------------------------
        # 2. Social Media & QR Slides (School Quiz)
        # -------------------------------------------------------------
        if quiz_type == "school" and include_social_slides and not is_minimalist:
            # Slide 2: Social Media Guidelines
            s_soc = prs.slides.add_slide(blank_layout)
            if question_bg.exists():
                s_soc.shapes.add_picture(str(question_bg), 0, 0, Inches(13.333), Inches(7.5))

            tb_stitle = s_soc.shapes.add_textbox(Inches(2.0), Inches(1.35), Inches(9.333), Inches(1.10))
            pst = tb_stitle.text_frame.paragraphs[0]
            pst.alignment = PP_ALIGN.CENTER
            pst.text = "Social Media"
            pst.font.name = FONT_HEAD
            pst.font.size = Pt(40)
            pst.font.bold = True
            pst.font.color.rgb = C_TEXT_DARK

            tb_sbody = s_soc.shapes.add_textbox(Inches(1.20), Inches(2.55), Inches(10.933), Inches(4.30))
            tf_sb = tb_sbody.text_frame
            tf_sb.word_wrap = True

            p1 = tf_sb.paragraphs[0]
            p1.alignment = PP_ALIGN.CENTER
            p1.text = "Share your experience of the quiz • Use #varsityquiz and tag @zerodhavarsity"
            p1.font.name = FONT_BODY
            p1.font.size = Pt(28)
            p1.font.bold = True
            p1.font.color.rgb = C_TEXT_DARK

            p2 = tf_sb.add_paragraph()
            p2.alignment = PP_ALIGN.CENTER
            p2.space_before = Pt(24)
            p2.text = "And send the link of your post on the WhatsApp participants group"
            p2.font.name = FONT_BODY
            p2.font.size = Pt(28)
            p2.font.bold = True
            p2.font.color.rgb = C_TEXT_DARK

            p3 = tf_sb.add_paragraph()
            p3.alignment = PP_ALIGN.CENTER
            p3.space_before = Pt(24)
            p3.text = "Win goodie bags!"
            p3.font.name = FONT_BODY
            p3.font.size = Pt(28)
            p3.font.bold = True
            p3.font.color.rgb = C_TEXT_DARK

            # Slide 3: QR Codes (Follow @Zerodhavarsity)
            s_qr = prs.slides.add_slide(blank_layout)
            if question_bg.exists():
                s_qr.shapes.add_picture(str(question_bg), 0, 0, Inches(13.333), Inches(7.5))

            tb_qrtitle = s_qr.shapes.add_textbox(Inches(1.0), Inches(1.30), Inches(11.333), Inches(1.30))
            pqt = tb_qrtitle.text_frame.paragraphs[0]
            pqt.alignment = PP_ALIGN.CENTER
            pqt.text = "Get Curious & Stay Curious\nFollow @Zerodhavarsity"
            pqt.font.name = FONT_HEAD
            pqt.font.size = Pt(32)
            pqt.font.bold = True
            pqt.font.color.rgb = C_TEXT_DARK

            if qr_varsity.exists():
                s_qr.shapes.add_picture(str(qr_varsity), Inches(1.50), Inches(2.95), Inches(3.30), Inches(3.30))
            if qr_insta.exists():
                s_qr.shapes.add_picture(str(qr_insta), Inches(5.05), Inches(2.95), Inches(3.30), Inches(3.30))
            if qr_wa.exists():
                s_qr.shapes.add_picture(str(qr_wa), Inches(8.60), Inches(2.95), Inches(3.30), Inches(3.30))

        # -------------------------------------------------------------
        # 3. Mandatory 3-Slide Question Loop
        # -------------------------------------------------------------
        from backend.app.services.export import format_slides
        for idx, q in enumerate(approved_questions, start=1):
            # Format-aware questions (List It, Image Reveal, Connect, Stepped Clue, Arrange, MCQ) get a static expansion
            # of their own; anything else, or a payload that cannot be expanded, takes the template path below.
            fp_q = _get_val(q, "format_payload")
            if format_slides.export_format_of(fp_q):
                opts_q = _get_val(q, "options", None)
                if format_slides.add_format_question(
                    prs, blank_layout, payload=fp_q,
                    question_text=(_get_val(q, "question_text") or _get_val(q, "question", "") or "").strip(),
                    answer=(_get_val(q, "answer") or "").strip(),
                    explanation=(_get_val(q, "explanation") or "").strip(),
                    options=list(opts_q) if opts_q else None,
                    meta={}, idx=idx,
                    resolve=lambda u: _resolve_media_path(u, download_dir=media_tmp_dir),
                ):
                    continue
            is_starred = (idx % 5 == 0)
            star_tag = " *" if is_starred else ""

            hist_media = _get_hist_q_media(q)

            # --- MEDIA DETECTION ---
            cand_videos = (
                list(_get_val(q, "question_video", []) or []) +
                list(_get_val(q, "video_refs", []) or []) +
                list(_get_val(q, "raw_media_refs", []) or []) +
                list(_get_val(q, "image_refs", []) or []) +
                list(hist_media.get("raw_media_refs", []) or [])
            )
            video_path = None
            for cv in cand_videos:
                if isinstance(cv, str) and cv.lower().endswith(('.mp4', '.m4v', '.mov', '.webm')):
                    p = _resolve_media_path(cv, download_dir=media_tmp_dir)
                    if p and p.exists():
                        video_path = p
                        break

            cand_audios = (
                list(_get_val(q, "question_audio", []) or []) +
                list(_get_val(q, "audio_refs", []) or []) +
                list(_get_val(q, "raw_media_refs", []) or []) +
                list(hist_media.get("question_audio", []) or []) +
                list(hist_media.get("audio_refs", []) or []) +
                list(hist_media.get("raw_media_refs", []) or [])
            )
            audio_path = None
            for ca in cand_audios:
                if isinstance(ca, str) and ca.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg')):
                    p = _resolve_media_path(ca, download_dir=media_tmp_dir)
                    if p and p.exists():
                        audio_path = p
                        break

            cand_q_imgs = (
                list(_get_val(q, "question_images", []) or []) +
                list(_get_val(q, "image_refs", []) or []) +
                list(hist_media.get("question_images", []) or []) +
                list(hist_media.get("image_refs", []) or [])
            )
            resolved_q_imgs = []
            for ci in cand_q_imgs:
                if isinstance(ci, str) and not ci.lower().endswith(('.mp4', '.m4v', '.mov', '.mp3', '.wav')):
                    p = _resolve_media_path(ci, download_dir=media_tmp_dir)
                    if p and p.exists() and p not in resolved_q_imgs:
                        resolved_q_imgs.append(p)

            poster_path = resolved_q_imgs[0] if resolved_q_imgs else None
            q_text = (_get_val(q, "question_text") or _get_val(q, "question", "")).strip()
            # Clean trailing prompt artifacts like '[Image on slide]', '[Image on the right]', '[Image on'
            q_text = re.sub(r'\[\s*(?:image|picture|photo|visual|video)\b.*$', '', q_text, flags=re.I).strip()
            q_text = re.sub(r'\[\s*(?:image|picture|photo|visual|video)\s+[^\s\]]*\s*\]?', '', q_text, flags=re.I).strip()
            if not q_text or len(q_text) < 5:
                # Check visual clues or connect trivia
                v_clue = (_get_val(q, "visual_clues") or "").strip()
                if v_clue:
                    q_text = v_clue
                elif resolved_q_imgs:
                    q_text = "Identify the connect or entity represented by the clues shown on this slide."
                else:
                    q_text = "Identify the person, brand, or concept based on the competition clues."
            options = _get_val(q, "options", []) or []

            ans_val = (_get_val(q, "answer") or "N/A").strip()
            raw_exp = (_get_val(q, "explanation") or "").strip()
            is_dup_explanation = (
                not raw_exp or
                raw_exp.strip().lower() == ans_val.strip().lower() or
                raw_exp.strip().lower() in ("n/a", "none", "historical qshala competition question.") or
                len(raw_exp.strip()) < 3
            )
            explanation = "" if is_dup_explanation else raw_exp

            # ---------------------------------------------------------
            # (A) QUESTION SLIDE (uses question_bg: PPT.png with peach border)
            # ---------------------------------------------------------
            sq = prs.slides.add_slide(blank_layout)
            if not is_minimalist and question_bg.exists():
                sq.shapes.add_picture(str(question_bg), 0, 0, Inches(13.333), Inches(7.5))
                if not is_qshala_school:
                    # Number badge slot matching South Zone template: top=0.40", left=6.67", width=4.13"
                    tb_num = sq.shapes.add_textbox(Inches(6.67), Inches(0.40), Inches(4.13), Inches(1.08))
                    pn = tb_num.text_frame.paragraphs[0]
                    pn.text = f"{idx}."
                    pn.alignment = PP_ALIGN.CENTER
                    pn.font.name = FONT_HEAD
                    pn.font.size = Pt(40)
                    pn.font.bold = True
                    pn.font.color.rgb = C_TEXT_DARK
            else:
                bg_q = sq.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
                bg_q.fill.solid()
                bg_q.fill.fore_color.rgb = C_WHITE
                bg_q.line.fill.background()
                if not is_qshala_school:
                    tb_num = sq.shapes.add_textbox(Inches(10.2), Inches(0.4), Inches(2.4), Inches(0.9))
                    pn = tb_num.text_frame.paragraphs[0]
                    pn.text = f"{idx}."
                    pn.alignment = PP_ALIGN.RIGHT
                    pn.font.name = FONT_HEAD
                    pn.font.size = Pt(44)
                    pn.font.bold = True
                    pn.font.color.rgb = C_TEXT_DARK

            has_media = bool(video_path or audio_path or resolved_q_imgs)

            if is_qshala_school:
                # QShala School Style Guide: Header in 40pt bold Causten Round, centered
                tb_head = sq.shapes.add_textbox(Inches(1.00), Inches(0.70), Inches(11.333), Inches(0.75))
                p_hd = tb_head.text_frame.paragraphs[0]
                p_hd.alignment = PP_ALIGN.CENTER
                p_hd.text = f"Question {idx}"
                p_hd.font.name = FONT_HEAD
                p_hd.font.size = Pt(40)
                p_hd.font.bold = True
                p_hd.font.color.rgb = C_TEXT_DARK

                if has_media:
                    is_short_prompt = (len(q_text) <= 90 and not options)
                    if is_short_prompt:
                        # Short 1-2 line prompt: centered text top, centered media below
                        tb_q = sq.shapes.add_textbox(Inches(1.20), Inches(1.55), Inches(10.933), Inches(0.95))
                        tf_q = tb_q.text_frame
                        tf_q.word_wrap = True
                        pq_main = tf_q.paragraphs[0]
                        pq_main.alignment = PP_ALIGN.CENTER
                        pq_main.text = q_text
                        pq_main.font.name = FONT_BODY
                        pq_main.font.size = Pt(26)
                        pq_main.font.bold = True
                        pq_main.font.color.rgb = C_TEXT_DARK

                        media_left = Inches(3.666)
                        media_top = Inches(2.65)
                        media_w = Inches(6.00)
                        media_h = Inches(3.70)
                    else:
                        # 2-Column Side-by-Side Layout for Standard/Long Questions:
                        # Left: Question text (width=7.20 in, height=4.70 in)
                        # Right: Media Clue (width=3.80 in, height=4.50 in)
                        # Prevents vertical collision between multi-line text and media
                        tb_q = sq.shapes.add_textbox(Inches(1.00), Inches(1.60), Inches(7.20), Inches(4.70))
                        tf_q = tb_q.text_frame
                        tf_q.word_wrap = True
                        pq_main = tf_q.paragraphs[0]
                        pq_main.alignment = PP_ALIGN.LEFT
                        pq_main.text = q_text
                        pq_main.font.name = FONT_BODY
                        pq_main.font.size = Pt(24) if len(q_text) < 180 else (Pt(20) if len(q_text) < 300 else Pt(18))
                        pq_main.font.bold = True
                        pq_main.font.color.rgb = C_TEXT_DARK

                        if options:
                            for opt_idx, opt in enumerate(options):
                                po = tf_q.add_paragraph()
                                po.space_before = Pt(8)
                                prefix = f"{chr(65 + opt_idx)}. " if opt_idx < 26 and not opt.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                                po.text = f"{prefix}{opt}"
                                po.font.name = FONT_BODY
                                po.font.size = Pt(16)
                                po.font.color.rgb = C_TEXT_BODY

                        media_left = Inches(8.50)
                        media_top = Inches(1.60)
                        media_w = Inches(3.80)
                        media_h = Inches(4.50)

                    is_video_mention = bool(re.search(r"\bvideo\b|\bclip\b|\bwatch\b", q_text, re.I))
                    if video_path:
                        sq.shapes.add_movie(
                            str(video_path),
                            media_left, media_top, media_w, min(media_h, Inches(3.80)),
                            poster_frame_image=str(poster_path) if poster_path else None,
                            mime_type="video/mp4"
                        )
                        tb_badge = sq.shapes.add_textbox(media_left, media_top + min(media_h, Inches(3.80)) + Inches(0.10), media_w, Inches(0.40))
                        pb = tb_badge.text_frame.paragraphs[0]
                        pb.alignment = PP_ALIGN.CENTER
                        pb.text = "▶ VIDEO CLIP"
                        pb.font.name = FONT_HEAD
                        pb.font.size = Pt(13)
                        pb.font.bold = True
                        pb.font.color.rgb = C_ACCENT_ORANGE
                    elif is_video_mention and poster_path:
                        _add_scaled_picture(sq, poster_path, media_left, media_top, media_w, min(media_h, Inches(3.80)))
                        tb_badge = sq.shapes.add_textbox(media_left, media_top + min(media_h, Inches(3.80)) + Inches(0.10), media_w, Inches(0.40))
                        pb = tb_badge.text_frame.paragraphs[0]
                        pb.alignment = PP_ALIGN.CENTER
                        pb.text = "▶ VIDEO CLIP"
                        pb.font.name = FONT_HEAD
                        pb.font.size = Pt(13)
                        pb.font.bold = True
                        pb.font.color.rgb = C_ACCENT_ORANGE
                    elif audio_path:
                        card = sq.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, media_left, media_top, media_w, Inches(3.20))
                        card.fill.solid()
                        card.fill.fore_color.rgb = C_CARD_BG
                        card.line.color.rgb = C_CARD_BORDER
                        card.line.width = Pt(1)
                        tf_c = card.text_frame
                        tf_c.word_wrap = True
                        p_c1 = tf_c.paragraphs[0]
                        p_c1.text = "🎧 AUDIO CLUE"
                        p_c1.font.name = FONT_HEAD
                        p_c1.font.size = Pt(16)
                        p_c1.font.bold = True
                        p_c1.font.color.rgb = C_ACCENT_GOLD
                        p_c1.alignment = PP_ALIGN.CENTER
                        sq.shapes.add_movie(
                            str(audio_path),
                            media_left + Inches(1.40), media_top + Inches(1.80), Inches(1.00), Inches(1.00),
                            poster_frame_image=str(poster_path) if poster_path else None,
                            mime_type="audio/mpeg"
                        )
                    elif resolved_q_imgs:
                        _add_scaled_picture(sq, resolved_q_imgs[0], media_left, media_top, media_w, media_h)
                else:
                    # Pure text question: 28pt centered Causten Round (Style Guide compliant)
                    tb_q = sq.shapes.add_textbox(Inches(1.20), Inches(1.85), Inches(10.933), Inches(4.50))
                    tf_q = tb_q.text_frame
                    tf_q.word_wrap = True
                    pq_main = tf_q.paragraphs[0]
                    pq_main.alignment = PP_ALIGN.CENTER
                    pq_main.text = q_text
                    pq_main.font.name = FONT_BODY
                    pq_main.font.size = Pt(28) if len(q_text) < 180 else (Pt(24) if len(q_text) < 320 else Pt(20))
                    pq_main.font.bold = True
                    pq_main.font.color.rgb = C_TEXT_DARK

                    if options:
                        for opt_idx, opt in enumerate(options):
                            po = tf_q.add_paragraph()
                            po.alignment = PP_ALIGN.CENTER
                            po.space_before = Pt(10)
                            prefix = f"{chr(65 + opt_idx)}. " if opt_idx < 26 and not opt.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                            po.text = f"{prefix}{opt}"
                            po.font.name = FONT_BODY
                            po.font.size = Pt(20)
                            po.font.color.rgb = C_TEXT_BODY

                # Style Guide Speaker Notes
                q_notes = [f"Official Answer: {ans_val}"]
                sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", [])
                if sources:
                    s_titles = []
                    for s in sources:
                        dt = _get_val(s, "document_title", "")
                        sn = _get_val(s, "slide_number", "")
                        if dt:
                            s_titles.append(f"{dt} (Slide {sn})" if sn else dt)
                    if s_titles:
                        q_notes.append("Source Reference: " + "; ".join(s_titles))
                if video_path:
                    q_notes.append(f"Media Reference (Video): {video_path.name}")
                elif resolved_q_imgs:
                    q_notes.append(f"Media Reference (Image): {resolved_q_imgs[0].name}")
                v_clue = _get_val(q, "visual_clues") or _get_val(q, "clue")
                if v_clue:
                    q_notes.append(f"Presenter Clues: {v_clue}")
                sq.notes_slide.notes_text_frame.text = "\n".join(q_notes)
            elif has_media:
                # South Zone 2-Column Protocol: Question text left (7.80 in), Media clue right (3.90 in)
                tb_q = sq.shapes.add_textbox(Inches(0.80), Inches(1.50), Inches(7.80), Inches(4.80))
                tf_q = tb_q.text_frame
                tf_q.word_wrap = True

                pq_main = tf_q.paragraphs[0]
                pq_main.text = q_text
                pq_main.font.name = FONT_HEAD
                pq_main.font.size = Pt(28) if len(q_text) < 160 else (Pt(24) if len(q_text) < 240 else Pt(20))
                pq_main.font.bold = True
                pq_main.font.color.rgb = C_TEXT_DARK

                if options:
                    for opt_idx, opt in enumerate(options):
                        po = tf_q.add_paragraph()
                        po.space_before = Pt(8)
                        prefix = f"{chr(65 + opt_idx)}.  " if opt_idx < 26 and not opt.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                        po.text = f"{prefix}{opt}"
                        po.font.name = FONT_BODY
                        po.font.size = Pt(18)
                        po.font.color.rgb = C_TEXT_BODY

                # Media Placement on Right Column
                is_video_mention = bool(re.search(r"\bvideo\b|\bclip\b|\bwatch\b", q_text, re.I))
                if video_path:
                    sq.shapes.add_movie(
                        str(video_path),
                        Inches(8.80), Inches(1.50), Inches(3.90), Inches(4.80),
                        poster_frame_image=str(poster_path) if poster_path else None,
                        mime_type="video/mp4"
                    )
                elif is_video_mention and poster_path:
                    # Video Clue Card with Poster Image
                    _add_scaled_picture(sq, poster_path, Inches(8.80), Inches(1.50), Inches(3.90), Inches(4.20))
                    tb_v = sq.shapes.add_textbox(Inches(8.80), Inches(5.80), Inches(3.90), Inches(0.50))
                    pv = tb_v.text_frame.paragraphs[0]
                    pv.text = "🎥 VIDEO CLUE"
                    pv.font.name = FONT_HEAD
                    pv.font.size = Pt(13)
                    pv.font.bold = True
                    pv.font.color.rgb = C_ACCENT_ORANGE
                    pv.alignment = PP_ALIGN.CENTER
                elif audio_path:
                    card = sq.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.80), Inches(1.80), Inches(3.80), Inches(3.60))
                    card.fill.solid()
                    card.fill.fore_color.rgb = C_CARD_BG
                    card.line.color.rgb = C_CARD_BORDER
                    card.line.width = Pt(1)

                    tf_c = card.text_frame
                    tf_c.word_wrap = True
                    p_c1 = tf_c.paragraphs[0]
                    p_c1.text = "🎧 AUDIO CLUE"
                    p_c1.font.name = FONT_HEAD
                    p_c1.font.size = Pt(16)
                    p_c1.font.bold = True
                    p_c1.font.color.rgb = C_ACCENT_GOLD
                    p_c1.alignment = PP_ALIGN.CENTER

                    p_c2 = tf_c.add_paragraph()
                    p_c2.space_before = Pt(14)
                    p_c2.text = "Click below to play audio sound clip:"
                    p_c2.font.name = FONT_BODY
                    p_c2.font.size = Pt(14)
                    p_c2.font.color.rgb = C_TEXT_MUTED
                    p_c2.alignment = PP_ALIGN.CENTER

                    sq.shapes.add_movie(
                        str(audio_path),
                        Inches(10.20), Inches(3.40), Inches(1.00), Inches(1.00),
                        poster_frame_image=str(poster_path) if poster_path else None,
                        mime_type="audio/mpeg"
                    )
                elif resolved_q_imgs:
                    # South Zone image placement: aspect ratio preserved, fitting within right column box
                    _add_scaled_picture(sq, resolved_q_imgs[0], Inches(8.80), Inches(1.50), Inches(3.90), Inches(4.80))
            else:
                # Full Width Text-Only Layout
                tb_q = sq.shapes.add_textbox(Inches(0.80), Inches(1.60), Inches(11.70), Inches(4.80))
                tf_q = tb_q.text_frame
                tf_q.word_wrap = True

                pq_main = tf_q.paragraphs[0]
                pq_main.text = q_text
                pq_main.font.name = FONT_HEAD
                pq_main.font.size = Pt(32) if len(q_text) < 160 else (Pt(26) if len(q_text) < 260 else Pt(22))
                pq_main.font.bold = True
                pq_main.font.color.rgb = C_TEXT_DARK

                if options:
                    for opt_idx, opt in enumerate(options):
                        po = tf_q.add_paragraph()
                        po.space_before = Pt(12)
                        prefix = f"{chr(65 + opt_idx)}.  " if opt_idx < 26 and not opt.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                        po.text = f"{prefix}{opt}"
                        po.font.name = FONT_BODY
                        po.font.size = Pt(20)
                        po.font.color.rgb = C_TEXT_BODY

            if not is_qshala_school:
                # Speaker Notes (ZSV)
                host_notes = []
                if is_starred:
                    host_notes.append("★ Important: Star tie-breaker question!")
                ans_val = (_get_val(q, "answer") or "N/A").strip()
                host_notes.append(f"Official Answer: {ans_val}")
                if video_path:
                    host_notes.append(f"Video clue: {video_path.name}")
                elif audio_path:
                    host_notes.append(f"Audio clue: {audio_path.name}")
                sq.notes_slide.notes_text_frame.text = "\n".join(host_notes)

            # ---------------------------------------------------------
            # (B) ANSWER TRANSITION SLIDE (Divider)
            # ---------------------------------------------------------
            render_divider = (show_answer_divider is not False)
            if render_divider:
                s_ai = prs.slides.add_slide(blank_layout)
                if not is_minimalist and answer_intro_bg.exists():
                    s_ai.shapes.add_picture(str(answer_intro_bg), 0, 0, Inches(13.333), Inches(7.5))
                else:
                    bg_ai = s_ai.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
                    bg_ai.fill.solid()
                    bg_ai.fill.fore_color.rgb = C_WHITE
                    bg_ai.line.fill.background()

                tb_ai = s_ai.shapes.add_textbox(Inches(4.00), Inches(2.75), Inches(5.333), Inches(1.90))
                pai = tb_ai.text_frame.paragraphs[0]
                pai.alignment = PP_ALIGN.CENTER
                pai.text = "Answer"
                pai.font.name = FONT_HEAD
                pai.font.size = Pt(50)
                pai.font.bold = True
                pai.font.color.rgb = C_TEXT_DARK

            # ---------------------------------------------------------
            # (C) ANSWER REVEAL SLIDE (uses answer_bg: Red border 2.png)
            # ---------------------------------------------------------
            sa = prs.slides.add_slide(blank_layout)
            if not is_minimalist and answer_bg.exists():
                sa.shapes.add_picture(str(answer_bg), 0, 0, Inches(13.333), Inches(7.5))
            else:
                bg_a = sa.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
                bg_a.fill.solid()
                bg_a.fill.fore_color.rgb = C_WHITE
                bg_a.line.fill.background()

            cand_ans_imgs = (
                list(_get_val(q, "answer_images", []) or []) +
                list(hist_media.get("answer_images", []) or []) +
                list(_get_val(q, "image_refs", []) or []) +
                list(hist_media.get("raw_media_refs", []) or [])
            )
            ans_img_path = None
            for ai in cand_ans_imgs:
                if isinstance(ai, str) and not ai.lower().endswith(('.mp4', '.m4v', '.mov', '.mp3', '.wav')):
                    p = _resolve_media_path(ai, download_dir=media_tmp_dir)
                    if p and p.exists():
                        ans_img_path = p
                        break

            if not ans_img_path and poster_path:
                ans_img_path = poster_path

            raw_exp = (_get_val(q, "explanation") or "").strip()
            # Clean duplicate explanation: check if explanation merely repeats answer
            is_dup_explanation = (
                not raw_exp or
                raw_exp.strip().lower() == ans_val.strip().lower() or
                raw_exp.strip().lower() in ("n/a", "none", "historical qshala competition question.") or
                len(raw_exp.strip()) < 3
            )
            explanation = "" if is_dup_explanation else raw_exp
            has_ans_img = bool(ans_img_path)

            if is_qshala_school:
                # QShala School Answer Reveal Layout
                tb_ahead = sa.shapes.add_textbox(Inches(1.00), Inches(0.70), Inches(11.333), Inches(0.75))
                pah = tb_ahead.text_frame.paragraphs[0]
                pah.alignment = PP_ALIGN.CENTER
                pah.text = f"Answer {idx}"
                pah.font.name = FONT_HEAD
                pah.font.size = Pt(36)
                pah.font.bold = True
                pah.font.color.rgb = C_TEXT_DARK

                if has_ans_img:
                    if explanation and len(explanation) > 50:
                        # 2-Column Solution Layout: Answer + Explanation left (7.20 in), Solution Image right (3.80 in)
                        # Prevents image from overlapping bottom explanation or bottom-right cats artwork
                        tb_a = sa.shapes.add_textbox(Inches(1.00), Inches(1.60), Inches(7.20), Inches(4.70))
                        tf_a = tb_a.text_frame
                        tf_a.word_wrap = True
                        pa_head = tf_a.paragraphs[0]
                        pa_head.alignment = PP_ALIGN.LEFT
                        pa_head.text = ans_val
                        pa_head.font.name = FONT_HEAD
                        pa_head.font.size = Pt(34) if len(ans_val) < 40 else Pt(26)
                        pa_head.font.bold = True
                        pa_head.font.color.rgb = C_ACCENT_GREEN

                        pa_exp = tf_a.add_paragraph()
                        pa_exp.alignment = PP_ALIGN.LEFT
                        pa_exp.space_before = Pt(14)
                        pa_exp.text = explanation
                        pa_exp.font.name = FONT_BODY
                        pa_exp.font.size = Pt(20) if len(explanation) < 200 else Pt(17)
                        pa_exp.font.color.rgb = C_TEXT_BODY

                        _add_scaled_picture(sa, ans_img_path, Inches(8.50), Inches(1.60), Inches(3.80), Inches(4.50))
                    else:
                        # Short or no explanation: Centered answer top, centered solution image below
                        tb_a = sa.shapes.add_textbox(Inches(1.10), Inches(1.55), Inches(11.133), Inches(0.95))
                        tf_a = tb_a.text_frame
                        tf_a.word_wrap = True
                        pa_head = tf_a.paragraphs[0]
                        pa_head.alignment = PP_ALIGN.CENTER
                        pa_head.text = ans_val
                        pa_head.font.name = FONT_HEAD
                        pa_head.font.size = Pt(38) if len(ans_val) < 40 else Pt(30)
                        pa_head.font.bold = True
                        pa_head.font.color.rgb = C_ACCENT_GREEN

                        _add_scaled_picture(sa, ans_img_path, Inches(3.666), Inches(2.65), Inches(6.00), Inches(3.70))
                else:
                    if explanation:
                        tb_a = sa.shapes.add_textbox(Inches(1.20), Inches(1.75), Inches(10.933), Inches(1.30))
                        tf_a = tb_a.text_frame
                        tf_a.word_wrap = True
                        pa_head = tf_a.paragraphs[0]
                        pa_head.alignment = PP_ALIGN.CENTER
                        pa_head.text = ans_val
                        pa_head.font.name = FONT_HEAD
                        pa_head.font.size = Pt(44) if len(ans_val) < 40 else Pt(34)
                        pa_head.font.bold = True
                        pa_head.font.color.rgb = C_ACCENT_GREEN

                        tb_exp = sa.shapes.add_textbox(Inches(1.50), Inches(3.20), Inches(10.333), Inches(3.30))
                        tf_exp = tb_exp.text_frame
                        tf_exp.word_wrap = True
                        pexp = tf_exp.paragraphs[0]
                        pexp.alignment = PP_ALIGN.CENTER
                        pexp.text = explanation
                        pexp.font.name = FONT_BODY
                        pexp.font.size = Pt(24) if len(explanation) < 180 else Pt(20)
                        pexp.font.color.rgb = C_TEXT_BODY
                    else:
                        tb_a = sa.shapes.add_textbox(Inches(1.20), Inches(2.50), Inches(10.933), Inches(2.40))
                        tf_a = tb_a.text_frame
                        tf_a.word_wrap = True
                        pa_head = tf_a.paragraphs[0]
                        pa_head.alignment = PP_ALIGN.CENTER
                        pa_head.text = ans_val
                        pa_head.font.name = FONT_HEAD
                        pa_head.font.size = Pt(48) if len(ans_val) < 40 else Pt(38)
                        pa_head.font.bold = True
                        pa_head.font.color.rgb = C_ACCENT_GREEN

                sa.notes_slide.notes_text_frame.text = (
                    f"Presenter Talking Points:\n"
                    f"Official Answer: {ans_val}\n"
                    f"Award points for correct answer or verified synonym.\n"
                    f"Trivia Context: {explanation}"
                )
            elif has_ans_img:
                if explanation and len(explanation) > 100:
                    # 2-Column Solution Layout
                    tb_a = sa.shapes.add_textbox(Inches(0.80), Inches(1.50), Inches(6.80), Inches(5.00))
                    tf_a = tb_a.text_frame
                    tf_a.word_wrap = True

                    pa_head = tf_a.paragraphs[0]
                    pa_head.text = ans_val
                    pa_head.font.name = FONT_HEAD
                    pa_head.font.size = Pt(34) if len(ans_val) < 40 else (Pt(28) if len(ans_val) < 80 else Pt(22))
                    pa_head.font.bold = True
                    pa_head.font.color.rgb = C_TEXT_DARK

                    pa_exp = tf_a.add_paragraph()
                    pa_exp.space_before = Pt(16)
                    pa_exp.text = explanation
                    pa_exp.font.name = FONT_BODY
                    pa_exp.font.size = Pt(18)
                    pa_exp.font.color.rgb = C_TEXT_BODY

                    _add_scaled_picture(sa, ans_img_path, Inches(8.00), Inches(1.50), Inches(4.70), Inches(5.00))
                else:
                    # South Zone Protocol: Answer heading top, Solution Media horizontally centered below
                    tb_a = sa.shapes.add_textbox(Inches(1.00), Inches(1.30), Inches(11.333), Inches(1.10))
                    tf_a = tb_a.text_frame
                    tf_a.word_wrap = True

                    pa_head = tf_a.paragraphs[0]
                    pa_head.alignment = PP_ALIGN.CENTER
                    pa_head.text = ans_val
                    pa_head.font.name = FONT_HEAD
                    pa_head.font.size = Pt(36) if len(ans_val) < 40 else (Pt(30) if len(ans_val) < 80 else Pt(24))
                    pa_head.font.bold = True
                    pa_head.font.color.rgb = C_TEXT_DARK

                    _add_scaled_picture(sa, ans_img_path, Inches(3.00), Inches(2.55), Inches(7.333), Inches(4.20))

                    if explanation:
                        tb_exp = sa.shapes.add_textbox(Inches(1.50), Inches(6.80), Inches(10.333), Inches(0.60))
                        pexp = tb_exp.text_frame.paragraphs[0]
                        pexp.alignment = PP_ALIGN.CENTER
                        pexp.text = explanation
                        pexp.font.name = FONT_BODY
                        pexp.font.size = Pt(14)
                        pexp.font.color.rgb = C_TEXT_MUTED
            else:
                # Text-Only Answer Slide: Large green reveal
                if explanation:
                    # Answer at top, explanation card below
                    tb_a = sa.shapes.add_textbox(Inches(1.00), Inches(1.60), Inches(11.333), Inches(1.20))
                    tf_a = tb_a.text_frame
                    tf_a.word_wrap = True

                    pa_head = tf_a.paragraphs[0]
                    pa_head.alignment = PP_ALIGN.CENTER
                    pa_head.text = ans_val
                    pa_head.font.name = FONT_HEAD
                    pa_head.font.size = Pt(42) if len(ans_val) < 40 else (Pt(34) if len(ans_val) < 80 else Pt(26))
                    pa_head.font.bold = True
                    pa_head.font.color.rgb = C_TEXT_DARK

                    tb_exp = sa.shapes.add_textbox(Inches(1.50), Inches(3.20), Inches(10.333), Inches(3.20))
                    tf_exp = tb_exp.text_frame
                    tf_exp.word_wrap = True

                    pexp = tf_exp.paragraphs[0]
                    pexp.alignment = PP_ALIGN.CENTER
                    pexp.text = explanation
                    pexp.font.name = FONT_BODY
                    pexp.font.size = Pt(22)
                    pexp.font.color.rgb = C_TEXT_BODY
                else:
                    # No redundant explanation: Centered majestic single answer reveal
                    tb_a = sa.shapes.add_textbox(Inches(1.00), Inches(2.70), Inches(11.333), Inches(2.20))
                    tf_a = tb_a.text_frame
                    tf_a.word_wrap = True

                    pa_head = tf_a.paragraphs[0]
                    pa_head.alignment = PP_ALIGN.CENTER
                    pa_head.text = ans_val
                    pa_head.font.name = FONT_HEAD
                    pa_head.font.size = Pt(48) if len(ans_val) < 40 else (Pt(38) if len(ans_val) < 80 else Pt(28))
                    pa_head.font.bold = True
                    pa_head.font.color.rgb = C_TEXT_DARK

            if not is_qshala_school:
                # Speaker Notes for Answer Slide (ZSV)
                sa.notes_slide.notes_text_frame.text = (
                    f"Presenter Talking Points:\n"
                    f"Official Answer: {ans_val}\n"
                    f"Award +10 points to any team with the correct answer or verified synonym.\n"
                    f"Trivia Context: {explanation}"
                )

        buffer = io.BytesIO()
        prs.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()
