import io
import os
import re
import shutil
import posixpath
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import hashlib
import mimetypes
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from backend.app.config import settings
from backend.app.services.ingestion.pptx_structure import extract_slide_structure, classify_overlay_pixels

logger = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".wma", ".ogg", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".wmv", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff"}

# python-pptx's canonical extension for the image formats it understands (same table as pptx.parts.image.Image.ext).
_PIL_FORMAT_EXT = {"BMP": "bmp", "GIF": "gif", "JPEG": "jpg", "PNG": "png", "TIFF": "tiff", "WMF": "wmf"}

# PowerPoint's generic "video camera" poster icon -- never a quiz picture.
_VIDEO_ICON_MD5 = "c050197d0f5c8b511e976e3975d4d36f"
# The icon is tiny; only files up to this size are hashed to look for it (bigger files can't be it).
_ICON_CHECK_MAX_BYTES = 512 * 1024
# Media identity (shape.media_md5) is a real MD5 up to this size; bigger entries use size + CRC instead.
_MD5_MAX_BYTES = 512 * 1024

# Parts the "lite" copy of the deck keeps byte-for-byte. Everything else (pictures, audio, video,
# embedded workbooks, fonts...) is replaced by a 1-byte placeholder -- python-pptx only needs the
# XML and the relationship files to read text, shapes and notes.
_LITE_KEEP_SUFFIXES = (".xml", ".rels")
_LITE_PLACEHOLDER = b"\x00"

# Folders whose relationship files point at template art (layout / master / theme backgrounds).
_TEMPLATE_REL_PREFIXES = (
    "ppt/slideLayouts/_rels/", "ppt/slideMasters/_rels/", "ppt/theme/_rels/",
    "ppt/notesMasters/_rels/", "ppt/handoutMasters/_rels/",
)

_COPY_CHUNK = int(os.getenv('STREAM_COPY_CHUNK', str(2 * 1024 * 1024)))
UPLOAD_CONCURRENCY = int(os.getenv('UPLOAD_CONCURRENCY', '32'))
UPLOAD_SLOTS = int(os.getenv('UPLOAD_SLOTS', '64'))


def _attach_template_media() -> bool:
    """PPTX_ATTACH_TEMPLATE_MEDIA=1 restores the old behaviour of filing layout/master/theme art under slide 1."""
    return os.getenv("PPTX_ATTACH_TEMPLATE_MEDIA", "").strip().lower() in {"1", "true", "yes", "on"}


def clean_pptx_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = text.replace("\x0b", "\n").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\xa0", " ")
    return text.strip()


def build_lite_package(file_path: str) -> io.BytesIO:
    """Copies a .pptx into memory with every non-XML part replaced by a 1-byte placeholder.

    A 200 MB deck becomes a few MB, and python-pptx can parse its structure (slides, text, tables,
    notes, relationships) without ever loading a picture, audio or video into memory. The real media
    is read on demand from the original file, one entry at a time.
    """
    out = io.BytesIO()
    with zipfile.ZipFile(file_path, "r") as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zout:
        for info in zin.infolist():
            if info.is_dir():
                continue
            if info.filename.lower().endswith(_LITE_KEEP_SUFFIXES):
                zout.writestr(info.filename, zin.read(info))
            else:
                zout.writestr(info.filename, _LITE_PLACEHOLDER)
    out.seek(0)
    return out


class PPTXParser:
    """
    Extracts structured content, text, speaker notes, tables,
    and embedded multi-modal media (images, audio clips, video clips)
    from PPTX presentation decks.

    Memory model (built for a 512 MB server): python-pptx is given a "lite" copy of the deck whose
    media parts are placeholders, so structure parsing costs a few MB no matter how big the deck is.
    Media is then streamed one file at a time from the original zip to disk (1 MB chunks) and
    uploaded to R2 from that file -- no picture, audio clip or video is ever held in memory whole.
    Each distinct file (by size + CRC) is stored once per deck and shared by every slide that uses it.
    """
    def __init__(self, file_path: str, document_id: str, write_to_disk: bool = True):
        self.file_path = file_path
        self.document_id = document_id
        self.write_to_disk = write_to_disk
        # Optional zero-arg callable; when it returns True parsing aborts (user cancelled).
        self.should_cancel = None
        self.on_progress = None   # optional callable(detail, pct=None) for live status
        # Media uploads to R2 run in a small thread pool (one slow network call per image used to be serial).
        self._executor: Optional[ThreadPoolExecutor] = None
        self._pending: List[Any] = []
        self._zip: Optional[zipfile.ZipFile] = None
        self._zip_lock = threading.Lock()
        # Per-deck media bookkeeping (see _store_media).
        self._by_content: Dict[Tuple[int, int], Tuple[str, str, Optional[Path]]] = {}   # (size, crc) -> (filename, url, path)
        self._used_filenames: Set[str] = set()
        self._header_cache: Dict[str, Optional[Tuple[str, Tuple[int, int]]]] = {}
        self._icon_cache: Dict[str, bool] = {}
        self._md5_cache: Dict[str, Optional[str]] = {}
        self._overlay_cache: Dict[str, str] = {}
        self._tmp_dir: Optional[Path] = None             # staging area when write_to_disk=False but R2 is on
        self._upload_slots = threading.BoundedSemaphore(UPLOAD_SLOTS)
        self._stats = {"stored": 0, "stored_bytes": 0, "reused": 0, "template_skipped": 0}
        self.output_dir = settings.SLIDES_DIR / document_id
        self.output_media_dir = self.output_dir / "media"
        # Mirror extracted media to Next.js public directory if it exists for Edge CDN delivery
        self.frontend_media_dir = Path(__file__).resolve().parents[4] / "frontend" / "public" / "static" / "slides" / document_id / "media"
        if self.write_to_disk:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.output_media_dir.mkdir(parents=True, exist_ok=True)
            try:
                if self.frontend_media_dir.parent.parent.exists():
                    self.frontend_media_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------ zip access

    def _get_zip(self) -> zipfile.ZipFile:
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.file_path, "r")
        return self._zip

    def _close_zip(self) -> None:
        with self._zip_lock:
            if self._zip is not None:
                try:
                    self._zip.close()
                finally:
                    self._zip = None

    def _entry_info(self, zip_name: str) -> Optional[zipfile.ZipInfo]:
        with self._zip_lock:
            try:
                return self._get_zip().getinfo(zip_name)
            except KeyError:
                return None

    def _read_zip(self, name: str) -> bytes:
        """Read one (small) entry from the deck on demand. Large media is streamed, never read whole."""
        with self._zip_lock:
            return self._get_zip().read(name)

    def _image_header(self, zip_name: str) -> Optional[Tuple[str, Tuple[int, int]]]:
        """(PIL format, (width, height)) read from the file header only; None when it is not a readable raster image."""
        if zip_name in self._header_cache:
            return self._header_cache[zip_name]
        result = None
        try:
            from PIL import Image
            with self._zip_lock:
                with self._get_zip().open(zip_name) as fh:
                    with Image.open(fh) as im:           # lazy: decodes the header, not the pixels
                        result = (im.format, tuple(im.size))
        except Exception as e:
            logger.debug(f"Could not read image header of {zip_name}: {e}")
        self._header_cache[zip_name] = result
        return result

    def _is_video_icon(self, zip_name: str, size: int) -> bool:
        if size > _ICON_CHECK_MAX_BYTES:
            return False
        cached = self._icon_cache.get(zip_name)
        if cached is None:
            try:
                cached = hashlib.md5(self._read_zip(zip_name)).hexdigest() == _VIDEO_ICON_MD5
            except Exception:
                cached = False
            self._icon_cache[zip_name] = cached
        return cached

    # ------------------------------------------------------------------ storing media

    def _unique_filename(self, wanted: str) -> str:
        name = wanted
        stem, suffix = os.path.splitext(wanted)
        n = 2
        while name in self._used_filenames:
            name = f"{stem}_{n}{suffix}"
            n += 1
        self._used_filenames.add(name)
        return name

    def _store_media(self, zip_name: str, wanted_filename: str) -> Tuple[str, str, Optional[Path]]:
        """
        Saves one media file from the deck and returns (filename, servable_url, local_path).

        The bytes are streamed from the zip straight to disk -- never held whole in memory. A file whose
        content (size + CRC) was already stored for this deck is not written or uploaded again; the
        earlier file's name/URL is returned, so a picture used on ten slides is saved once.

        With write_to_disk the local copy is kept (the vision/OCR step and the exporter read it).
        When R2 is configured the file is also uploaded and the *public R2 URL* is the servable path;
        the frontend already passes any absolute http(s) URL straight through (resolveMediaUrl).
        """
        info = self._entry_info(zip_name)
        if info is None:
            raise KeyError(zip_name)
        content_key = (info.file_size, info.CRC)
        existing = self._by_content.get(content_key)
        if existing is not None:
            self._stats["reused"] += 1
            return existing

        filename = self._unique_filename(wanted_filename)
        servable_url = f"/static/slides/{self.document_id}/media/{filename}"
        local_path: Optional[Path] = self.output_media_dir / filename if self.write_to_disk else None

        r2 = None
        try:
            from backend.app.services.storage.r2_storage import get_media_storage
            r2 = get_media_storage()
        except Exception as e:
            logger.warning(f"R2 not available for {filename}, falling back to local URL: {e}")

        stage_path: Optional[Path] = local_path
        if stage_path is None and r2 is not None:       # no local copy wanted, but R2 needs a file to read from
            if self._tmp_dir is None:
                self._tmp_dir = Path(tempfile.mkdtemp(prefix=f"pptx_media_{self.document_id}_"))
            stage_path = self._tmp_dir / filename

        if stage_path is not None:
            self._stream_entry_to(zip_name, stage_path)
            if self.write_to_disk and self.frontend_media_dir.exists():
                try:
                    f_dest = self.frontend_media_dir / filename
                    try:
                        if f_dest.exists():
                            f_dest.unlink()
                        os.link(stage_path, f_dest)      # same bytes, no second copy on disk
                    except OSError:
                        shutil.copyfile(stage_path, f_dest)
                except Exception:
                    pass

        if r2 is not None and stage_path is not None:
            r2_key = f"slides/{self.document_id}/media/{filename}"
            # The public URL is predictable, so the upload can finish in the background.
            servable_url = f"{r2.public_base_url}/{r2_key.lstrip('/')}"
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=UPLOAD_CONCURRENCY)
            temp_copy = stage_path if not self.write_to_disk else None
            if temp_copy is not None:
                self._upload_slots.acquire()            # bound the staged files waiting to upload
            self._pending.append((filename, self._executor.submit(
                self._upload_task, r2, r2_key, stage_path, temp_copy)))

        self._stats["stored"] += 1
        self._stats["stored_bytes"] += info.file_size
        record = (filename, servable_url, local_path)
        self._by_content[content_key] = record
        return record

    def _stream_entry_to(self, zip_name: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            with self._zip_lock:
                with self._get_zip().open(zip_name) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, _COPY_CHUNK)
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _upload_task(self, r2, key: str, path: Path, temp_copy: Optional[Path]):
        try:
            return self._upload_with_retry(r2, key, str(path))
        finally:
            if temp_copy is not None:
                try:
                    temp_copy.unlink()
                except OSError:
                    pass
                self._upload_slots.release()

    @staticmethod
    def _upload_with_retry(r2, key: str, path: str, attempts: int = 3):
        import time as _t
        for i in range(attempts):
            try:
                if hasattr(r2, "upload_file"):
                    return r2.upload_file(key, path)
                with open(path, "rb") as f:            # storage backend without file upload support
                    return r2.upload_bytes(key, f.read())
            except Exception:
                if i == attempts - 1:
                    raise
                _t.sleep(1.0 * (i + 1))

    def _finish_uploads(self) -> None:
        failed = 0
        total = len(self._pending)
        if not total:
            return
        fut_map = {fut: name for name, fut in self._pending}
        for i, fut in enumerate(as_completed(fut_map), start=1):
            name = fut_map[fut]
            if self.on_progress is not None:
                try:
                    self.on_progress(f"Saving pictures to storage ({i}/{total})")
                except Exception:
                    pass
            try:
                fut.result()
            except Exception as e:
                failed += 1
                logger.warning(f"R2 upload failed for {name} after retries: {e}")
        self._pending = []
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
        if failed:
            logger.warning(f"{failed} media file(s) did not reach R2 for document {self.document_id}")

    def _abort_uploads(self) -> None:
        """Called when parsing fails/cancels: stop queued uploads quickly instead of finishing them all."""
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        self._pending = []
        if self._tmp_dir is not None:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None

    # ------------------------------------------------------------------ classification

    def _classify_media_type(self, ext: str) -> str:
        ext_lower = ext.lower()
        if ext_lower in AUDIO_EXTS:
            return "audio"
        if ext_lower in VIDEO_EXTS:
            return "video"
        if ext_lower in IMAGE_EXTS:
            return "image"
        return "other"

    def _is_valid_quiz_image(self, zip_name: str, ext: str, dims: Optional[Tuple[int, int]] = None) -> bool:
        """
        Filters out presentation clutter:
        - Animated GIFs (in tournament decks these are timers / countdowns)
        - Tiny spacer artifacts (< 2 KB or < 80 px)
        - PowerPoint's generic video-camera poster icon
        Decided from the zip directory and (for dims) the image header -- the file is not read into memory.
        """
        ext_clean = ext.lower().lstrip(".")
        if ext_clean == "gif":
            return False
        info = self._entry_info(zip_name)
        if info is None or info.file_size < 2048:  # Under 2 KB
            return False
        if self._is_video_icon(zip_name, info.file_size):
            return False
        if dims and isinstance(dims, (tuple, list)) and len(dims) == 2:
            w, h = dims[0], dims[1]
            if (w is not None and w < 80) or (h is not None and h < 80):
                return False
        return True

    def _media_md5(self, zip_name: str) -> Optional[str]:
        """MD5 of one media entry, streamed in 1 MB chunks (never whole in memory) and cached per entry.
        Identical files reused across slides get the same value, which is how decorative media is told apart."""
        if zip_name in self._md5_cache:
            return self._md5_cache[zip_name]
        digest = None
        info = self._entry_info(zip_name)
        if info is not None and info.file_size > _MD5_MAX_BYTES:
            # Big files (timer videos, music) are identified by size + CRC from the zip directory: free, and
            # equally good at telling "same file reused on many slides" without reading hundreds of MB.
            digest = f"crc{info.CRC:08x}{info.file_size:x}"
            self._md5_cache[zip_name] = digest
            return digest
        try:
            h = hashlib.md5()
            with self._zip_lock:
                with self._get_zip().open(zip_name) as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            digest = h.hexdigest()
        except Exception as e:
            logger.debug(f"Could not hash {zip_name}: {e}")
        self._md5_cache[zip_name] = digest
        return digest

    def _overlay_class(self, zip_name: str) -> str:
        """'tick' | 'cross' | 'unknown' from the picture's pixels (List-It overlays; file names mean nothing)."""
        if zip_name in self._overlay_cache:
            return self._overlay_cache[zip_name]
        result = "unknown"
        info = self._entry_info(zip_name)
        if info is not None and info.file_size <= 2 * 1024 * 1024:       # overlays are small icons
            try:
                from PIL import Image
                with self._zip_lock:
                    with self._get_zip().open(zip_name) as fh:
                        with Image.open(fh) as im:
                            im.seek(0)
                            result = classify_overlay_pixels(im.copy())
            except Exception as e:
                logger.debug(f"Could not classify overlay {zip_name}: {e}")
        self._overlay_cache[zip_name] = result
        return result

    # ------------------------------------------------------------------ zip relationship scan

    @staticmethod
    def _rel_targets(rel_xml: str, rels_name: str) -> List[str]:
        """Resolved zip names of the internal targets in one .rels file, in file order."""
        # ppt/slides/_rels/slide1.xml.rels describes the part ppt/slides/slide1.xml
        source_dir = posixpath.dirname(posixpath.dirname(rels_name))
        out = []
        for tag in re.findall(r"<Relationship\b[^>]*>", rel_xml):
            if re.search(r'TargetMode="External"', tag):
                continue
            m = re.search(r'Target="([^"]+)"', tag)
            if not m:
                continue
            target = m.group(1)
            if target.startswith("/"):
                out.append(target.lstrip("/"))
            else:
                out.append(posixpath.normpath(posixpath.join(source_dir, target)))
        return out

    def _scan_zip_media(self) -> Tuple[Dict[int, List[Dict[str, Any]]], List[Dict[str, Any]]]:
        """
        Inspects the PPTX zip directly, reading ppt/slides/_rels/slide{N}.xml.rels to map ppt/media/* files.
        Returns (media per slide FILE number, unmapped media). Unmapped media is whatever no slide points at;
        template art (layouts/masters/themes) is dropped from it unless PPTX_ATTACH_TEMPLATE_MEDIA is set.
        """
        mapped: Dict[int, List[Dict[str, Any]]] = {}
        unmapped: List[Dict[str, Any]] = []
        if not os.path.exists(self.file_path):
            return mapped, unmapped

        try:
            with self._zip_lock:
                z = self._get_zip()
                names = z.namelist()
                media_entries = {n for n in names if n.startswith("ppt/media/") and not n.endswith("/")}
                if not media_entries:
                    return mapped, unmapped

                slide_rel_pattern = re.compile(r"ppt/slides/_rels/slide(\d+)\.xml\.rels")
                mapped_media_keys = set()
                template_keys = set()

                for rel_name in names:
                    m = slide_rel_pattern.match(rel_name)
                    if m:
                        slide_num = int(m.group(1))
                        rel_xml = z.read(rel_name).decode("utf-8", errors="ignore")
                        for clean_target in self._rel_targets(rel_xml, rel_name):
                            if clean_target in media_entries:
                                mapped_media_keys.add(clean_target)
                                ext = Path(clean_target).suffix.lower()
                                mime, _ = mimetypes.guess_type(clean_target)
                                mapped.setdefault(slide_num, []).append({
                                    "filename": Path(clean_target).name,
                                    "slide_number": slide_num,
                                    "media_type": self._classify_media_type(ext),
                                    "zip_name": clean_target,
                                    "mime_type": mime or "application/octet-stream",
                                    "source": "zip_rel"
                                })
                    elif rel_name.startswith(_TEMPLATE_REL_PREFIXES) and rel_name.endswith(".rels"):
                        rel_xml = z.read(rel_name).decode("utf-8", errors="ignore")
                        template_keys.update(t for t in self._rel_targets(rel_xml, rel_name) if t in media_entries)

                attach_template = _attach_template_media()
                for key in sorted(media_entries):
                    if key in mapped_media_keys:
                        continue
                    if key in template_keys and not attach_template:
                        self._stats["template_skipped"] += 1
                        continue
                    ext = Path(key).suffix.lower()
                    mime, _ = mimetypes.guess_type(key)
                    unmapped.append({
                        "filename": Path(key).name,
                        "slide_number": 1,
                        "media_type": self._classify_media_type(ext),
                        "zip_name": key,
                        "mime_type": mime or "application/octet-stream",
                        "source": "zip_unmapped"
                    })
        except Exception as e:
            logger.warning(f"PPTX zipfile fallback inspection warning: {e}")

        return mapped, unmapped

    def _slide_file_numbers(self) -> List[Optional[int]]:
        """File number (slideN.xml) of each slide in presentation order.

        python-pptx renames slide parts to match their position when it opens a deck, so after that the
        original file numbers are gone; a deck whose slides were reordered in PowerPoint would otherwise
        get each slide's zip-fallback media attached to the wrong slide.
        """
        try:
            pres = self._read_zip("ppt/presentation.xml").decode("utf-8", errors="ignore")
            rels = self._read_zip("ppt/_rels/presentation.xml.rels").decode("utf-8", errors="ignore")
            targets = {}
            for tag in re.findall(r"<Relationship\b[^>]*>", rels):
                rid = re.search(r'\bId="([^"]+)"', tag)
                tgt = re.search(r'\bTarget="([^"]+)"', tag)
                if rid and tgt:
                    targets[rid.group(1)] = tgt.group(1)
            numbers: List[Optional[int]] = []
            for rid in re.findall(r'<p:sldId\b[^>]*\br:id="([^"]+)"', pres):
                m = re.search(r"slides/slide(\d+)\.xml$", targets.get(rid, ""))
                numbers.append(int(m.group(1)) if m else None)
            return numbers
        except Exception as e:
            logger.debug(f"Could not read slide order from the deck: {e}")
            return []

    def extract_media_from_zip(self) -> Dict[int, List[Dict[str, Any]]]:
        """Compatibility wrapper: media per slide file number, with unmapped media filed under slide 1."""
        mapped, unmapped = self._scan_zip_media()
        if unmapped:
            mapped.setdefault(1, []).extend(unmapped)
        return mapped

    # ------------------------------------------------------------------ parsing

    def parse(self) -> List[Dict[str, Any]]:
        ok = False
        try:
            result = self._parse_impl()
            ok = True
            return result
        finally:
            if ok:
                self._finish_uploads()
            else:
                self._abort_uploads()
            self._close_zip()

    def _open_presentation(self):
        lite = build_lite_package(self.file_path)
        try:
            return Presentation(lite)
        finally:
            lite.close()

    def _media_item(self, filename: str, slide_idx: int, media_type: str, mime: str,
                    local_path: Optional[Path], rel_url: str) -> Dict[str, Any]:
        return {
            "filename": filename,
            "slide_number": slide_idx,
            "media_type": media_type,
            "mime_type": mime,
            "local_path": str(local_path) if (self.write_to_disk and local_path is not None) else None,
            "rel_url": rel_url
        }

    def _parse_impl(self) -> List[Dict[str, Any]]:
        prs = self._open_presentation()
        slides_data = []
        zip_media, unmapped_media = self._scan_zip_media()
        total_slides = len(prs.slides)
        slide_file_numbers = self._slide_file_numbers()
        if len(slide_file_numbers) != total_slides:
            slide_file_numbers = list(range(1, total_slides + 1))

        for slide_idx, slide in enumerate(prs.slides, start=1):
            if self.on_progress is not None:
                try:
                    self.on_progress(f"Reading slide {slide_idx} of {total_slides}, saving pictures and media",
                                     20 + 24.0 * slide_idx / max(total_slides, 1))
                except Exception:
                    pass
            if self.should_cancel is not None and self.should_cancel():
                from backend.app.services.ingestion.cancellation import IngestionCancelled
                raise IngestionCancelled(self.document_id)
            structure = extract_slide_structure(slide, self._media_md5, self._overlay_class)
            slide_text_chunks = []
            title = None
            speaker_notes = ""
            image_paths = []
            audio_items = []
            video_items = []
            all_media_items = []
            seen_media_names: Set[str] = set()
            seen_img_keys: Set[Tuple[int, int]] = set()   # content identity (size + CRC) of pictures already listed

            # Extract title if present
            if slide.shapes.title and slide.shapes.title.has_text_frame:
                title = clean_pptx_text(slide.shapes.title.text_frame.text)

            # Extract speaker notes
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                speaker_notes = clean_pptx_text(slide.notes_slide.notes_text_frame.text)

            # 1. Iterate shapes for text, tables, and PICTURE shapes (recursively traversing GROUP shapes)
            has_star_shape = False
            has_timer = False

            def _walk_shapes_recursive(shapes_collection):
                for s in shapes_collection:
                    try:
                        s_type = s.shape_type
                    except NotImplementedError:      # python-pptx cannot name some shape kinds (raises, not None)
                        s_type = None
                    if s_type == MSO_SHAPE_TYPE.GROUP or hasattr(s, "shapes"):
                        try:
                            yield from _walk_shapes_recursive(s.shapes)
                        except Exception:
                            pass
                    else:
                        yield s

            for s in slide.shapes:
                s_name = (getattr(s, "name", "") or "").lower()
                if "timer" in s_name or "1min" in s_name or "countdown" in s_name:
                    has_timer = True
                    break

            for shape in _walk_shapes_recursive(slide.shapes):
                try:
                    st = getattr(shape, "auto_shape_type", None) if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE else None
                    if (st is not None and "STAR" in str(st).upper()) or "star" in (shape.name or "").lower():
                        has_star_shape = True
                except Exception:
                    pass
                if shape.has_text_frame:
                    shape_text = clean_pptx_text(shape.text_frame.text)
                    if shape_text:
                        slide_text_chunks.append(shape_text)

                elif shape.has_table:
                    table_rows = []
                    for row in shape.table.rows:
                        row_cells = [clean_pptx_text(cell.text) for cell in row.cells]
                        table_rows.append(" | ".join(row_cells))
                    if table_rows:
                        slide_text_chunks.append("\n".join(table_rows))

                elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        rId = shape._element.blip_rId
                        if rId is None:
                            raise ValueError("no embedded image")
                        target_part = slide.part.related_part(rId)
                        zip_name = str(target_part.partname).lstrip("/")
                        info = self._entry_info(zip_name)
                        if info is None:
                            raise KeyError(f"{zip_name} is not in the deck")
                        header = self._image_header(zip_name)
                        if header is None or header[0] not in _PIL_FORMAT_EXT:
                            raise ValueError(f"unsupported image format in {zip_name}")
                        ext = f".{_PIL_FORMAT_EXT[header[0]]}"
                        dims = header[1]

                        # Validate that this is a genuine quiz image (not a GIF timer, bullet icon, or spacer)
                        if not self._is_valid_quiz_image(zip_name, ext, dims):
                            continue

                        content_key = (info.file_size, info.CRC)
                        if content_key in seen_img_keys:
                            continue
                        seen_img_keys.add(content_key)

                        part_name = Path(zip_name).name
                        wanted = part_name if Path(part_name).suffix.lower() in IMAGE_EXTS else f"{Path(part_name).stem}{ext}"
                        img_filename, rel_path, img_path = self._store_media(zip_name, wanted)

                        image_paths.append(rel_path)
                        seen_media_names.add(part_name)
                        seen_media_names.add(img_filename)

                        all_media_items.append(self._media_item(
                            img_filename, slide_idx, "image",
                            getattr(target_part, "content_type", None) or f"image/{ext.lstrip('.')}",
                            img_path, rel_path))
                    except Exception as e:
                        if "no embedded image" in str(e).lower():
                            logger.debug(f"Poster graphic shape on slide {slide_idx}: {e}")
                        else:
                            logger.warning(f"Could not extract picture shape from slide {slide_idx}: {e}")

            # 2. Extract embedded audio & video from slide.part.rels
            try:
                for rel_id, rel in slide.part.rels.items():
                    if getattr(rel, "is_external", False):
                        continue
                    rel_type = (rel.reltype or "").lower()
                    target_ref = getattr(rel, "target_ref", "") or ""
                    ext = Path(target_ref).suffix.lower()

                    is_media = (
                        "video" in rel_type or
                        "audio" in rel_type or
                        "media" in rel_type or
                        ext in AUDIO_EXTS or
                        ext in VIDEO_EXTS or
                        ext in IMAGE_EXTS
                    )

                    if is_media and not getattr(rel, "is_external", False):
                        try:
                            if not hasattr(rel, "target_part"):
                                continue
                            target_part = rel.target_part
                            zip_name = str(target_part.partname).lstrip("/")
                            info = self._entry_info(zip_name)
                        except Exception:
                            continue
                        if info is not None and info.file_size > 0:
                            part_name = Path(zip_name).name
                            if part_name not in seen_media_names:
                                seen_media_names.add(part_name)
                                media_type = self._classify_media_type(ext)
                                is_timer_vid = has_timer or any(k in part_name.lower() for k in ["timer", "1min", "countdown"])
                                if is_timer_vid and media_type == "video":
                                    has_timer = True
                                    continue
                                if media_type == "image" and not self._is_valid_quiz_image(zip_name, ext):
                                    continue

                                mime_type = getattr(target_part, "content_type", None) or mimetypes.guess_type(part_name)[0] or "application/octet-stream"
                                stored_name, rel_url, local_file = self._store_media(zip_name, part_name)

                                media_dict = self._media_item(stored_name, slide_idx, media_type, mime_type, local_file, rel_url)
                                all_media_items.append(media_dict)
                                if media_type == "audio":
                                    audio_items.append(media_dict)
                                elif media_type == "video":
                                    video_items.append(media_dict)
                                elif media_type == "image":
                                    content_key = (info.file_size, info.CRC)
                                    if content_key not in seen_img_keys:
                                        seen_img_keys.add(content_key)
                                        image_paths.append(rel_url)
            except Exception as re_err:
                logger.warning(f"Error inspecting slide.part.rels on slide {slide_idx}: {re_err}")

            # 3. Incorporate any media found from the zipfile inspection for this slide
            #    (keyed by the slide's own file number, not its position in the deck; media nothing
            #    points at is filed under the first slide, as before).
            slide_file_no = slide_file_numbers[slide_idx - 1] if slide_idx - 1 < len(slide_file_numbers) else slide_idx
            z_items = list(zip_media.get(slide_file_no, [])) if slide_file_no is not None else []
            if slide_idx == 1:
                z_items.extend(unmapped_media)
            for z_item in z_items:
                if z_item["filename"] not in seen_media_names:
                    seen_media_names.add(z_item["filename"])
                    info = self._entry_info(z_item["zip_name"])
                    if info is None:
                        logger.warning(f"Could not read {z_item['zip_name']} from the deck")
                        continue
                    if z_item["media_type"] == "image" and not self._is_valid_quiz_image(z_item["zip_name"], Path(z_item["filename"]).suffix):
                        continue

                    try:
                        stored_name, rel_url, local_file = self._store_media(z_item["zip_name"], z_item["filename"])
                    except Exception as zerr:
                        logger.warning(f"Could not read {z_item['zip_name']} from the deck: {zerr}")
                        continue

                    item_record = self._media_item(stored_name, slide_idx, z_item["media_type"], z_item["mime_type"], local_file, rel_url)
                    all_media_items.append(item_record)
                    if z_item["media_type"] == "audio":
                        audio_items.append(item_record)
                    elif z_item["media_type"] == "video":
                        video_items.append(item_record)
                    elif z_item["media_type"] == "image":
                        content_key = (info.file_size, info.CRC)
                        if content_key not in seen_img_keys:
                            seen_img_keys.add(content_key)
                            image_paths.append(rel_url)

            # Deduplicate text chunks while preserving order
            unique_chunks = []
            seen = set()
            for chunk in slide_text_chunks:
                norm = chunk.strip()
                if norm and norm not in seen:
                    unique_chunks.append(norm)
                    seen.add(norm)

            combined_text = "\n\n".join(unique_chunks)

            audio_paths = [a["rel_url"] for a in audio_items if a.get("rel_url")]
            video_paths = [v["rel_url"] for v in video_items if v.get("rel_url")]

            slides_data.append({
                "slide_number": slide_idx,
                "title": title or (unique_chunks[0] if unique_chunks else f"Slide {slide_idx}"),
                "extracted_text": combined_text,
                "speaker_notes": speaker_notes,
                "image_paths": image_paths,
                "has_images": len(image_paths) > 0,
                "has_timer": has_timer,
                "timer_seconds": 60 if has_timer else None,
                "audio_paths": audio_paths,
                "has_audio": len(audio_paths) > 0,
                "video_paths": video_paths,
                "has_video": len(video_paths) > 0,
                "audio_items": audio_items,
                "video_items": video_items,
                "all_media_items": all_media_items,
                "hidden": structure["hidden"],
                "layout_name": structure["layout_name"],
                "shapes": structure["shapes"],
                "animations": structure["animations"],
                "interactive_animations": structure["interactive_animations"],
                "metadata_json": {
                    "shape_count": len(slide.shapes),
                    "has_notes": bool(speaker_notes),
                    "audio_count": len(audio_items),
                    "video_count": len(video_items),
                    "image_count": len(image_paths),
                    "has_star_shape": has_star_shape,
                }
            })

        logger.info(
            f"PPTX {self.document_id}: {total_slides} slides, stored {self._stats['stored']} media files "
            f"({self._stats['stored_bytes'] / 1e6:.0f} MB), reused {self._stats['reused']} duplicates, "
            f"skipped {self._stats['template_skipped']} template-only files"
        )
        return slides_data

    def extract_all_media(self) -> List[Dict[str, Any]]:
        """
        Parses presentation and aggregates all multi-modal media objects across slides.
        """
        slides = self.parse()
        all_media = []
        for s in slides:
            all_media.extend(s.get("all_media_items", []))
        return all_media
