import os
import re
import shutil
import tempfile
import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models.custom_template import CustomPptTemplate
from backend.app.services.export.template_extractor import TemplateExtractor
from backend.app.services.export import template_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["Custom PPT Templates"])


def _template_response(tpl: CustomPptTemplate) -> Dict[str, Any]:
    return {
        "id": tpl.id,
        "name": tpl.name,
        "category": tpl.category,
        "description": tpl.description,
        "aspect_ratio": tpl.aspect_ratio,
        "slide_width_in": tpl.slide_width_in,
        "slide_height_in": tpl.slide_height_in,
        "title_font": tpl.title_font,
        "title_size_pt": tpl.title_size_pt,
        "title_color_hex": tpl.title_color_hex,
        "body_font": tpl.body_font,
        "body_size_pt": tpl.body_size_pt,
        "body_color_hex": tpl.body_color_hex,
        "header_font": tpl.header_font,
        "header_size_pt": tpl.header_size_pt,
        "header_color_hex": tpl.header_color_hex,
        "has_preview": bool(
            (tpl.preview_image_path and os.path.exists(tpl.preview_image_path))
            or (tpl.storage_path and os.path.exists(os.path.join(os.path.dirname(tpl.storage_path), "cover_bg.png")))
            or (tpl.storage_path and os.path.exists(os.path.join(os.path.dirname(tpl.storage_path), "preview.png")))
            or "preview.png" in template_store.stored_files(tpl)
        ),
        # False = the template's files are gone (server restarted before they were saved to durable storage):
        # the UI should ask for the sample deck to be uploaded again instead of exporting a wrong deck.
        "files_ok": template_store.files_ok(tpl),
        "file_size_bytes": tpl.file_size_bytes,
        "layout_metadata": tpl.layout_metadata or {},
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
    }


def _finalize_extracted(data: Dict[str, Any]) -> Dict[str, Any]:
    """Copy the extracted files to durable storage and record which files exist. Runs in a worker thread."""
    folder = Path(data["storage_path"]).parent
    names = template_store.upload_template_dir(data["id"], folder)
    meta = dict(data.get("layout_metadata") or {})
    meta["stored_files"] = names
    data["layout_metadata"] = meta
    return data


def _extract_and_store(source, name: str, category: str, description: Optional[str]) -> Dict[str, Any]:
    data = TemplateExtractor(source).extract(name=name, category=category, description=description)
    return _finalize_extracted(data)


