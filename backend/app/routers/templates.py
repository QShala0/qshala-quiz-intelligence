import os
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models.custom_template import CustomPptTemplate
from backend.app.services.export.template_extractor import TemplateExtractor

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
        ),
        "file_size_bytes": tpl.file_size_bytes,
        "layout_metadata": tpl.layout_metadata or {},
        "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
        "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
    }


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
    """
    clean_name = (name or "").strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Template name is required")

    clean_category = (category or "qshala_school").strip().lower()
    if clean_category not in ("qshala_school", "zvq"):
        raise HTTPException(status_code=400, detail="Category must be either 'qshala_school' or 'zvq'")

    if not file.filename or not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only PowerPoint (.pptx) files are supported")

    content = await file.read()
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="Uploaded file is empty or corrupted")

    try:
        extractor = TemplateExtractor(content)
        data = extractor.extract(
            name=clean_name,
            category=clean_category,
            description=description,
        )
    except Exception as e:
        logger.exception("Failed to extract template from PPTX")
        raise HTTPException(status_code=422, detail=f"Could not extract template from presentation: {str(e)}")

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

    return _template_response(tpl)


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

    # Clean up storage directory
    if tpl.storage_path:
        storage_dir = Path(tpl.storage_path).parent
        if storage_dir.exists() and storage_dir.is_dir():
            try:
                shutil.rmtree(storage_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to remove template directory {storage_dir}: {e}")

    db.delete(tpl)
    db.commit()
    return {"message": "Template deleted successfully", "id": template_id}


@router.get("/{template_id}/preview")
def get_template_preview(
    template_id: str,
    db: Session = Depends(get_db),
):
    """Serves the preview thumbnail image for the template."""
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    if tpl.preview_image_path and os.path.exists(tpl.preview_image_path):
        return FileResponse(tpl.preview_image_path, media_type="image/png")

    # Check for cover_bg in storage directory as fallback
    if tpl.storage_path:
        storage_dir = Path(tpl.storage_path).parent
        for candidate in ["cover_bg.png", "cover_bg.jpg", "cover_bg.jpeg"]:
            p = storage_dir / candidate
            if p.exists():
                return FileResponse(str(p), media_type="image/png" if candidate.endswith(".png") else "image/jpeg")

    raise HTTPException(status_code=404, detail="Preview image not found")


@router.get("/{template_id}/download")
def download_template_master(
    template_id: str,
    db: Session = Depends(get_db),
):
    """Downloads the clean master presentation (.pptx) file."""
    tpl = db.query(CustomPptTemplate).filter(CustomPptTemplate.id == template_id).first()
    if not tpl or not tpl.storage_path or not os.path.exists(tpl.storage_path):
        raise HTTPException(status_code=404, detail="Template master presentation file not found")

    safe_name = "".join(c for c in tpl.name if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
    filename = f"{safe_name or 'template'}_master.pptx"
    return FileResponse(
        tpl.storage_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )
