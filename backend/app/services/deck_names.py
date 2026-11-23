import re
from typing import Optional
from sqlalchemy.orm import Session
from backend.app.models.document import Document


def normalize_deck_name(filename: Optional[str]) -> str:
    """Normalize a presentation filename for duplicate detection.

    Lowercases, drops known extensions (.pptx, .ppt, .pdf), turns underscores
    and repeated spaces into single spaces, and trims. Two names are the same deck
    when their normalised names are equal.
    """
    if not filename:
        return ""
    name = filename.strip().lower()
    for ext in (".pptx", ".ppt", ".pdf"):
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    name = re.sub(r"[_\s]+", " ", name).strip()
    return name


def find_deck_by_name(db: Session, filename: Optional[str]) -> Optional[Document]:
    """Find an existing Document with the same normalised filename.

    Returns the existing Document whose processing_status is not FAILED or CANCELLED,
    allowing failed or cancelled uploads to be retried.
    """
    target = normalize_deck_name(filename)
    if not target:
        return None

    docs = (
        db.query(Document)
        .filter(Document.processing_status.notin_(["FAILED", "CANCELLED"]))
        .order_by(Document.uploaded_at.desc() if hasattr(Document, "uploaded_at") else Document.id)
        .all()
    )
    for doc in docs:
        if normalize_deck_name(doc.filename) == target:
            return doc
    return None