def _persist_extracted_template(data: Dict[str, Any], db: Session) -> CustomPptTemplate:
    tpl = CustomPptTemplate(
        id=data["id"],
        name=data["name"],
        category=data["category"],
        description=data["description"],
        storage_path=data["storage_path"],
        file_size_bytes=data["file_size_bytes"],
        preview_image_path=data["preview_image_path"],
        aspect_ratio=data["aspect_ratio"],
        slide_width_in=data["slide_width_in"],
        slide_height_in=data["slide_height_in"],
        title_font=data["title_font"],
        title_size_pt=data["title_size_pt"],
        title_color_hex=data["title_color_hex"],
        body_font=data["body_font"],
        body_size_pt=data["body_size_pt"],
        body_color_hex=data["body_color_hex"],
        header_font=data["header_font"],
        header_size_pt=data["header_size_pt"],
        header_color_hex=data["header_color_hex"],
        layout_metadata=data["layout_metadata"],
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.post("/extract")
async def extract_template(
    file: UploadFile = File(...),
    name: str = Form(...),
    category: str = Form("qshala_school"),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Extracts a custom PowerPoint template from an uploaded sample .pptx deck,
    saves the master presentation and typography, and persists it under QShala School or ZVQ.
    Guards against large direct uploads (>32 MB) and directs clients to chunked upload.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Template name is required")

    clean_category = (category or "qshala_school").strip().lower()
    if clean_category not in ("qshala_school", "zvq"):
        raise HTTPException(status_code=400, detail="Category must be either 'qshala_school' or 'zvq'")

    if not file.filename or not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only PowerPoint (.pptx) files are supported")

    MAX_DIRECT_BYTES = 32 * 1024 * 1024
    content = bytearray()
    while chunk_bytes := await file.read(64 * 1024):
        content.extend(chunk_bytes)
        if len(content) > MAX_DIRECT_BYTES:
            raise HTTPException(
                status_code=413,
                detail="File exceeds maximum direct upload size (32 MB). Use chunked upload for large presentation decks."
            )

    if len(content) < 100:
        raise HTTPException(status_code=400, detail="Uploaded file is empty or corrupted")

    try:
        # Heavy PPTX parsing + upload: run in a worker thread so the single Cloud Run instance keeps serving other requests.
        data = await asyncio.to_thread(_extract_and_store, bytes(content), clean_name, clean_category, description)
    except Exception as e:
        logger.exception("Failed to extract template from PPTX")
        raise HTTPException(status_code=422, detail=f"Could not extract template from presentation: {str(e)}")

    tpl = _persist_extracted_template(data, db)
    return _template_response(tpl)


@router.post("/extract-chunk")
async def extract_template_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    name: str = Form(...),
    category: str = Form("qshala_school"),
    description: Optional[str] = Form(None),
    filename: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Receives sliced chunks of a large presentation deck (>32 MB or >4.5 MB),
    assembles them once all slices arrive, extracts the custom template, and persists it.
    Bypasses Vercel and Cloud Run single-request body size limits.
    """
    clean_upload_id = re.sub(r"[^a-zA-Z0-9_\-]", "", upload_id)
    if not clean_upload_id:
        raise HTTPException(status_code=400, detail="Invalid upload_id")

    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Template name is required")

    clean_category = (category or "qshala_school").strip().lower()
    if clean_category not in ("qshala_school", "zvq"):
        raise HTTPException(status_code=400, detail="Category must be either 'qshala_school' or 'zvq'")

    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        raise HTTPException(status_code=400, detail="Invalid chunk_index or total_chunks")

    chunk_dir = Path(tempfile.gettempdir()) / f"tpl_chunk_{clean_upload_id}"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    part_file = chunk_dir / f"chunk_{chunk_index:05d}.part"
    with open(part_file, "wb") as f:
        while chunk_data := await chunk.read(64 * 1024):
            f.write(chunk_data)

    parts = sorted(list(chunk_dir.glob("chunk_*.part")))
    if len(parts) < total_chunks:
        return {
            "status": "UPLOADING",
            "chunk_index": chunk_index,
            "received_chunks": len(parts),
            "total_chunks": total_chunks,
        }

    # All chunks arrived: stitch into full presentation deck off the event loop
    stitched_path = chunk_dir / "deck.pptx"
    try:
        def _stitch_and_extract():
            with open(stitched_path, "wb") as outfile:
                for p in parts:
                    with open(p, "rb") as infile:
                        shutil.copyfileobj(infile, outfile)

            return _extract_and_store(stitched_path, clean_name, clean_category, description)

        data = await asyncio.to_thread(_stitch_and_extract)
        tpl = _persist_extracted_template(data, db)
    except Exception as e:
        logger.exception(f"Failed to extract template from chunked upload {clean_upload_id}: {e}")
        raise HTTPException(status_code=422, detail=f"Could not extract template from presentation: {str(e)}")
    finally:
        try:
            shutil.rmtree(chunk_dir, ignore_errors=True)
        except Exception:
            pass

    return {
        "status": "COMPLETED",
        "template": _template_response(tpl),
    }


@router.delete("/extract-chunk/{upload_id}")
def cancel_extract_chunk(upload_id: str):
    """Cleans up temporary chunks if a user cancels or an upload fails."""
    clean_upload_id = re.sub(r"[^a-zA-Z0-9_\-]", "", upload_id)
    if clean_upload_id:
        chunk_dir = Path(tempfile.gettempdir()) / f"tpl_chunk_{clean_upload_id}"
        if chunk_dir.exists():
            shutil.rmtree(chunk_dir, ignore_errors=True)
    return {"status": "CANCELLED", "upload_id": upload_id}


@router.get("")
def list_templates(
    category: Optional[str] = Query(None, description="Filter by category: 'qshala_school' or 'zvq'"),
    db: Session = Depends(get_db),
):
    """Lists saved custom templates, optionally filtered by category."""
    query = db.query(CustomPptTemplate).filter(CustomPptTemplate.is_active == True)
    if category:
        clean_cat = category.strip().lower()
        query = query.filter(CustomPptTemplate.category == clean_cat)
    templates = query.order_by(CustomPptTemplate.created_at.desc()).all()
    return [_template_response(t) for t in templates]


@router.get("/{template_id}")
def get_template(
    template_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves full details of a specific custom template."""
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id, CustomPptTemplate.is_active == True).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    resp = _template_response(tpl)
    resp["layout_metadata"] = tpl.layout_metadata
    return resp


@router.delete("/{template_id}")
def delete_template(
    template_id: str,
    db: Session = Depends(get_db),
):
    """Deletes a custom template and cleans up stored files."""
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    template_store.delete_template_files(tpl)

    db.delete(tpl)
    db.commit()
    return {"message": "Template deleted successfully", "id": template_id}


@router.get("/{template_id}/preview")
def get_template_preview(
    template_id: str,
    slide: Optional[str] = Query("cover", description="Slide role to preview: 'cover', 'question', or 'answer'"),
    db: Session = Depends(get_db),
):
    """Serves the preview background or thumbnail image for the template."""
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    storage_dir = template_store.ensure_local_dir(tpl)  # restores from durable storage after a restart
    role = (slide or "cover").strip().lower()
    role_key = "divider" if role in ("divider", "section", "intro") else ("cover" if role in ("cover", "title") else ("question" if role in ("question", "q") else "answer"))

    cache_headers = {"Cache-Control": "public, max-age=86400"}

    if storage_dir and storage_dir.exists():
        # 1. Check for pre-generated web preview (fast, lightweight ~80-150 KB)
        web_file = storage_dir / f"web_{role_key}.jpg"
        if web_file.exists():
            return FileResponse(str(web_file), media_type="image/jpeg", headers=cache_headers)

        # 2. Look for role background and generate cached web thumbnail on the fly
        candidates = []
        if role in ("question", "q"):
            candidates = ["web_question.jpg", "question_bg.png", "question_bg.jpg", "question_bg.jpeg"]
        elif role in ("divider", "section", "intro"):
            candidates = ["web_divider.jpg", "answer_intro_bg.png", "answer_intro_bg.jpg", "answer_bg.png", "answer_bg.jpg"]
        elif role in ("answer", "a"):
            candidates = ["web_answer.jpg", "answer_bg.png", "answer_bg.jpg", "answer_intro_bg.png", "answer_intro_bg.jpg"]
        elif role in ("cover", "title"):
            candidates = ["web_cover.jpg", "cover_bg.png", "cover_bg.jpg", "cover_bg.jpeg"]

        for cand in candidates:
            p = storage_dir / cand
            if p.exists():
                if cand.startswith("web_") or cand.endswith(".jpg") or cand.endswith(".jpeg"):
                    return FileResponse(str(p), media_type="image/jpeg", headers=cache_headers)
                try:
                    from PIL import Image
                    with Image.open(p) as im:
                        im.thumbnail((1280, 720), Image.Resampling.LANCZOS)
                        im.convert("RGB").save(str(web_file), "JPEG", quality=85)
                    return FileResponse(str(web_file), media_type="image/jpeg", headers=cache_headers)
                except Exception:
                    return FileResponse(str(p), media_type="image/png", headers=cache_headers)

    # Fallback to preview_image_path or any cover_bg
    if tpl.preview_image_path and os.path.exists(tpl.preview_image_path):
        return FileResponse(tpl.preview_image_path, media_type="image/png", headers=cache_headers)

    if storage_dir and storage_dir.exists():
        for cand in ["preview.png", "cover_bg.png", "cover_bg.jpg", "question_bg.png"]:
            p = storage_dir / cand
            if p.exists():
                return FileResponse(str(p), media_type="image/png" if cand.endswith(".png") else "image/jpeg", headers=cache_headers)

    # Dynamic fallback: generate a clean 16:9 theme canvas so preview never 404s
    try:
        from PIL import Image
        import io
        from starlette.responses import StreamingResponse
        color_scheme = (tpl.layout_metadata or {}).get("theme", {}).get("color_scheme", {})
        bg_hex = color_scheme.get("lt1", "#FFFFFF") if role not in ("answer", "a") else color_scheme.get("dk1", "#0F172A")
        if not bg_hex.startswith("#") or len(bg_hex) != 7:
            bg_hex = "#FFFFFF"
        r = int(bg_hex[1:3], 16)
        g = int(bg_hex[3:5], 16)
        b = int(bg_hex[5:7], 16)
        img = Image.new("RGB", (640, 360), (r, g, b))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png", headers=cache_headers)
    except Exception as e:
        logger.warning(f"Could not generate dynamic fallback preview: {e}")

    raise HTTPException(status_code=404, detail=f"Preview image for '{role}' not found")


@router.get("/{template_id}/download")
def download_template_master(
    template_id: str,
    db: Session = Depends(get_db),
):
    """Downloads the clean master presentation (.pptx) file."""
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    master = template_store.master_path(tpl)
    if master is None:
        raise HTTPException(status_code=409, detail="This template's files are no longer on the server. Upload the sample deck again to recreate it.")

    safe_name = "".join(c for c in tpl.name if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
    filename = f"{safe_name or 'template'}_master.pptx"
    return FileResponse(
        str(master),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )
