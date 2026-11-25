import logging
from pathlib import Path
from typing import List, Dict, Any
from backend.app.config import settings

logger = logging.getLogger(__name__)

class PDFParser:
    """
    Extracts structured pages, text, and metadata from PDF quiz decks.
    """
    def __init__(self, file_path: str, document_id: str):
        self.file_path = file_path
        self.document_id = document_id

    def parse(self) -> List[Dict[str, Any]]:
        slides_data = []

        # Attempt 1: pdfplumber (best for layout and rich extraction)
        try:
            import pdfplumber
            with pdfplumber.open(self.file_path) as pdf:
                for page_idx, page in enumerate(pdf.pages, start=1):
                    extracted_text = page.extract_text() or ""
                    lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                    title = lines[0] if lines else f"Page {page_idx}"

                    slides_data.append({
                        "slide_number": page_idx,
                        "title": title,
                        "extracted_text": extracted_text.strip(),
                        "speaker_notes": "",
                        "image_paths": [],
                        "has_images": len(page.images) > 0 if hasattr(page, "images") else False,
                        "metadata_json": {
                            "width": float(page.width),
                            "height": float(page.height),
                            "image_count": len(page.images) if hasattr(page, "images") else 0
                        }
                    })
            if slides_data:
                return slides_data
        except Exception as e:
            logger.warning(f"pdfplumber extraction failed ({e}), falling back to pure-Python pypdf")

        # Attempt 2: pypdf (pure Python, 100% reliable in AWS Lambda / Vercel)
        slides_data = []
        try:
            from pypdf import PdfReader
            reader = PdfReader(self.file_path)
            for page_idx, page in enumerate(reader.pages, start=1):
                extracted_text = page.extract_text() or ""
                lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                title = lines[0] if lines else f"Page {page_idx}"

                has_images = False
                try:
                    has_images = len(page.images) > 0
                except Exception:
                    pass

                slides_data.append({
                    "slide_number": page_idx,
                    "title": title,
                    "extracted_text": extracted_text.strip(),
                    "speaker_notes": "",
                    "image_paths": [],
                    "has_images": has_images,
                    "metadata_json": {}
                })
        except Exception as e:
            logger.error(f"pypdf extraction failed: {e}")

        return slides_data
