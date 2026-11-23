import re
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from backend.app.models.document import Document


class DeckEntry:
    __slots__ = ("id", "title", "filename", "sharepoint_file_id", "processing_status")

    def __init__(
        self,
        id: Optional[str],
        title: str,
        filename: Optional[str] = None,
        sharepoint_file_id: Optional[str] = None,
        processing_status: Optional[str] = None,
    ):
        self.id = id
        self.title = title
        self.filename = filename
        self.sharepoint_file_id = sharepoint_file_id
        self.processing_status = processing_status

    def __iter__(self):
        yield self.id
        yield self.title

    def __getitem__(self, idx):
        if idx == 0:
            return self.id
        if idx == 1:
            return self.title
        raise IndexError("DeckEntry index out of range")

    def __repr__(self):
        return f"DeckEntry(id={self.id!r}, title={self.title!r})"


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


def existing_deck_names(db: Session) -> Dict[str, DeckEntry]:
    """Return an in-memory index {normalised_name: DeckEntry} for all active decks.

    Executes a single query selecting only the necessary columns (Document.id,
    Document.filename, Document.title, Document.processing_status, plus
    sharepoint_file_id and uploaded_at for ordering).
    Skips FAILED and CANCELLED documents so they can be re-uploaded.
    Keeps the newest document when two share a normalised name.
    """
    rows = (
        db.query(
            Document.id,
            Document.filename,
            Document.title,
            Document.processing_status,
            Document.sharepoint_file_id,
            Document.uploaded_at,
        )
        .filter(Document.processing_status.notin_(["FAILED", "CANCELLED"]))
        .order_by(Document.uploaded_at.desc(), Document.id.desc())
        .all()
    )
    index: Dict[str, DeckEntry] = {}
    for r in rows:
        norm = normalize_deck_name(r.filename)
        if norm and norm not in index:
            index[norm] = DeckEntry(
                id=r.id,
                title=r.title or r.filename,
                filename=r.filename,
                sharepoint_file_id=r.sharepoint_file_id,
                processing_status=r.processing_status,
            )
    return index


def find_deck_in_index(index: Dict[str, Any], filename: Optional[str]) -> Optional[DeckEntry]:
    """Look up a deck by filename in the existing_deck_names index."""
    if not filename or not index:
        return None
    norm = normalize_deck_name(filename)
    if not norm:
        return None
    return index.get(norm)


def find_deck_by_name(db: Session, filename: Optional[str]) -> Optional[DeckEntry]:
    """Find an existing Document with the same normalised filename.

    Thin wrapper around existing_deck_names and find_deck_in_index for
    single-file upload endpoints.
    """
    if not filename:
        return None
    index = existing_deck_names(db)
    return find_deck_in_index(index, filename)
