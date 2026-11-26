import os
import re
import csv
import io
import json
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from backend.app.services.export.slide_clone import drop_slide, duplicate_slide, fill_text, find_title_and_subtitle
from PIL import Image

logger = logging.getLogger(__name__)


def _patch_pptx_media_parts() -> None:
    """Work around a python-pptx bug where loaded audio/video parts are Part (no .sha1) rather than MediaPart,
    which causes shapes.add_movie() to raise AttributeError: 'Part' object has no attribute 'sha1'."""
    try:
        import hashlib
        from pptx.package import _MediaParts

        if getattr(_MediaParts._find_by_sha1, "_qshala_patched", False):
            return

        def _safe_find_by_sha1(self, sha1):
            for media_part in self:
                part_sha1 = getattr(media_part, "sha1", None)
                if part_sha1 is None:
                    blob = getattr(media_part, "blob", None)
                    if blob is not None:
                        part_sha1 = hashlib.sha1(blob).hexdigest()
                if part_sha1 == sha1:
                    return media_part
            return None

        _safe_find_by_sha1._qshala_patched = True  # type: ignore[attr-defined]
        _MediaParts._find_by_sha1 = _safe_find_by_sha1  # type: ignore[method-assign]
    except Exception as e:
        logger.debug("Could not patch python-pptx _MediaParts._find_by_sha1: %s", e)


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
        sub = clean_url[len("/static/"):].lstrip("/\\")
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

_MAX_EMBED_IMAGE_DIM = 1600
_OPTIMIZE_IMAGE_MIN_BYTES = 512 * 1024


def _optimize_picture_source(img_path: Path, pil_img: Image.Image) -> Union[str, io.BytesIO]:
    """Return a compressed BytesIO stream when an image is oversized, otherwise the original path."""
    try:
        orig_size = img_path.stat().st_size
    except OSError:
        return str(img_path)
    img_w, img_h = pil_img.size
    if max(img_w, img_h) <= _MAX_EMBED_IMAGE_DIM and orig_size <= _OPTIMIZE_IMAGE_MIN_BYTES:
        return str(img_path)
    try:
        work = pil_img.copy()
        if max(img_w, img_h) > _MAX_EMBED_IMAGE_DIM:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            work.thumbnail((_MAX_EMBED_IMAGE_DIM, _MAX_EMBED_IMAGE_DIM), resampling)
        has_alpha = work.mode in ("RGBA", "LA") or (
            work.mode == "P" and "transparency" in work.info
        )
        buf = io.BytesIO()
        if has_alpha:
            work.save(buf, format="PNG", optimize=True)
        else:
            if work.mode != "RGB":
                work = work.convert("RGB")
            work.save(buf, format="JPEG", quality=85, optimize=True)
        if buf.tell() < orig_size:
            buf.seek(0)
            return buf
    except Exception as opt_err:
        logger.debug(f"Image optimization skipped for {img_path}: {opt_err}")
    return str(img_path)


def _add_scaled_picture(slide, img_path: Path, max_left, max_top, max_w, max_h):
    """Adds an image keeping exact aspect ratio without distortion or borders."""
    try:
        with Image.open(img_path) as pil_img:
            img_w, img_h = pil_img.size
            if img_w <= 0 or img_h <= 0:
                return None
            img_source = _optimize_picture_source(img_path, pil_img)
        scale = min(float(max_w) / img_w, float(max_h) / img_h)
        scaled_w = int(img_w * scale)
        scaled_h = int(img_h * scale)
        final_left = int(max_left + (max_w - scaled_w) / 2)
        final_top = int(max_top + (max_h - scaled_h) / 2)
        return slide.shapes.add_picture(img_source, final_left, final_top, width=scaled_w, height=scaled_h)
    except Exception as img_err:
        logger.warning(f"Error adding picture {img_path} to slide: {img_err}")
        return None


# --- QShala School (Causten Round) typography + media layout helpers ------------------------------
CAUSTEN_REGULAR = "Causten Round"          # the family's standard (Regular) weight: all body text
CAUSTEN_BOLD = "Causten Round Bold"        # headings only: cover title, question/answer number, Answer divider
# Safe content zone inside the white card (inches): clear of the border artwork and bottom-right cats.
_SAFE_L, _SAFE_R, _SAFE_T, _SAFE_B = 0.95, 12.38, 1.55, 6.15
_BOLD_SHAPE_NAMES = {"QNum", "CoverTitle", "Heading"}


def _image_aspect(path: Optional[Path], default: float = 16 / 9) -> float:
    """Width / height of an image (or ``default`` for video/audio posters that cannot be read)."""
    if not path:
        return default
    try:
        with Image.open(path) as im:
            w, h = im.size
        return (w / h) if w > 0 and h > 0 else default
    except Exception:
        return default


