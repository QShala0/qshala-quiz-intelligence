"""Pure classification module for SharePoint unimported file relevance.

Classifies files into three buckets:
- 'excluded': clearly not a quiz deck (PDF when toggle off, temp file, < 30KB, exclude word/phrase, or hidden folder)
- 'likely': probable quiz deck (quiz keywords, year pattern + quiz keyword, folder segment 'content', or folder affinity)
- 'other': unimported .pptx (or .pdf if toggle on) without exclude triggers and without quiz signals
"""

import os
import re
from typing import Optional, Set, List, Dict, Any, Tuple

# Bucket constants
BUCKET_EXCLUDED = "excluded"
BUCKET_LIKELY = "likely"
BUCKET_OTHER = "other"


# Default exclude words / phrases (cbse and ncert are handled separately by targeted rules)
DEFAULT_EXCLUDE_WORDS = (
    "books", "textbooks", "marketing", "collateral", "collaterals",
    "flyer", "flyers", "brochure", "invoice", "invoices", "quote", "quotes", "quotation",
    "vendor", "vendors", "agreement", "contract", "proposal", "photos", "photo",
    "images", "videos", "logo", "logos", "certificate", "certificates", "poster",
    "posters", "branding", "social media", "script", "scripts", "working doc",
    "backup", "archive", "old versions", "template", "templates"
)

# Quiz words / signals
DEFAULT_QUIZ_WORDS = (
    "quiz", "quizzes", "round", "prelim", "prelims", "final", "finals", "semi",
    "semifinal", "regional", "regionals", "set", "questions", "reserve",
    "potpourri", "connect", "ticker", "three clues", "buzzer", "hotspot",
    "zvq", "qqq", "qql", "agastya", "varsity", "qh"
)


def get_exclude_rules(extra_env_words: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """Return (multi_word_phrases, single_word_prefixes) for exclusion."""
    raw_extra = extra_env_words if extra_env_words is not None else os.environ.get("SHAREPOINT_EXCLUDE_WORDS", "")
    all_terms = list(DEFAULT_EXCLUDE_WORDS)
    if raw_extra:
        for t in raw_extra.split(","):
            cleaned = t.strip().lower()
            if cleaned and cleaned not in all_terms:
                all_terms.append(cleaned)

    phrases = []
    single_words = []
    for term in all_terms:
        term = term.strip().lower()
        if not term:
            continue
        if " " in term or "_" in term or "-" in term:
            # Normalize internal separators for phrase matching
            phrases.append(re.sub(r'[\s_\-]+', ' ', term))
        else:
            single_words.append(term)

    return phrases, single_words


def _tokenize(text: str) -> List[str]:
    """Extract lowercased alphanumeric word tokens."""
    return [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', text)]


def contains_exclude_word(
    file_name: str,
    folder_path: str,
    phrases: List[str],
    single_words: List[str],
) -> bool:
    """Check if file name or folder path contains an exclude phrase or word-prefix."""
    full_text = f"{folder_path}/{file_name}".lower()
    norm_text = re.sub(r'[\s_\-]+', ' ', full_text)

    # 1. Multi-word phrase check (substring match in normalized text)
    for phrase in phrases:
        if phrase in norm_text:
            return True

    # 2. Single word / prefix check on word tokens
    tokens = _tokenize(full_text)
    for token in tokens:
        for word in single_words:
            if token.startswith(word):
                return True

    return False


def has_quiz_signal(file_name: str, folder_path: str) -> bool:
    """Check if file name or folder path exhibits quiz signals."""
    fn_lower = file_name.lower()
    fp_lower = folder_path.lower()
    combined = f"{fp_lower}/{fn_lower}"
    norm_combined = re.sub(r'[\s_\-]+', ' ', combined)

    # 1. Multi-word quiz phrases (e.g. 'three clues')
    if "three clues" in norm_combined:
        return True

    # 2. Folder name segment 'content' (e.g. folder named 'content' or '00_qshala content folders')
    folder_segments = [s.strip().lower() for s in folder_path.split("/") if s.strip()]
    for seg in folder_segments:
        seg_tokens = _tokenize(seg)
        if "content" in seg_tokens:
            return True

    # 3. Token-based quiz words
    fn_tokens = _tokenize(fn_lower)
    all_tokens = _tokenize(combined)

    single_quiz_words = [
        "quiz", "quizzes", "round", "prelim", "prelims", "final", "finals", "semi",
        "semifinal", "regional", "regionals", "set", "questions", "reserve",
        "potpourri", "connect", "ticker", "buzzer", "hotspot", "zvq", "qqq",
        "qql", "agastya", "varsity", "qh"
    ]

    has_token_signal = False
    for token in all_tokens:
        for qw in single_quiz_words:
            if token.startswith(qw) or token == qw:
                has_token_signal = True
                break
        if has_token_signal:
            break

    if has_token_signal:
        return True

    # 4. Year pattern in file name together with another quiz word
    # Year pattern: 4 digits like 2019-2099 or 2 digits like '24, '25, '26
    has_year = bool(re.search(r'\b(19\d\d|20\d\d)\b', fn_lower) or re.search(r'\b\d{2}[-_]\d{2}\b', fn_lower))
    if has_year:
        # Check if any quiz word token appears anywhere
        for token in all_tokens:
            for qw in single_quiz_words:
                if token.startswith(qw) or token == qw:
                    return True

    return False


def is_file_temp_or_lock(file_name: str) -> bool:
    """Return True if file is temporary or lock file."""
    fn = file_name.strip()
    fn_lower = fn.lower()
    if fn.startswith("~$") or fn.startswith(".~"):
        return True
    if fn_lower.endswith(".tmp") or fn_lower.endswith(".crdownload"):
        return True
    return False


CBSE_NCERT_EXEMPT_QUIZ_WORDS = ("quiz", "prelim", "final", "round", "questions", "reserve")


def is_textbook_or_curriculum_folder(folder_path: str) -> bool:
    """Check if any folder in path is a textbook or curriculum folder:
    - CBSE Books, Textbooks, Textbook, NCERT Books (whole-word and case-insensitive)
    - Discover - Curriculum / Discover Curriculum followed (anywhere below it) by a CBSE or NCERT folder
    """
    if not folder_path:
        return False

    segments = [s.strip() for s in folder_path.split("/") if s.strip()]
    if not segments:
        return False

    for i, seg in enumerate(segments):
        norm_seg = re.sub(r'[\s_\-]+', ' ', seg.lower()).strip()
        # Direct textbook folders matched whole-word
        if re.search(r'\bcbse\s+books\b', norm_seg):
            return True
        if re.search(r'\bncert\s+books\b', norm_seg):
            return True
        if re.search(r'\btextbooks?\b', norm_seg):
            return True

        # Discover - Curriculum / Discover Curriculum followed anywhere below it by CBSE or NCERT folder
        if "discover curriculum" in norm_seg:
            for below_seg in segments[i + 1:]:
                norm_below = re.sub(r'[\s_\-]+', ' ', below_seg.lower()).strip()
                if re.search(r'\b(cbse|ncert)\b', norm_below):
                    return True

    return False


def is_non_quiz_cbse_or_ncert_filename(file_name: str) -> bool:
    """Check if file name contains cbse or ncert and none of the quiz words
    (quiz, prelim, final, round, questions, reserve) appear in the file name.
    """
    fn_lower = file_name.lower()
    tokens = _tokenize(fn_lower)

    # Must contain cbse or ncert as a token or prefix
    has_cbse_or_ncert = any(t == "cbse" or t == "ncert" or t.startswith("cbse") or t.startswith("ncert") for t in tokens)
    if not has_cbse_or_ncert:
        return False

    # Exempt if any quiz word appears in file name
    has_quiz_word = any(
        any(t.startswith(qw) or t == qw for qw in CBSE_NCERT_EXEMPT_QUIZ_WORDS)
        for t in tokens
    )
    return not has_quiz_word


def is_cbse_ncert_excluded(file_name: str, folder_path: str) -> bool:
    """Classify as excluded if folder is a textbook/curriculum folder or file name is non-quiz cbse/ncert."""
    return is_textbook_or_curriculum_folder(folder_path) or is_non_quiz_cbse_or_ncert_filename(file_name)


def classify_file(
    name: str,
    folder_path: str = "",
    size: Optional[int] = None,
    ext: Optional[str] = None,
    folder_id: Optional[str] = None,
    include_pdf: bool = False,
    hidden_folder_ids: Optional[Set[str]] = None,
    ancestor_folder_ids: Optional[List[str]] = None,
    folders_with_affinity: Optional[Set[str]] = None,
    folders_with_ingested_decks: Optional[Set[str]] = None,
    exclude_phrases: Optional[List[str]] = None,
    exclude_single_words: Optional[List[str]] = None,
) -> str:
    """Classify a single unimported candidate file into 'excluded', 'likely', or 'other'.

    Args:
        name: Name of file.
        folder_path: Full rebuilt folder path (e.g. 'root/Quizzes/Round1').
        size: Size in bytes.
        ext: Extension (e.g. '.pptx', '.pdf').
        folder_id: Parent folder ID of the file.
        include_pdf: Whether PDFs are allowed (if False, any non-pptx is excluded).
        hidden_folder_ids: Set of hidden folder IDs.
        ancestor_folder_ids: List of folder IDs from immediate parent up to root.
        folders_with_affinity: Set of folder IDs that have ingested decks within 2 levels.
        folders_with_ingested_decks: Alias for folders_with_affinity.
        exclude_phrases: Precomputed multi-word exclude phrases.
        exclude_single_words: Precomputed single-word exclude prefixes.

    Returns:
        'excluded', 'likely', or 'other'.
    """
    if exclude_phrases is None or exclude_single_words is None:
        exclude_phrases, exclude_single_words = get_exclude_rules()

    if hidden_folder_ids is None:
        hidden_folder_ids = set()
    if ancestor_folder_ids is None:
        ancestor_folder_ids = []
    effective_affinity = folders_with_affinity or folders_with_ingested_decks or set()

    effective_ext = (ext or "").lower()
    if not effective_ext.startswith(".") and effective_ext:
        effective_ext = f".{effective_ext}"

    # 1. Hidden folder check: if parent or any ancestor folder is hidden, file is excluded
    if folder_id and folder_id in hidden_folder_ids:
        return "excluded"
    if any(aid in hidden_folder_ids for aid in ancestor_folder_ids):
        return "excluded"

    # 2. Extension check: unless include_pdf is True, non-.pptx is excluded
    if not include_pdf and effective_ext != ".pptx":
        return "excluded"

    # 3. Temp / lock file check
    if is_file_temp_or_lock(name):
        return "excluded"

    # 4. Size check (< 30 KB is excluded)
    if size is not None and size < 30 * 1024:
        return "excluded"

    # 5. Exclude words / phrases check
    if contains_exclude_word(name, folder_path, exclude_phrases, exclude_single_words):
        return "excluded"

    # 6. Targeted CBSE / NCERT textbook & curriculum check
    if is_cbse_ncert_excluded(name, folder_path):
        return "excluded"

    # Check for likely signals:
    # A. Quiz word / keyword signal
    if has_quiz_signal(name, folder_path):
        return "likely"

    # B. Folder affinity signal (folder or up to 2 levels above holds an ingested deck)
    if folder_id and folder_id in effective_affinity:
        return "likely"
    # Check parent and grandparent
    if len(ancestor_folder_ids) >= 1 and ancestor_folder_ids[0] in effective_affinity:
        return "likely"
    if len(ancestor_folder_ids) >= 2 and ancestor_folder_ids[1] in effective_affinity:
        return "likely"

    return "other"