def _fit_font_pt(text: str, w_in: float, h_in: float, sizes=(28, 26, 24, 22, 20, 18, 16)) -> int:
    """Largest size (pt) at which ``text`` (Causten Round, ~0.5 em avg glyph) fits a w x h box."""
    paras = [t for t in (text or "").split("\n")] or [""]
    for sz in sizes:
        chars_per_line = max(1, int((w_in * 72) / (sz * 0.47)))
        lines = sum(max(1, -(-len(t) // chars_per_line)) for t in paras)
        if lines * sz * 1.28 <= h_in * 72:
            return sz
    return sizes[-1]


_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'(0-9])')


def _question_paragraphs(text: str, max_paras: int = 4) -> list:
    """Break a question into readable paragraphs on sentence boundaries, so a long question reads as
    separate lines instead of one dense block (e.g. the context sentences and the final 'Which brand
    is this?' each get their own paragraph). Short questions are returned as a single paragraph."""
    raw = (text or '').strip()
    if not raw:
        return ['']
    if '\n\n' in raw or '\r\n\r\n' in raw:
        explicit = [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'(?:\r?\n){2,}', raw) if p.strip()]
        if len(explicit) > 1:
            return explicit[:max_paras]
    text = re.sub(r'\s+', ' ', raw)
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if len(parts) <= 1:
        return [text]
    if len(parts) <= max_paras:
        return parts
    # Too many sentences: keep the final one (usually the actual question) on its own line and
    # pack the rest evenly into the remaining paragraph slots.
    import math
    tail, head = parts[-1], parts[:-1]
    per = max(1, math.ceil(len(head) / (max_paras - 1)))
    grouped = [' '.join(head[i:i + per]) for i in range(0, len(head), per)]
    return grouped + [tail]


def _format_question_source_notes(q: Any) -> str:
    """Extract and format question source links and references for PPT notes."""
    lines = []
    raw_sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", []) or []
    if not raw_sources:
        prov = _get_val(q, "provenance") or {}
        if isinstance(prov, dict) and (prov.get("document_title") or prov.get("document_id")):
            raw_sources = [prov]

    if not raw_sources:
        dt = _get_val(q, "document_title") or _get_val(q, "source_presentation")
        doc_id = _get_val(q, "document_id")
        sn = _get_val(q, "source_slide_range")
        if dt or doc_id:
            raw_sources = [{"document_title": dt, "slide_number": sn, "document_id": doc_id}]

    # Resolve web URLs from database if available
    doc_urls = {}
    doc_titles = {}
    doc_ids_to_fetch = set()
    for s in raw_sources:
        did = _get_val(s, "document_id")
        if did:
            doc_ids_to_fetch.add(str(did))
    q_doc_id = _get_val(q, "document_id")
    if q_doc_id:
        doc_ids_to_fetch.add(str(q_doc_id))

    if doc_ids_to_fetch:
        try:
            from backend.app.database import SessionLocal
            from backend.app.models.document import Document
            from backend.app.models.sharepoint import SharePointFile
            with SessionLocal() as db:
                docs = db.query(Document).filter(Document.id.in_(list(doc_ids_to_fetch))).all()
                sp_ids = [d.sharepoint_file_id for d in docs if d.sharepoint_file_id]
                sp_urls = {}
                if sp_ids:
                    sp_files = db.query(SharePointFile).filter(SharePointFile.id.in_(sp_ids)).all()
                    for spf in sp_files:
                        if spf.web_url:
                            sp_urls[spf.id] = spf.web_url
                for d in docs:
                    if d.title:
                        doc_titles[str(d.id)] = d.title
                    u = sp_urls.get(d.sharepoint_file_id) or d.blob_url
                    if u:
                        doc_urls[d.id] = u
                        if d.title:
                            doc_urls[d.title] = u
        except Exception as e:
            logger.debug(f"_format_question_source_notes DB lookup error: {e}")

    seen = set()
    for s in raw_sources:
        doc_id = _get_val(s, "document_id")
        doc_title = (_get_val(s, "document_title") or doc_titles.get(str(doc_id)) or "").strip()
        slide_num = _get_val(s, "slide_number")
        url = (
            _get_val(s, "web_url")
            or _get_val(s, "source_url")
            or _get_val(s, "url")
            or _get_val(s, "link")
            or doc_urls.get(str(doc_id))
            or doc_urls.get(doc_title)
        )

        slide_str = f"Slide {slide_num}" if slide_num else ""
        if slide_str and str(slide_num).lower().startswith("slide"):
            slide_str = str(slide_num)

        ref_parts = []
        if doc_title:
            ref_parts.append(doc_title)
        if slide_str:
            ref_parts.append(f"({slide_str})")

        ref_text = " ".join(ref_parts) if ref_parts else (str(doc_id) if doc_id else "")
        if not ref_text and not url:
            continue

        key = (ref_text, url)
        if key in seen:
            continue
        seen.add(key)

        if ref_text and url:
            lines.append(f"Source: {ref_text}\nLink: {url}")
        elif url:
            lines.append(f"Source Link: {url}")
        else:
            lines.append(f"Source: {ref_text}")

    for u_field in ["fact_check_url", "source_url", "reference_link", "web_url"]:
        direct_url = _get_val(q, u_field)
        if direct_url and str(direct_url).strip() and str(direct_url).strip() not in "".join(lines):
            lines.append(f"Reference Link: {str(direct_url).strip()}")

    if not lines:
        return "Source: QShala Knowledge Vault"
    return "\n\n".join(lines)


def _strip_answer_from_notes(notes: str) -> str:
    """Remove any 'Official Answer:' lines so answers never leak onto Question slide notes."""
    if not notes:
        return ""
    cleaned = re.sub(r"(?im)^Official Answer:.*$", "", notes)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _get_original_slide_notes(q: Any) -> Tuple[str, str]:
    """Look up the original PowerPoint speaker notes from the ingested Slide(s) for this question.
    Returns (question_slide_notes, answer_slide_notes)."""
    raw_sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", []) or []
    q_slide_ids = []
    a_slide_ids = []
    hq_ids = []
    doc_slide_pairs = []

    for s in raw_sources:
        sid = _get_val(s, "slide_id")
        if sid:
            q_slide_ids.append(str(sid))
        hqid = _get_val(s, "historical_question_id")
        if hqid:
            hq_ids.append(str(hqid))
        did = _get_val(s, "document_id")
        snum = _get_val(s, "slide_number")
        if did and snum and not sid:
            try:
                doc_slide_pairs.append((str(did), int(snum)))
            except (TypeError, ValueError):
                pass

    for attr in ("slide_id",):
        val = _get_val(q, attr)
        if val:
            q_slide_ids.append(str(val))
    ans_sid = _get_val(q, "answer_slide_id")
    if ans_sid:
        a_slide_ids.append(str(ans_sid))
    hq_direct = _get_val(q, "historical_question_id") or _get_val(q, "duplicate_of_id")
    if hq_direct:
        hq_ids.append(str(hq_direct))

    if not q_slide_ids and not a_slide_ids and not hq_ids and not doc_slide_pairs:
        return "", ""

    q_notes_list: List[str] = []
    a_notes_list: List[str] = []
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.document import Slide
        from backend.app.models.question import Question
        with SessionLocal() as db:
            if hq_ids:
                hqs = db.query(Question).filter(Question.id.in_(list(set(hq_ids)))).all()
                for h in hqs:
                    if h.slide_id:
                        q_slide_ids.append(str(h.slide_id))
                    if h.answer_slide_id:
                        a_slide_ids.append(str(h.answer_slide_id))

            all_sids = list(dict.fromkeys(q_slide_ids + a_slide_ids))
            slide_map: Dict[str, str] = {}
            if all_sids:
                slides = db.query(Slide).filter(Slide.id.in_(all_sids)).all()
                for sl in slides:
                    if sl.speaker_notes and sl.speaker_notes.strip():
                        slide_map[str(sl.id)] = sl.speaker_notes.strip()

            for sid in dict.fromkeys(q_slide_ids):
                if sid in slide_map and slide_map[sid] not in q_notes_list:
                    q_notes_list.append(slide_map[sid])

            if not q_notes_list and doc_slide_pairs:
                for did, snum in doc_slide_pairs:
                    sl = db.query(Slide).filter(Slide.document_id == did, Slide.slide_number == snum).first()
                    if sl and sl.speaker_notes and sl.speaker_notes.strip():
                        if sl.speaker_notes.strip() not in q_notes_list:
                            q_notes_list.append(sl.speaker_notes.strip())

            for sid in dict.fromkeys(a_slide_ids):
                if sid in slide_map and slide_map[sid] not in a_notes_list and slide_map[sid] not in q_notes_list:
                    a_notes_list.append(slide_map[sid])
    except Exception as e:
        logger.debug(f"_get_original_slide_notes DB lookup error: {e}")

    q_notes = _strip_answer_from_notes("\n\n".join(q_notes_list))
    a_notes = "\n\n".join(a_notes_list).strip()
    return q_notes, a_notes


def _text_fits_at_28pt(text: str, options: list, w_in: float, h_in: float) -> bool:
    """Check if question text (at strictly 28pt) plus options fits comfortably in a w_in x h_in box."""
    paras = _question_paragraphs(text) if text else [""]
    usable_w_pt = max(1.0, (w_in - 0.2)) * 72.0
    chars_per_line = max(1, int(usable_w_pt / (28 * 0.47)))
    line_h_pt = 28 * 1.25  # ~35 pt per line
    
    q_lines = sum(max(1, -(-len(p) // chars_per_line)) for p in paras)
    para_gap_pt = max(0, len(paras) - 1) * 8.0
    
    opt_lines = 0
    opt_gap_pt = 0.0
    if options:
        opt_cpl = max(1, int(usable_w_pt / (22 * 0.47)))
        for o in options:
            opt_lines += max(1, -(-len(str(o)) // opt_cpl))
            opt_gap_pt += 8.0
            
    opt_h_pt = (opt_lines * (22 * 1.25)) + opt_gap_pt
    total_h_pt = (q_lines * line_h_pt) + para_gap_pt + opt_h_pt
    available_h_pt = (h_in - 0.2) * 72.0
    return total_h_pt <= available_h_pt


def _plan_media(aspect: float, text_len: int, has_options: bool = False):
    """Plan question/answer media against the amount of text. Returns (mode, text_box, media_box), inches.

    ``stacked``: short text on top, media centred below using the full card width (landscape images shine).
    ``side``: text left, media right with the media box sized to the image shape (portrait/square images
    get a tall, narrow box instead of being letter-boxed into a landscape one).
    """
    full_w = _SAFE_R - _SAFE_L
    if text_len <= 110 and aspect >= 0.9 and not has_options:
        return ("stacked",
                (_SAFE_L, _SAFE_T, full_w, 1.05),
                (_SAFE_L, _SAFE_T + 1.2, full_w, _SAFE_B - (_SAFE_T + 1.2)))
    box_h = _SAFE_B - _SAFE_T
    media_w = max(3.6, min(6.4, aspect * box_h))
    # long texts need breathing room: never let media take more than 45% of the width
    media_w = min(media_w, full_w * 0.45) if text_len > 220 else media_w
    gap = 0.45
    text_w = full_w - media_w - gap
    if text_w < 4.8:
        media_w = full_w - 4.8 - gap
        text_w = 4.8
    return ("side",
            (_SAFE_L, _SAFE_T, text_w, box_h),
            (_SAFE_R - media_w, _SAFE_T, media_w, box_h))


_SCHOOL_MASTER = Path(__file__).resolve().parent / "templates" / "qshala_school_master.pptx"


def _load_school_master():
    """The School template deck (artwork in its slide master/layouts, no slides), or None if it is missing."""
    if not _SCHOOL_MASTER.exists():
        logger.warning("School master deck missing at %s; falling back to picture backgrounds", _SCHOOL_MASTER)
        return None
    from pptx import Presentation
    return Presentation(str(_SCHOOL_MASTER))


def _all_layouts(prs):
    """Every slide layout in the deck, across ALL slide masters. `prs.slide_layouts` only lists the FIRST master's,
    but real sample decks usually have several masters (layouts named like "1_Title and Content" live in the others)."""
    out = []
    for master in prs.slide_masters:
        out.extend(master.slide_layouts)
    return out


def _find_layout(prs, name: str):
    """Layout with exactly this name in any master, or None."""
    for layout in _all_layouts(prs):
        if layout.name == name:
            return layout
    return None


def _layout_named(prs, name: str):
    layout = _find_layout(prs, name)
    if layout is None:
        raise KeyError(f"layout {name!r} not in the School master deck")
    return layout


def _layout_named_safe(prs, candidate_names: List[str]):
    layouts = _all_layouts(prs)
    for name in candidate_names:
        for layout in layouts:
            if layout.name and layout.name.lower() == name.lower():
                return layout
    return None


def _send_shape_to_back(shape, slide) -> None:
    """Move shape to the bottom of the slide's shape tree, preserving non-visual group properties."""
    try:
        spTree = slide.shapes._spTree
        sp = shape._element
        spTree.remove(sp)
        insert_idx = 0
        for i, child in enumerate(spTree):
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag in ("nvGrpSpPr", "grpSpPr"):
                insert_idx = i + 1
            else:
                break
        spTree.insert(insert_idx, sp)
    except Exception as e:
        logger.debug(f"Could not send shape to back: {e}")


# Safe boxes (x, y, w, h in inches) for the Connect loop, measured from each template's own artwork.
_CONNECT_SAFE = {
    "default": (0.62, 0.60, 12.07, 6.27),          # QShala School master card (0.37-12.94 x 0.35-7.12) inset 0.25, and plain slides
    "school": (0.70, 1.65, 11.95, 4.45),           # below the header and logo, above the trophies in the bottom corners
    "corporate": (0.80, 0.70, 9.40, 6.10),         # left of the logo and of the mascot at the bottom right
}


def _contrast_with_white(hex_colour: str) -> float:
    h = (hex_colour or "").lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except Exception:
        return 0.0
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return 1.05 / (lum + 0.05)


def _connect_backdrop(question_bg, is_qshala_school, is_minimalist, master_prs, layout, is_custom=False):
    """The backdrop picture a Connect slide needs, matching what this template's normal question slides get."""
    try:
        if is_minimalist or question_bg is None or not question_bg.exists():
            return None
        if master_prs is None:
            return question_bg                       # plain deck: every question slide carries the picture
        if is_qshala_school and not is_custom:
            return None                              # built-in QShala master: artwork is in the master, never a slide picture
        from types import SimpleNamespace
        # master-based deck (QShala School master, custom template): same rule as the normal question slide
        return None if _has_visual_background(SimpleNamespace(slide_layout=layout, shapes=[])) else question_bg
    except Exception:
        return None


def _connect_theme(quiz_type, is_qshala_school, is_minimalist, custom_template, font_head, font_body):
    """Fonts, colours and the safe box for the Connect builder (format_slides.CONNECT_DEFAULT_THEME holds the defaults)."""
    theme = {"font_head": font_head, "font_body": font_body, "bold_head": False, "safe_box": _CONNECT_SAFE["default"]}
    if is_qshala_school:
        theme.update(font_head="Causten Round", font_body="Causten Round", bold_head=True)
    if custom_template is None and not is_qshala_school:
        theme["safe_box"] = _CONNECT_SAFE.get(quiz_type if quiz_type in ("school", "corporate") else "default")
    if custom_template is not None:
        col = (getattr(custom_template, "title_color_hex", None) or "").lstrip("#")
        if len(col) == 6 and _contrast_with_white(col) >= 4.5:
            theme.update(line=col.upper(), hint_fill=col.upper(), title_color=col.upper())
    return theme


def _has_visual_background(slide) -> bool:
    """Check if the slide, its layout, or its master already has a background picture or fill."""
    try:
        from pptx.util import Inches
        # Check layout shapes
        if hasattr(slide, "slide_layout") and slide.slide_layout:
            for s in slide.slide_layout.shapes:
                if s.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                    return True
            if hasattr(slide.slide_layout, "slide_master") and slide.slide_layout.slide_master:
                for s in slide.slide_layout.slide_master.shapes:
                    if s.shape_type == 13:
                        return True
            bg_els = (
                slide.slide_layout._element.xpath("./p:bg//a:blipFill") +
                slide.slide_layout.slide_master._element.xpath("./p:bg//a:blipFill")
            )
            if bg_els:
                return True
        # Check slide shapes for fullscreen picture
        for s in slide.shapes:
            if s.shape_type == 13:
                try:
                    if (abs(s.width - Inches(13.333)) < Inches(1.0) or abs(s.height - Inches(7.5)) < Inches(1.0)):
                        return True
                except Exception:
                    return True
        bg_els_slide = slide._element.xpath("./p:bg//a:blipFill")
        if bg_els_slide:
            return True
    except Exception:
        pass
    return False


def _school_cover(prs, layout, title: str, subtitle: str, Pt):
    """Cover on the template's Title Slide layout: real title/subtitle placeholders over the master artwork."""
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    slide = prs.slides.add_slide(layout)
    ttl = slide.shapes.title if hasattr(slide.shapes, "title") else None
    if ttl is not None:
        ttl.text_frame.text = title
        ttl.name = "CoverTitle"
        for r in ttl.text_frame.paragraphs[0].runs:
            r.font.size = Pt(44) if len(title) <= 34 else Pt(36)
    else:
        tb = slide.shapes.add_textbox(Inches(1.5), Inches(2.5), Inches(10.33), Inches(2.0))
        tb.name = "CoverTitle"
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.alignment = PP_ALIGN.CENTER
        p.font.size = Pt(44) if len(title) <= 34 else Pt(36)
        p.font.bold = True

    sub_ph = next((ph for ph in slide.placeholders if ph.placeholder_format.idx == 1), None) if hasattr(slide, "placeholders") else None
    lines = [ln.strip() for ln in subtitle.splitlines() if ln.strip()]
    if sub_ph is not None and lines:
        tf = sub_ph.text_frame
        tf.text = lines[0]
        for ln in lines[1:]:
            tf.add_paragraph().text = ln
        for par in tf.paragraphs:
            for r in par.runs:
                r.font.size = Pt(32) if len(par.text) <= 24 else Pt(26)
    elif sub_ph is not None:
        sub_ph._element.getparent().remove(sub_ph._element)   # no empty "Click to add subtitle" box
    elif lines:
        tb_sub = slide.shapes.add_textbox(Inches(2.0), Inches(4.5), Inches(9.33), Inches(1.5))
        tf_sub = tb_sub.text_frame
        tf_sub.text = lines[0]
        for ln in lines[1:]:
            tf_sub.add_paragraph().text = ln
        for par in tf_sub.paragraphs:
            par.alignment = PP_ALIGN.CENTER
            for r in par.runs:
                r.font.size = Pt(30) if len(par.text) <= 24 else Pt(24)
    return slide


def _set_run_font(run, face: str) -> None:
    """Latin, East-Asian and complex-script typefaces all set, so no script of the run can fall back to the theme font."""
    from pptx.oxml.ns import qn
    run.font.name = face
    rPr = run._r.get_or_add_rPr()
    latin = rPr.find(qn("a:latin"))
    for tag in ("a:ea", "a:cs"):
        for old in rPr.findall(qn(tag)):
            rPr.remove(old)
    anchor = latin
    for tag in ("a:ea", "a:cs"):
        el = rPr.makeelement(qn(tag), {"typeface": face})
        anchor.addnext(el)
        anchor = el


def _is_title_placeholder(sh) -> bool:
    try:
        from pptx.enum.shapes import PP_PLACEHOLDER
        return sh.is_placeholder and sh.placeholder_format.type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
    except Exception:
        return False


def _apply_causten_style(prs) -> None:
    """Make every run explicit: Causten Round (standard weight), Causten Round Bold for headings only.

    Headings are the cover title, the question/answer number, the Answer divider and short slide headings (shapes
    named in _BOLD_SHAPE_NAMES, or title placeholders). PowerPoint ignores paragraph-level default run properties,
    so size/bold/colour are copied from the paragraph defaults onto each run. Answer text that used the old green
    is turned black.
    """
    from pptx.dml.color import RGBColor

    green = "059669"
    black = RGBColor(15, 23, 42)

    def _style_para(par, bold_face: bool):
        d = par.font  # paragraph-level defaults (what the builders set)
        size, color = d.size, None
        try:
            if d.color and d.color.type is not None and d.color.rgb is not None:
                color = d.color.rgb
        except Exception:
            color = None
        italic = getattr(d, 'italic', None)
        runs = list(par.runs)
        if not runs:
            return
        for r in runs:
            f = r.font
            _set_run_font(r, CAUSTEN_BOLD if bold_face else CAUSTEN_REGULAR)
            f.bold = True if bold_face else False
            if italic is not None and f.italic is None:
                f.italic = italic
            if f.size is None and size is not None:
                f.size = size
            c = color
            try:
                if f.color and f.color.type is not None and f.color.rgb is not None:
                    c = f.color.rgb
            except Exception:
                pass
            if c is not None:
                f.color.rgb = black if str(c).upper() == green.upper() else c
        d.name = CAUSTEN_BOLD if bold_face else CAUSTEN_REGULAR

    def _walk(shapes):
        for sh in shapes:
            if sh.shape_type == 6 and hasattr(sh, "shapes"):  # group
                _walk(sh.shapes)
                continue
            if sh.has_text_frame:
                bold_face = sh.name in _BOLD_SHAPE_NAMES or _is_title_placeholder(sh)
                for pi, par in enumerate(sh.text_frame.paragraphs):
                    # cover: only the title line is bold, the subtitle lines stay regular
                    _style_para(par, bold_face and (sh.name != "CoverTitle" or pi == 0))
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    for cell in row.cells:
                        for par in cell.text_frame.paragraphs:
                            _style_para(par, False)

    for slide in prs.slides:
        _walk(slide.shapes)


def _heading_font(name: str) -> str:
    """Style guide: Causten headings are always 'Causten Round Bold', whatever heavier weight the sample deck's cover used."""
    return "Causten Round Bold" if name and name.lower().startswith("causten round") else name


def _apply_custom_style(
    prs,
    font_regular: str,
    font_bold: str,
    title_size_pt: Optional[float] = None,
    header_size_pt: Optional[float] = None,
    body_size_pt: Optional[float] = None,
    skip_slide_ids: Optional[set] = None,
) -> None:
    """Make every run explicit with custom template fonts and typography sizes across Latin, East-Asian, and complex scripts."""
    from pptx.util import Pt
    def _style_para(par, bold_face: bool, is_cover_title: bool = False, is_header_shape: bool = False):
        d = par.font
        size, color = d.size, None
        try:
            if d.color and d.color.type is not None and d.color.rgb is not None:
                color = d.color.rgb
        except Exception:
            color = None
        italic = getattr(d, 'italic', None)
        par_bold = getattr(d, 'bold', None)
        runs = list(par.runs)
        if not runs:
            return
        for r in runs:
            f = r.font
            # Preserve existing bolding from builders (e.g. question prompt or answer title) or heading shape
            should_be_bold = bold_face or (par_bold is True) or (f.bold is True)
            target_font = font_bold if should_be_bold else font_regular
            _set_run_font(r, target_font)
            f.bold = True if should_be_bold else False
            if italic is not None and f.italic is None:
                f.italic = italic
            if f.size is None and size is not None:
                f.size = size
            # If custom template specifies custom font sizing:
            if is_cover_title and title_size_pt and 28.0 <= title_size_pt <= 60.0:
                f.size = Pt(title_size_pt)
            elif is_header_shape and header_size_pt and 20.0 <= header_size_pt <= 48.0:
                f.size = Pt(header_size_pt)
            elif not should_be_bold and body_size_pt and 14.0 <= body_size_pt <= 32.0 and not is_header_shape and not is_cover_title:
                cur_pt = f.size.pt if f.size else (size.pt if size else None)
                if cur_pt and 16.0 <= cur_pt <= 30.0:
                    f.size = Pt(body_size_pt)
            from pptx.dml.color import RGBColor
            green = "059669"
            black = RGBColor(15, 23, 42)
            c = color
            try:
                if f.color and f.color.type is not None and f.color.rgb is not None:
                    c = f.color.rgb
            except Exception:
                pass
            if c is not None and str(c).upper() in (green.upper(), "16A34A"):
                f.color.rgb = black
            elif color is not None:
                try:
                    if f.color is None or getattr(f.color, "type", None) is None:
                        f.color.rgb = color
                except Exception:
                    pass
        d.name = font_bold if bold_face else font_regular

    def _walk(shapes):
        for sh in shapes:
            if sh.shape_type == 6 and hasattr(sh, "shapes"):
                _walk(sh.shapes)
                continue
            if sh.has_text_frame:
                is_cover_title = (sh.name == "CoverTitle") or _is_title_placeholder(sh)
                is_header_shape = sh.name in _BOLD_SHAPE_NAMES or _is_title_placeholder(sh)
                bold_face = is_header_shape
                for pi, par in enumerate(sh.text_frame.paragraphs):
                    is_title_p = is_cover_title and pi == 0
                    is_header_p = bold_face and (sh.name != "CoverTitle" or pi == 0)
                    _style_para(
                        par,
                        bold_face=is_header_p,
                        is_cover_title=is_title_p,
                        is_header_shape=is_header_shape,
                    )
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    for cell in row.cells:
                        for par in cell.text_frame.paragraphs:
                            _style_para(par, bold_face=False)

    for slide in prs.slides:
        if skip_slide_ids and slide.slide_id in skip_slide_ids:
            continue   # the template's own cover / divider keep their designed fonts
        _walk(slide.shapes)


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

def _source_notes(q: Any) -> str:
    """The speaker notes from the question's original deck slides (question slide, then answer slide)."""
    sources = _get_val(q, "retrieval_sources", []) or _get_val(q, "sources", [])
    hist_id = _get_val(sources[0], "historical_question_id") if sources else None
    target_ids = [str(i) for i in [hist_id, _get_val(q, "id")] if i]
    if not target_ids:
        return ""
    db = None
    try:
        from backend.app.database import SessionLocal
        from backend.app.models.question import Question
        db = SessionLocal()
        hq = db.query(Question).filter(Question.id.in_(target_ids)).first()
        if not hq:
            return ""
        parts: List[str] = []
        for slide in (hq.slide, hq.answer_slide):
            text = ((slide.speaker_notes if slide else "") or "").strip()
            if text and text not in parts:
                parts.append(text)
        return "\n\n".join(parts)
    except Exception as e:
        logger.warning(f"Error fetching original speaker notes: {e}")
        return ""
    finally:
        if db is not None:
            db.close()


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
        template_id: Optional[str] = None,
        db: Optional[Any] = None,
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
        _patch_pptx_media_parts()
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
        elif quiz_type in ("zvq_corporate", "zsv_corporate", "corporate", "corp"):
            quiz_type = "corporate"
        elif quiz_type in ("zvq", "zvq_school", "zvq-school", "zsv", "zsv_school", "school"):
            quiz_type = "school"

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
                template_id=template_id,
                db=db,
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
        template_id: Optional[str] = None,
        db: Optional[Any] = None,
        **kwargs: Any,
    ) -> bytes:
        from pptx import Presentation
        _patch_pptx_media_parts()
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
        from pptx.enum.shapes import MSO_SHAPE
        from fastapi import HTTPException
        from backend.app.services.export import template_store
        from backend.app.services.export.slide_fit import fit_deck_to_slide, remove_empty_placeholders

        custom_template = None
        custom_dir: Optional[Path] = None
        if template_id and db:
            from backend.app.models.custom_template import CustomPptTemplate
            custom_template = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id).first()
            # A template the user explicitly picked must never be silently swapped for the default deck.
            if custom_template is None:
                raise HTTPException(status_code=404, detail="That PowerPoint template no longer exists. Pick another template.")
            custom_dir = template_store.ensure_local_dir(custom_template)
            if custom_dir is None:
                raise HTTPException(
                    status_code=409,
                    detail="This template's files are no longer on the server (it was saved before durable storage was on). "
                           "Upload the sample deck again to recreate it.",
                )

        is_qshala_school = (quiz_type in ("qshala_school", "qshala")) or (custom_template and custom_template.category == "qshala_school")
        master_prs = None
        cover_src = divider_src = None
        retained_ids: set = set()   # slides taken from the template as they are: never restyled, rescaled or rebuilt

        if custom_template and custom_dir is not None:
            try:
                prs = Presentation(str(custom_dir / template_store.MASTER_NAME))
                layouts_info = (custom_template.layout_metadata or {}).get("layouts", {})
                # The template's real title slide and Answer divider are kept in the master and reused as they are
                # (see TemplateExtractor._generate_clean_master). Any other slide in an old master is dropped.
                n_kept = len(prs.slides)
                ci, di = layouts_info.get("cover_slide_index"), layouts_info.get("divider_slide_index")
                cover_src = prs.slides[ci] if isinstance(ci, int) and 0 <= ci < n_kept else None
                divider_src = prs.slides[di] if isinstance(di, int) and 0 <= di < n_kept else None
                for sl in list(prs.slides):
                    if sl is not cover_src and sl is not divider_src:
                        drop_slide(prs, sl)
                cov_name = layouts_info.get("cover_layout")
                q_name = layouts_info.get("question_layout")
                div_name = layouts_info.get("divider_layout")

                cover_layout = (_find_layout(prs, cov_name) if cov_name else None) or _layout_named_safe(prs, ["Title Slide", "Title", "Cover"]) or _all_layouts(prs)[0]
                blank_layout = (_find_layout(prs, q_name) if q_name else None) or _layout_named_safe(prs, ["Title and Content", "Blank", "Title Only"]) or _all_layouts(prs)[-1]
                divider_layout = (_find_layout(prs, div_name) if div_name else None) or _layout_named_safe(prs, ["Section Header", "Header", "Title Only"]) or blank_layout
                master_prs = prs
            except Exception as e:
                logger.exception(f"Could not load custom master deck for template {custom_template.id}: {e}")
                raise HTTPException(
                    status_code=409,
                    detail="This template's master file could not be opened. Upload the sample deck again to recreate it.",
                )

        if master_prs is None:
            if is_qshala_school:
                master_prs = _load_school_master()
            if master_prs is not None:
                prs = master_prs
                blank_layout = _layout_named(prs, "Blank")
                cover_layout = _layout_named(prs, "Title Slide")
                divider_layout = _layout_named(prs, "Section Header")
            else:
                prs = Presentation()
                prs.slide_width = Inches(13.333)
                prs.slide_height = Inches(7.5)
                blank_layout = prs.slide_layouts[6]
                cover_layout = divider_layout = None

        # -------------------------------------------------------------
        # Template Asset Directory Setup
        # -------------------------------------------------------------
        assets_base = Path(__file__).resolve().parent / "templates" / "assets"
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

        # Override with custom template extracted backgrounds if available. A custom template must NOT inherit the
        # QShala/ZVQ default artwork: start from "no background" and only use what was extracted from its own deck.
        if custom_template and custom_dir is not None:
            cover_bg = question_bg = answer_intro_bg = answer_bg = track_dir / "__no_background__.png"
            for cand in ["cover_bg.png", "cover_bg.jpg", "cover_bg.jpeg"]:
                if (custom_dir / cand).exists():
                    cover_bg = custom_dir / cand
                    break
            for cand in ["question_bg.png", "question_bg.jpg", "question_bg.jpeg"]:
                if (custom_dir / cand).exists():
                    question_bg = custom_dir / cand
                    break
            for cand in ["answer_intro_bg.png", "answer_intro_bg.jpg", "answer_bg.png", "answer_bg.jpg"]:
                if (custom_dir / cand).exists():
                    answer_intro_bg = custom_dir / cand
                    break
            for cand in ["answer_bg.png", "answer_bg.jpg", "answer_bg.jpeg"]:
                if (custom_dir / cand).exists():
                    answer_bg = custom_dir / cand
                    break

        is_minimalist = (quiz_type == "minimalist")

        # -------------------------------------------------------------
        # Typography & Color Standards (Inter for ZSV, Causten Round for QShala School)
        # -------------------------------------------------------------
        if custom_template:
            default_head = "Causten Round Bold" if is_qshala_school else "Inter"
            default_body = "Causten Round Regular" if is_qshala_school else "Inter"
            FONT_HEAD = custom_template.header_font or default_head
            FONT_BODY = custom_template.body_font or default_body
        elif is_qshala_school:
            # Causten Round family only: standard weight for body text, Bold for headings.
            FONT_HEAD = CAUSTEN_REGULAR   # bold headings are decided per shape in _apply_causten_style
            FONT_BODY = CAUSTEN_REGULAR
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

        def _parse_hex_rgb(hex_code: Optional[str], default_rgb: RGBColor) -> RGBColor:
            if not hex_code:
                return default_rgb
            clean = hex_code.strip().lstrip("#")
            if len(clean) == 6:
                try:
                    return RGBColor(int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))
                except ValueError:
                    pass
            return default_rgb

        if custom_template:
            if custom_template.header_color_hex:
                C_TEXT_DARK = _parse_hex_rgb(custom_template.header_color_hex, C_TEXT_DARK)
            if custom_template.body_color_hex:
                C_TEXT_BODY = _parse_hex_rgb(custom_template.body_color_hex, C_TEXT_BODY)

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
        if master_prs is not None and cover_src is not None:
            # The template's own title slide, text replaced in place (logos, art and formatting untouched).
            s_title = cover_src
            retained_ids.add(s_title.slide_id)
            t_shape, sub_shape = find_title_and_subtitle(s_title)
            if t_shape is not None:
                fill_text(t_shape, final_title)
            sub_text = (custom_subtitle or "").strip()   # not the default campus line: the template's own tagline stays
            if sub_shape is not None and sub_text:
                fill_text(sub_shape, sub_text)
        elif master_prs is not None:
            s_title = _school_cover(prs, cover_layout, final_title, (custom_subtitle or campus_edition or "").strip(), Pt)
            if not _has_visual_background(s_title) and not is_minimalist and cover_bg.exists():
                pic_c = s_title.shapes.add_picture(str(cover_bg), 0, 0, prs.slide_width, prs.slide_height)
                _send_shape_to_back(pic_c, s_title)
        else:
            s_title = prs.slides.add_slide(blank_layout)

        if master_prs is not None:
            pass
        elif not is_minimalist and cover_bg.exists():
            s_title.shapes.add_picture(str(cover_bg), 0, 0, Inches(13.333), Inches(7.5))

            if is_qshala_school:
                # QShala Jr cover, matching the "Place your bets" title slide: logo + circle backdrop are
                # baked into cover_bg.png; title (bold) and subtitle sit in the circle, centred, bottom-anchored.
                from pptx.enum.text import MSO_ANCHOR
                tb_qshala_cov = s_title.shapes.add_textbox(Inches(2.61), Inches(3.10), Inches(8.11), Inches(2.98))
                tb_qshala_cov.name = "CoverTitle"
                tf_qcov = tb_qshala_cov.text_frame
                tf_qcov.word_wrap = True
                tf_qcov.vertical_anchor = MSO_ANCHOR.BOTTOM

                p_qc1 = tf_qcov.paragraphs[0]
                p_qc1.alignment = PP_ALIGN.CENTER
                p_qc1.text = final_title
                p_qc1.font.name = CAUSTEN_BOLD
                p_qc1.font.size = Pt(44) if len(final_title) <= 34 else Pt(36)
                p_qc1.font.bold = True
                p_qc1.font.color.rgb = C_TEXT_DARK

                sub_display = (custom_subtitle if custom_template else (custom_subtitle or campus_edition or "")).strip()
                if sub_display:
                    sub_lines = [ln.strip() for ln in sub_display.splitlines() if ln.strip()]
                    for k, ln in enumerate(sub_lines):
                        p_qc2 = tf_qcov.add_paragraph()
                        p_qc2.alignment = PP_ALIGN.CENTER
                        p_qc2.space_before = Pt(30) if k == 0 else Pt(0)
                        p_qc2.text = ln
                        p_qc2.font.name = CAUSTEN_REGULAR
                        p_qc2.font.size = Pt(40) if len(ln) <= 24 else Pt(32)
                        p_qc2.font.bold = False
                        p_qc2.font.color.rgb = C_TEXT_DARK
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
                # Corporate Cover Layout:
                # Template background has Zerodha Varsity Quiz pre-printed top-left (y=1.8" - 3.5")
                # and a blue dotted divider rule at y=4.0". Bottom-left has 'Corporate edition' at y=5.73".
                # Right side has the puzzle hero illustration (x >= 6.2").
                # The dynamic title box is cleanly LEFT-ALIGNED in the open left zone (x=0.88", y=4.15", w=5.40", h=1.45"),
                # perfectly flush with the artwork typography and free of collisions.
                tb_corp = s_title.shapes.add_textbox(Inches(0.88), Inches(4.15), Inches(5.40), Inches(1.45))
                tf_c = tb_corp.text_frame
                tf_c.word_wrap = True

                p_c1 = tf_c.paragraphs[0]
                p_c1.alignment = PP_ALIGN.LEFT
                p_c1.text = final_title
                p_c1.font.name = FONT_HEAD
                p_c1.font.size = Pt(28) if len(final_title) <= 30 else (Pt(24) if len(final_title) <= 48 else Pt(20))
                p_c1.font.bold = True
                p_c1.font.color.rgb = C_TEXT_DARK

                sub_raw = (custom_subtitle or campus_edition or "").strip()
                if sub_raw:
                    if not sub_raw.startswith("—") and not sub_raw.startswith("-"):
                        sub_display = f"— {sub_raw} —"
                    else:
                        sub_display = sub_raw
                    p_c2 = tf_c.add_paragraph()
                    p_c2.alignment = PP_ALIGN.LEFT
                    p_c2.space_before = Pt(8)
                    p_c2.text = sub_display
                    p_c2.font.name = FONT_HEAD
                    p_c2.font.size = Pt(18) if len(sub_display) <= 32 else Pt(15)
                    p_c2.font.bold = False
                    p_c2.font.italic = True
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
        if quiz_type == "school" and include_social_slides and not is_minimalist and not custom_template:
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
                before_fmt = len(prs.slides)
                if format_slides.add_format_question(
                    prs, blank_layout, payload=fp_q,
                    question_text=(_get_val(q, "question_text") or _get_val(q, "question", "") or "").strip(),
                    answer=(_get_val(q, "answer") or "").strip(),
                    explanation=(_get_val(q, "explanation") or "").strip(),
                    options=list(opts_q) if opts_q else None,
                    meta={}, idx=idx,
                    resolve=lambda u: _resolve_media_path(u, download_dir=media_tmp_dir),
                    question_bg=_connect_backdrop(question_bg, is_qshala_school, is_minimalist, master_prs, blank_layout, custom_template is not None),
                    answer_layout=divider_layout,
                    theme=_connect_theme(quiz_type, is_qshala_school, is_minimalist, custom_template, FONT_HEAD, FONT_BODY),
                ):
                    orig_q_notes, orig_a_notes = _get_original_slide_notes(q)
                    fmt_src_notes = _format_question_source_notes(q)
                    if len(prs.slides) > before_fmt:
                        last_s = prs.slides[-1]
                        a_parts = []
                        # Presenter notes on answer slide only: original notes, clues, and format payloads
                        # sit exclusively on the answer reveal slide, never on question slides.
                        if orig_q_notes:
                            a_parts.append(f"Original Notes:\n{orig_q_notes}")
                        v_clue_fmt = _get_val(q, "visual_clues") or _get_val(q, "clue")
                        if v_clue_fmt:
                            a_parts.append(f"Presenter Clues: {v_clue_fmt}")
                        if orig_a_notes and orig_a_notes != orig_q_notes:
                            a_parts.append(f"Original Answer Slide Notes:\n{orig_a_notes}")
                        if fmt_src_notes:
                            a_parts.append(fmt_src_notes)
                        existing_blob = last_s.notes_slide.notes_text_frame.text or ""
                        if existing_blob:
                            a_parts.append(existing_blob)
                        if a_parts:
                            last_s.notes_slide.notes_text_frame.text = "\n\n".join(a_parts)
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

            # School template: an image tied to the answer must never appear on the question slide — it would
            # give the answer away (the question image IS the branded product / ad, e.g. the Cornetto pack or
            # the Fevicol creative). question_images/image_refs are a catch-all that also holds answer images,
            # so drop any question image that is also an answer image; it still shows on the answer slide.
            if is_qshala_school:
                _ans_paths = set()
                for _ai in (list(_get_val(q, "answer_images", []) or []) + list(hist_media.get("answer_images", []) or [])):
                    if isinstance(_ai, str):
                        _rp = _resolve_media_path(_ai, download_dir=media_tmp_dir)
                        if _rp:
                            _ans_paths.add(str(_rp))
                if _ans_paths:
                    resolved_q_imgs = [p for p in resolved_q_imgs if str(p) not in _ans_paths]

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
            sq_img = None

            # ---------------------------------------------------------
            # (A) QUESTION SLIDE (uses question_bg: PPT.png with peach border)
            # ---------------------------------------------------------
            sq = prs.slides.add_slide(blank_layout)
            if master_prs is not None:
                if not _has_visual_background(sq) and not is_minimalist and question_bg.exists():
                    pic_q = sq.shapes.add_picture(str(question_bg), 0, 0, prs.slide_width, prs.slide_height)
                    _send_shape_to_back(pic_q, sq)
            elif not is_minimalist and question_bg.exists():
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
                tb_head.name = "QNum"
                p_hd = tb_head.text_frame.paragraphs[0]
                p_hd.alignment = PP_ALIGN.CENTER
                p_hd.text = f"Question {idx}"
                p_hd.font.name = FONT_HEAD
                p_hd.font.size = Pt(40)
                p_hd.font.bold = True
                p_hd.font.color.rgb = C_TEXT_DARK

                if has_media:
                    _q_aspect = _image_aspect(poster_path or (resolved_q_imgs[0] if resolved_q_imgs else None))
                    _opt_len = sum(len(str(o)) + 4 for o in options)
                    mode_q, (tl, tt, tw, th), (ml, mt, mw, mh) = _plan_media(_q_aspect, len(q_text) + _opt_len, has_options=bool(options))
                    stacked_q = (mode_q == "stacked")
                    q_paras = _question_paragraphs(q_text)
                    fits_same_slide = _text_fits_at_28pt(q_text, options, tw, th)

                    if fits_same_slide:
                        tb_q = sq.shapes.add_textbox(Inches(tl), Inches(tt), Inches(tw), Inches(th))
                        tf_q = tb_q.text_frame
                        tf_q.word_wrap = True
                        pq_main = tf_q.paragraphs[0]
                        for pi, para in enumerate(q_paras):
                            p = pq_main if pi == 0 else tf_q.add_paragraph()
                            p.alignment = PP_ALIGN.CENTER
                            if pi > 0:
                                p.space_before = Pt(8)
                            p.text = para
                            p.font.name = FONT_BODY
                            p.font.size = Pt(28)
                            p.font.bold = False
                            p.font.color.rgb = C_TEXT_DARK
                        if options:
                            for opt_idx, opt in enumerate(options):
                                opt_str = opt.get("text", "") if isinstance(opt, dict) else str(opt)
                                po = tf_q.add_paragraph()
                                po.alignment = PP_ALIGN.CENTER
                                po.space_before = Pt(8)
                                prefix = f"{chr(65 + opt_idx)}. " if opt_idx < 26 and not opt_str.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                                po.text = f"{prefix}{opt_str}"
                                po.font.name = FONT_BODY
                                po.font.size = Pt(22)
                                po.font.color.rgb = C_TEXT_BODY
                        if not stacked_q:
                            tf_q.vertical_anchor = MSO_ANCHOR.MIDDLE

                        media_left, media_top, media_w, media_h = Inches(ml), Inches(mt), Inches(mw), Inches(mh)
                        vid_h = media_h - Inches(0.45)  # leave room for the "VIDEO CLIP" caption

                        is_video_mention = bool(re.search(r"\bvideo\b|\bclip\b|\bwatch\b", q_text, re.I))
                        if video_path:
                            sq.shapes.add_movie(
                                str(video_path),
                                media_left, media_top, media_w, vid_h,
                                poster_frame_image=str(poster_path) if poster_path else None,
                                mime_type="video/mp4"
                            )
                            tb_badge = sq.shapes.add_textbox(media_left, media_top + vid_h + Inches(0.10), media_w, Inches(0.40))
                            pb = tb_badge.text_frame.paragraphs[0]
                            pb.alignment = PP_ALIGN.CENTER
                            pb.text = "▶ VIDEO CLIP"
                            pb.font.name = FONT_HEAD
                            pb.font.size = Pt(13)
                            pb.font.bold = True
                            pb.font.color.rgb = C_ACCENT_ORANGE
                        elif is_video_mention and poster_path:
                            _add_scaled_picture(sq, poster_path, media_left, media_top, media_w, vid_h)
                            tb_badge = sq.shapes.add_textbox(media_left, media_top + vid_h + Inches(0.10), media_w, Inches(0.40))
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
                        # Uniform School QShala Layout: When there is no space for the image at 28pt,
                        # render the full question text at 28pt, add "(image in next slide)",
                        # and place the image on the following slide.
                        tb_q = sq.shapes.add_textbox(Inches(1.20), Inches(1.65), Inches(10.933), Inches(4.70))
                        tf_q = tb_q.text_frame
                        tf_q.word_wrap = True
                        tf_q.vertical_anchor = MSO_ANCHOR.MIDDLE
                        pq_main = tf_q.paragraphs[0]
                        for pi, para in enumerate(q_paras):
                            p = pq_main if pi == 0 else tf_q.add_paragraph()
                            p.alignment = PP_ALIGN.CENTER
                            if pi > 0:
                                p.space_before = Pt(8)
                            p.text = para
                            p.font.name = FONT_BODY
                            p.font.size = Pt(28)
                            p.font.bold = False
                            p.font.color.rgb = C_TEXT_DARK

                        if options:
                            for opt_idx, opt in enumerate(options):
                                opt_str = opt.get("text", "") if isinstance(opt, dict) else str(opt)
                                po = tf_q.add_paragraph()
                                po.alignment = PP_ALIGN.CENTER
                                po.space_before = Pt(8)
                                prefix = f"{chr(65 + opt_idx)}. " if opt_idx < 26 and not opt_str.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                                po.text = f"{prefix}{opt_str}"
                                po.font.name = FONT_BODY
                                po.font.size = Pt(22)
                                po.font.color.rgb = C_TEXT_BODY

                        p_sub = tf_q.add_paragraph()
                        p_sub.alignment = PP_ALIGN.CENTER
                        p_sub.space_before = Pt(14)
                        if video_path:
                            p_sub.text = "(video in next slide)"
                        elif audio_path:
                            p_sub.text = "(audio in next slide)"
                        else:
                            p_sub.text = "(image in next slide)"
                        p_sub.font.name = FONT_BODY
                        p_sub.font.size = Pt(20)
                        p_sub.font.bold = False
                        p_sub.font.italic = True
                        p_sub.font.color.rgb = C_TEXT_MUTED

                        # Dedicated image clue slide immediately following
                        if master_prs is not None:
                            sq_img = prs.slides.add_slide(blank_layout)
                            if not _has_visual_background(sq_img) and not is_minimalist and question_bg.exists():
                                pic_qi = sq_img.shapes.add_picture(str(question_bg), 0, 0, prs.slide_width, prs.slide_height)
                                _send_shape_to_back(pic_qi, sq_img)
                        elif not is_minimalist and question_bg.exists():
                            sq_img = prs.slides.add_slide(blank_layout)
                            sq_img.shapes.add_picture(str(question_bg), 0, 0, Inches(13.333), Inches(7.5))
                        else:
                            sq_img = prs.slides.add_slide(blank_layout)
                            bg_qi = sq_img.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
                            bg_qi.fill.solid()
                            bg_qi.fill.fore_color.rgb = C_WHITE
                            bg_qi.line.fill.background()

                        tb_ihead = sq_img.shapes.add_textbox(Inches(1.00), Inches(0.70), Inches(11.333), Inches(0.75))
                        tb_ihead.name = "QNum"
                        pi_hd = tb_ihead.text_frame.paragraphs[0]
                        pi_hd.alignment = PP_ALIGN.CENTER
                        pi_hd.text = f"Question {idx}"
                        pi_hd.font.name = FONT_HEAD
                        pi_hd.font.size = Pt(40)
                        pi_hd.font.bold = True
                        pi_hd.font.color.rgb = C_TEXT_DARK

                        img_l, img_t, img_w, img_h = Inches(1.50), Inches(1.65), Inches(10.333), Inches(4.55)
                        is_video_mention = bool(re.search(r"\bvideo\b|\bclip\b|\bwatch\b", q_text, re.I))
                        if video_path:
                            vid_h = img_h - Inches(0.45)
                            sq_img.shapes.add_movie(
                                str(video_path),
                                img_l, img_t, img_w, vid_h,
                                poster_frame_image=str(poster_path) if poster_path else None,
                                mime_type="video/mp4"
                            )
                            tb_badge = sq_img.shapes.add_textbox(img_l, img_t + vid_h + Inches(0.08), img_w, Inches(0.40))
                            pb = tb_badge.text_frame.paragraphs[0]
                            pb.alignment = PP_ALIGN.CENTER
                            pb.text = "▶ VIDEO CLIP"
                            pb.font.name = FONT_HEAD
                            pb.font.size = Pt(13)
                            pb.font.bold = True
                            pb.font.color.rgb = C_ACCENT_ORANGE
                        elif is_video_mention and poster_path:
                            vid_h = img_h - Inches(0.45)
                            _add_scaled_picture(sq_img, poster_path, img_l, img_t, img_w, vid_h)
                            tb_badge = sq_img.shapes.add_textbox(img_l, img_t + vid_h + Inches(0.08), img_w, Inches(0.40))
                            pb = tb_badge.text_frame.paragraphs[0]
                            pb.alignment = PP_ALIGN.CENTER
                            pb.text = "▶ VIDEO CLIP"
                            pb.font.name = FONT_HEAD
                            pb.font.size = Pt(13)
                            pb.font.bold = True
                            pb.font.color.rgb = C_ACCENT_ORANGE
                        elif audio_path:
                            card = sq_img.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(4.20), Inches(2.20), Inches(4.933), Inches(3.20))
                            card.fill.solid()
                            card.fill.fore_color.rgb = C_CARD_BG
                            card.line.color.rgb = C_CARD_BORDER
                            card.line.width = Pt(1)
                            tf_c = card.text_frame
                            tf_c.word_wrap = True
                            p_c1 = tf_c.paragraphs[0]
                            p_c1.text = "🎧 AUDIO CLUE"
                            p_c1.font.name = FONT_HEAD
                            p_c1.font.size = Pt(18)
                            p_c1.font.bold = True
                            p_c1.font.color.rgb = C_ACCENT_GOLD
                            p_c1.alignment = PP_ALIGN.CENTER
                            sq_img.shapes.add_movie(
                                str(audio_path),
                                Inches(6.166), Inches(3.60), Inches(1.00), Inches(1.00),
                                poster_frame_image=str(poster_path) if poster_path else None,
                                mime_type="audio/mpeg"
                            )
                        elif resolved_q_imgs:
                            if len(resolved_q_imgs) == 2:
                                _add_scaled_picture(sq_img, resolved_q_imgs[0], Inches(1.50), Inches(1.65), Inches(4.90), img_h)
                                _add_scaled_picture(sq_img, resolved_q_imgs[1], Inches(6.933), Inches(1.65), Inches(4.90), img_h)
                            else:
                                _add_scaled_picture(sq_img, resolved_q_imgs[0], img_l, img_t, img_w, img_h)
                else:
                    # Pure text question: 28pt centered Causten Round (Style Guide compliant)
                    tb_q = sq.shapes.add_textbox(Inches(1.20), Inches(1.65), Inches(10.933), Inches(4.70))
                    tf_q = tb_q.text_frame
                    tf_q.word_wrap = True
                    tf_q.vertical_anchor = MSO_ANCHOR.MIDDLE
                    pq_main = tf_q.paragraphs[0]
                    q_paras = _question_paragraphs(q_text)
                    for pi, para in enumerate(q_paras):
                        p = pq_main if pi == 0 else tf_q.add_paragraph()
                        p.alignment = PP_ALIGN.CENTER
                        if pi > 0:
                            p.space_before = Pt(8)
                        p.text = para
                        p.font.name = FONT_BODY
                        p.font.size = Pt(28)
                        p.font.bold = False
                        p.font.color.rgb = C_TEXT_DARK

                    if options:
                        for opt_idx, opt in enumerate(options):
                            opt_str = opt.get("text", "") if isinstance(opt, dict) else str(opt)
                            po = tf_q.add_paragraph()
                            po.alignment = PP_ALIGN.CENTER
                            po.space_before = Pt(8)
                            prefix = f"{chr(65 + opt_idx)}. " if opt_idx < 26 and not opt_str.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                            po.text = f"{prefix}{opt_str}"
                            po.font.name = FONT_BODY
                            po.font.size = Pt(22)
                            po.font.color.rgb = C_TEXT_BODY
            elif has_media:
                # South Zone 2-Column Protocol: Question text left (7.80 in), Media clue right (3.90 in)
                tb_q = sq.shapes.add_textbox(Inches(0.80), Inches(1.50), Inches(7.80), Inches(4.80))
                tf_q = tb_q.text_frame
                tf_q.word_wrap = True

                q_font_size = Pt(28) if len(q_text) < 160 else (Pt(24) if len(q_text) < 240 else Pt(20))
                pq_main = tf_q.paragraphs[0]
                q_paras = _question_paragraphs(q_text)
                for pi, para in enumerate(q_paras):
                    p = pq_main if pi == 0 else tf_q.add_paragraph()
                    if pi > 0:
                        p.space_before = Pt(8)
                    p.text = para
                    p.font.name = FONT_HEAD
                    p.font.size = q_font_size
                    p.font.bold = True
                    p.font.color.rgb = C_TEXT_DARK

                if options:
                    for opt_idx, opt in enumerate(options):
                        opt_str = opt.get("text", "") if isinstance(opt, dict) else str(opt)
                        po = tf_q.add_paragraph()
                        po.space_before = Pt(8)
                        prefix = f"{chr(65 + opt_idx)}.  " if opt_idx < 26 and not opt_str.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                        po.text = f"{prefix}{opt_str}"
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

                q_font_size = Pt(32) if len(q_text) < 160 else (Pt(26) if len(q_text) < 260 else Pt(22))
                pq_main = tf_q.paragraphs[0]
                q_paras = _question_paragraphs(q_text)
                for pi, para in enumerate(q_paras):
                    p = pq_main if pi == 0 else tf_q.add_paragraph()
                    if pi > 0:
                        p.space_before = Pt(8)
                    p.text = para
                    p.font.name = FONT_HEAD
                    p.font.size = q_font_size
                    p.font.bold = True
                    p.font.color.rgb = C_TEXT_DARK

                if options:
                    for opt_idx, opt in enumerate(options):
                        opt_str = opt.get("text", "") if isinstance(opt, dict) else str(opt)
                        po = tf_q.add_paragraph()
                        po.space_before = Pt(12)
                        prefix = f"{chr(65 + opt_idx)}.  " if opt_idx < 26 and not opt_str.startswith(('A.', 'B.', 'C.', 'D.')) else ""
                        po.text = f"{prefix}{opt_str}"
                        po.font.name = FONT_BODY
                        po.font.size = Pt(20)
                        po.font.color.rgb = C_TEXT_BODY

            # ---------------------------------------------------------
            # (B) ANSWER TRANSITION SLIDE (Divider)
            # ---------------------------------------------------------
            render_divider = (show_answer_divider is not False)
            if render_divider:
                if master_prs is not None and divider_src is not None:
                    s_ai = duplicate_slide(prs, divider_src)   # the template's own Answer slide, as designed
                    retained_ids.add(s_ai.slide_id)
                elif master_prs is not None:
                    s_ai = prs.slides.add_slide(divider_layout)
                    ttl_ai = s_ai.shapes.title if hasattr(s_ai.shapes, "title") else None
                    if ttl_ai is not None:
                        ttl_ai.text_frame.text = "Answer"
                        ttl_ai.name = "Heading"
                        for _r in ttl_ai.text_frame.paragraphs[0].runs:
                            _r.font.size = Pt(50)
                    else:
                        tb_ai = s_ai.shapes.add_textbox(Inches(4.00), Inches(2.75), Inches(5.333), Inches(1.90))
                        tb_ai.name = "Heading"
                        pai = tb_ai.text_frame.paragraphs[0]
                        pai.alignment = PP_ALIGN.CENTER
                        pai.text = "Answer"
                        pai.font.name = FONT_HEAD
                        pai.font.size = Pt(50)
                        pai.font.bold = True
                        pai.font.color.rgb = C_TEXT_DARK
                    if (custom_template or not _has_visual_background(s_ai)) and not is_minimalist and answer_intro_bg.exists():
                        pic_ai = s_ai.shapes.add_picture(str(answer_intro_bg), 0, 0, prs.slide_width, prs.slide_height)
                        _send_shape_to_back(pic_ai, s_ai)
                else:
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
            pic_a = None
            if master_prs is not None:
                if (custom_template or not _has_visual_background(sa)) and not is_minimalist and answer_bg.exists():
                    pic_a = sa.shapes.add_picture(str(answer_bg), 0, 0, prs.slide_width, prs.slide_height)
                    _send_shape_to_back(pic_a, sa)
            elif not is_minimalist and answer_bg.exists():
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

            if custom_template and has_ans_img and pic_a is not None and question_bg.exists() and question_bg != answer_bg:
                # The sample deck's answer slide only had a small card for a short text answer; a picture would spill out of it.
                # The question background carries the large content card, so use it when the answer has a picture.
                try:
                    pic_a._element.getparent().remove(pic_a._element)
                    pic_a = sa.shapes.add_picture(str(question_bg), 0, 0, prs.slide_width, prs.slide_height)
                    _send_shape_to_back(pic_a, sa)
                except Exception:
                    logger.debug("answer card swap skipped", exc_info=True)

            if is_qshala_school:
                # QShala School Answer Reveal Layout (answer separator slide is enough; no redundant "Answer {idx}" header)
                if has_ans_img:
                    _a_aspect = _image_aspect(ans_img_path)
                    mode_a, (tl, tt, tw, th), (ml, mt, mw, mh) = _plan_media(_a_aspect, len(ans_val) + len(explanation))
                    if mode_a == "stacked" and explanation:
                        # keep the explanation on the slide: answer + explanation above a slightly shorter image
                        th, mh_trim = 1.55, 0.5
                        mt, mh = mt + mh_trim, mh - mh_trim
                    tb_a = sa.shapes.add_textbox(Inches(tl), Inches(tt), Inches(tw), Inches(th))
                    tf_a = tb_a.text_frame
                    tf_a.word_wrap = True
                    pa_head = tf_a.paragraphs[0]
                    pa_head.alignment = PP_ALIGN.CENTER
                    pa_head.text = ans_val
                    pa_head.font.name = FONT_HEAD
                    pa_head.font.size = Pt(40) if len(ans_val) < 30 else (Pt(32) if len(ans_val) < 60 else Pt(26))
                    pa_head.font.bold = False
                    pa_head.font.color.rgb = C_TEXT_DARK
                    if explanation:
                        exp_h = th - (0.9 if mode_a == "stacked" else 1.3)
                        pa_exp = tf_a.add_paragraph()
                        pa_exp.alignment = PP_ALIGN.CENTER
                        pa_exp.space_before = Pt(10)
                        pa_exp.text = explanation
                        pa_exp.font.name = FONT_BODY
                        pa_exp.font.size = Pt(_fit_font_pt(explanation, tw - 0.3, max(0.6, exp_h), sizes=(22, 20, 18, 16, 14)))
                        pa_exp.font.color.rgb = C_TEXT_BODY
                    if mode_a == "side":
                        tf_a.vertical_anchor = MSO_ANCHOR.MIDDLE
                    _add_scaled_picture(sa, ans_img_path, Inches(ml), Inches(mt), Inches(mw), Inches(mh))
                else:
                    if explanation:
                        tb_a = sa.shapes.add_textbox(Inches(1.20), Inches(1.80), Inches(10.933), Inches(1.40))
                        tf_a = tb_a.text_frame
                        tf_a.word_wrap = True
                        pa_head = tf_a.paragraphs[0]
                        pa_head.alignment = PP_ALIGN.CENTER
                        pa_head.text = ans_val
                        pa_head.font.name = FONT_HEAD
                        pa_head.font.size = Pt(44) if len(ans_val) < 40 else Pt(34)
                        pa_head.font.bold = True
                        pa_head.font.color.rgb = C_TEXT_DARK

                        tb_exp = sa.shapes.add_textbox(Inches(1.50), Inches(3.35), Inches(10.333), Inches(3.20))
                        tf_exp = tb_exp.text_frame
                        tf_exp.word_wrap = True
                        pexp = tf_exp.paragraphs[0]
                        pexp.alignment = PP_ALIGN.CENTER
                        pexp.text = explanation
                        pexp.font.name = FONT_BODY
                        pexp.font.size = Pt(24) if len(explanation) < 180 else Pt(20)
                        pexp.font.color.rgb = C_TEXT_BODY
                    else:
                        tb_a = sa.shapes.add_textbox(Inches(1.20), Inches(2.20), Inches(10.933), Inches(3.00))
                        tf_a = tb_a.text_frame
                        tf_a.word_wrap = True
                        tf_a.vertical_anchor = MSO_ANCHOR.MIDDLE
                        pa_head = tf_a.paragraphs[0]
                        pa_head.alignment = PP_ALIGN.CENTER
                        pa_head.text = ans_val
                        pa_head.font.name = FONT_HEAD
                        pa_head.font.size = Pt(48) if len(ans_val) < 40 else Pt(38)
                        pa_head.font.bold = True
                        pa_head.font.color.rgb = C_TEXT_DARK
            elif has_ans_img:
                if explanation and len(explanation) > 100:
                    # 2-Column Solution Layout
                    tb_a = sa.shapes.add_textbox(Inches(0.80), Inches(1.50), Inches(6.80), Inches(5.00))
                    tf_a = tb_a.text_frame
                    tf_a.word_wrap = True

                    pa_head = tf_a.paragraphs[0]
                    pa_head.alignment = PP_ALIGN.CENTER
                    pa_head.text = ans_val
                    pa_head.font.name = FONT_HEAD
                    pa_head.font.size = Pt(34) if len(ans_val) < 40 else (Pt(28) if len(ans_val) < 80 else Pt(22))
                    pa_head.font.bold = True
                    pa_head.font.color.rgb = C_TEXT_DARK

                    pa_exp = tf_a.add_paragraph()
                    pa_exp.alignment = PP_ALIGN.CENTER
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
                # Text-Only Answer Slide: Large black reveal
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

            original_notes = _source_notes(q)
            if original_notes:
                sa.notes_slide.notes_text_frame.text = original_notes

        if divider_src is not None:
            drop_slide(prs, divider_src)   # exemplar only: each divider above is a copy of it

        if custom_template:
            _apply_custom_style(
                prs,
                custom_template.body_font or FONT_BODY,
                _heading_font(custom_template.header_font or FONT_HEAD),
                title_size_pt=44.0,   # style guide: title 44pt, body 28pt on every custom template (older templates stored averages)
                header_size_pt=custom_template.header_size_pt,
                body_size_pt=28.0,
                skip_slide_ids=retained_ids,
            )
        elif is_qshala_school:
            _apply_causten_style(prs)

        # Template cleanup: no ghost "Click to add title" boxes, and everything re-mapped onto the template's own slide
        # size (custom decks are often 10 x 5.625 or 4:3, the layout code above works on a 13.333 x 7.5 canvas).
        if master_prs is not None:
            remove_empty_placeholders(prs)
            fit_deck_to_slide(prs, skip_slide_ids=retained_ids)

        buffer = io.BytesIO()
        prs.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()
