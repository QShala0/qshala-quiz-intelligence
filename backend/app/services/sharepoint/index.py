import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import time
from typing import Optional, Dict, Any, List

import httpx
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.sharepoint import SharePointAllowedFolder
from backend.app.models.sharepoint_index import (
    SharePointIndexItem, SharePointIndexState, SharePointHiddenFolder,
)
from backend.app.services.deck_names import existing_deck_names, find_deck_in_index
from backend.app.services.sharepoint.relevance import (
    classify_file, get_exclude_rules,
)

logger = logging.getLogger(__name__)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def index_step(
    db: Session,
    client,
    budget_seconds: int = 20,
    drive_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute one scan step against Microsoft Graph drive delta.

    Advances the delta pagination within budget_seconds, upserting folders and
    supported files (.pptx, .pdf), removing deleted items, and saving state after
    each page. Returns status, items_indexed, finished, and last_full_at.
    """
    target_drive = drive_id or getattr(client, "drive_id", None) or getattr(settings, "SHAREPOINT_DRIVE_ID", None)
    if not target_drive:
        try:
            drives = await client.list_drives()
            if drives:
                target_drive = drives[0]["id"]
        except Exception as e:
            logger.warning("Failed to list drives for SharePoint indexing: %s", e)

    if not target_drive:
        raise ValueError("SharePoint drive_id must be provided or configured in settings")

    state = db.query(SharePointIndexState).filter(SharePointIndexState.drive_id == target_drive).first()
    if not state:
        state = SharePointIndexState(
            drive_id=target_drive,
            status="IDLE",
            items_indexed=0,
        )
        db.add(state)
        db.commit()
        db.refresh(state)

    now = datetime.now(timezone.utc)
    lease_until = _as_utc(state.lease_until)
    if state.status == "RUNNING" and lease_until and lease_until > now:
        return {
            "status": "busy",
            "items_indexed": state.items_indexed or 0,
            "finished": False,
            "last_full_at": state.last_full_at.isoformat() if state.last_full_at else None,
        }

    # Acquire lease
    lease_seconds = max(budget_seconds * 2, 60)
    state.status = "RUNNING"
    state.lease_until = now + timedelta(seconds=lease_seconds)
    if not state.scan_started_at:
        state.scan_started_at = now
    state.last_step_at = now
    state.error_message = None
    db.commit()

    root_delta_url = f"{client.GRAPH_BASE}/drives/{target_drive}/root/delta"
    current_url: Optional[str] = state.delta_link or root_delta_url
    start_time = time.time()
    finished = False

    try:
        while current_url:
            if (time.time() - start_time) >= budget_seconds:
                state.delta_link = current_url
                state.status = "IDLE"
                state.lease_until = None
                state.last_step_at = datetime.now(timezone.utc)
                db.commit()
                return {
                    "status": "IDLE",
                    "items_indexed": state.items_indexed or 0,
                    "finished": False,
                    "last_full_at": state.last_full_at.isoformat() if state.last_full_at else None,
                }

            try:
                data = await client.fetch_delta_page(current_url)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 410:
                    logger.warning("Delta token expired (410 Gone). Resetting delta token and restarting full scan.")
                    state.delta_link = None
                    state.last_full_at = None
                    state.scan_started_at = datetime.now(timezone.utc)
                    db.commit()
                    current_url = root_delta_url
                    continue
                raise

            items = data.get("value", [])
            for it in items:
                item_id = it.get("id")
                if not item_id:
                    continue

                # Remove deleted facet
                if "@removed" in it or it.get("deleted"):
                    db.query(SharePointIndexItem).filter(
                        SharePointIndexItem.id == item_id,
                        SharePointIndexItem.drive_id == target_drive,
                    ).delete(synchronize_session=False)
                    continue

                is_folder = "folder" in it
                name = it.get("name") or ""
                ext = Path(name).suffix.lower() if not is_folder else ""

                if not is_folder and ext not in (".pptx", ".pdf"):
                    continue

                parent_ref = it.get("parentReference", {})
                parent_id = parent_ref.get("id")
                size = it.get("size")
                etag = it.get("eTag")
                mod_str = it.get("lastModifiedDateTime")
                mod_dt = None
                if mod_str:
                    try:
                        mod_dt = datetime.fromisoformat(mod_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                existing = db.query(SharePointIndexItem).filter(
                    SharePointIndexItem.id == item_id,
                    SharePointIndexItem.drive_id == target_drive,
                ).first()
                if existing:
                    existing.parent_id = parent_id
                    existing.name = name
                    existing.is_folder = is_folder
                    existing.size = size
                    existing.modified = mod_dt
                    existing.etag = etag
                    existing.ext = ext
                    existing.last_seen_at = datetime.now(timezone.utc)
                else:
                    new_item = SharePointIndexItem(
                        id=item_id,
                        drive_id=target_drive,
                        parent_id=parent_id,
                        name=name,
                        is_folder=is_folder,
                        size=size,
                        modified=mod_dt,
                        etag=etag,
                        ext=ext,
                        last_seen_at=datetime.now(timezone.utc),
                    )
                    db.add(new_item)

            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")

            if next_link:
                current_url = next_link
                state.delta_link = next_link
            elif delta_link:
                current_url = None
                state.delta_link = delta_link
                state.last_full_at = datetime.now(timezone.utc)
                finished = True
            else:
                current_url = None
                finished = True

            db.flush()
            state.items_indexed = db.query(SharePointIndexItem).filter(
                SharePointIndexItem.drive_id == target_drive
            ).count()
            state.last_step_at = datetime.now(timezone.utc)
            db.commit()

        state.status = "IDLE"
        state.lease_until = None
        db.commit()
        return {
            "status": "IDLE",
            "items_indexed": state.items_indexed or 0,
            "finished": finished,
            "last_full_at": state.last_full_at.isoformat() if state.last_full_at else None,
        }

    except Exception as e:
        state.status = "ERROR"
        state.error_message = str(e)
        state.lease_until = None
        db.commit()
        raise


def get_index_status(db: Session, drive_id: Optional[str] = None) -> Dict[str, Any]:
    """Return index state, counts and last updated times for the drive."""
    target_drive = drive_id or getattr(settings, "SHAREPOINT_DRIVE_ID", None)
    state = db.query(SharePointIndexState).filter(
        SharePointIndexState.drive_id == target_drive
    ).first() if target_drive else db.query(SharePointIndexState).first()

    if not state and target_drive:
        state = SharePointIndexState(
            drive_id=target_drive,
            status="IDLE",
            items_indexed=0,
        )

    eff_drive = state.drive_id if state else target_drive

    total_items = 0
    supported_files = 0
    folders_count = 0
    if eff_drive:
        total_items = db.query(SharePointIndexItem).filter(SharePointIndexItem.drive_id == eff_drive).count()
        supported_files = db.query(SharePointIndexItem).filter(
            SharePointIndexItem.drive_id == eff_drive,
            SharePointIndexItem.is_folder == False,
            SharePointIndexItem.ext.in_([".pptx", ".pdf", "pptx", "pdf"]),
        ).count()
        folders_count = db.query(SharePointIndexItem).filter(
            SharePointIndexItem.drive_id == eff_drive,
            SharePointIndexItem.is_folder == True,
        ).count()

    return {
        "drive_id": eff_drive,
        "status": state.status if state else "IDLE",
        "items_indexed": state.items_indexed if state else total_items,
        "total_items": total_items,
        "supported_files": supported_files,
        "folders": folders_count,
        "scan_started_at": state.scan_started_at.isoformat() if state and state.scan_started_at else None,
        "last_full_at": state.last_full_at.isoformat() if state and state.last_full_at else None,
        "last_step_at": state.last_step_at.isoformat() if state and state.last_step_at else None,
        "error_message": state.error_message if state else None,
    }


def is_matching_modified_filter(file_modified: Optional[datetime], mod_filter: Optional[str], now: datetime) -> bool:
    """Filter by last modified date:
    - 30d: in the last 30 days
    - 90d: last 90 days
    - 1y: last 365 days
    - older: more than 365 days ago
    - all: no filter (undated files appear only in all)
    """
    filter_val = (mod_filter or "all").strip().lower()
    if filter_val == "all":
        return True
    if file_modified is None:
        return False

    fm = _as_utc(file_modified)
    if fm is None:
        return False

    if filter_val == "30d":
        return fm >= now - timedelta(days=30)
    elif filter_val == "90d":
        return fm >= now - timedelta(days=90)
    elif filter_val == "1y":
        return fm >= now - timedelta(days=365)
    elif filter_val == "older":
        return fm < now - timedelta(days=365)
    return True


async def get_not_imported_from_index(
    db: Session,
    client=None,
    drive_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    view: str = "likely",
    include_pdf: bool = False,
    folder: Optional[str] = None,
    q: Optional[str] = None,
    modified: Optional[str] = "all",
) -> Dict[str, Any]:
    """Read candidates from the stored index that are not yet imported into QShala.

    Filters files under configured scope roots (database allowed folders or env roots),
    omits duplicate decks whose normalised name matches an existing deck,
    classifies into buckets ('likely', 'other', 'excluded') via relevance.py,
    filters by view, folder, and search term q,
    sorts newest first (undated last), and pages with limit and offset.
    """
    target_drive = drive_id or getattr(client, "drive_id", None) or getattr(settings, "SHAREPOINT_DRIVE_ID", None)
    if not target_drive and client:
        try:
            drives = await client.list_drives()
            if drives:
                target_drive = drives[0]["id"]
        except Exception:
            pass

    state = db.query(SharePointIndexState).filter(
        SharePointIndexState.drive_id == target_drive
    ).first() if target_drive else db.query(SharePointIndexState).first()

    index_state = {
        "last_full_at": state.last_full_at.isoformat() if state and state.last_full_at else None,
        "status": state.status if state else "IDLE",
        "items_indexed": state.items_indexed if state else 0,
    }

    empty_buckets = {"likely": 0, "other": 0, "excluded": 0}

    if not target_drive:
        return {
            "count": 0,
            "total": 0,
            "files": [],
            "buckets": empty_buckets,
            "index": index_state,
        }

    # Load all folders for target_drive to rebuild folder paths in memory
    folder_rows = db.query(
        SharePointIndexItem.id,
        SharePointIndexItem.parent_id,
        SharePointIndexItem.name,
    ).filter(
        SharePointIndexItem.drive_id == target_drive,
        SharePointIndexItem.is_folder == True,
    ).all()

    folder_map: Dict[str, tuple[Optional[str], str]] = {
        f.id: (f.parent_id, f.name) for f in folder_rows
    }

    path_cache: Dict[str, str] = {}

    def get_folder_path(folder_id: Optional[str]) -> str:
        if not folder_id or folder_id not in folder_map:
            return ""
        if folder_id in path_cache:
            return path_cache[folder_id]
        parts = []
        curr = folder_id
        visited = set()
        while curr and curr in folder_map and curr not in visited:
            visited.add(curr)
            p_id, p_name = folder_map[curr]
            parts.append(p_name)
            curr = p_id
        parts.reverse()
        full_path = "/".join(parts)
        path_cache[folder_id] = full_path
        return full_path

    # Determine scope roots
    from backend.app.services.sharepoint import importer

    root_map: Optional[Dict[str, str]] = {}  # folder_id -> root display name

    # 1. Database allowed folders
    allowed = db.query(SharePointAllowedFolder).filter(
        SharePointAllowedFolder.drive_id == target_drive,
        SharePointAllowedFolder.is_active == True,
    ).all()
    if allowed:
        for a in allowed:
            root_map[a.folder_id] = a.display_name or a.folder_name
    else:
        # 2. Configured env roots
        raw_env = (
            getattr(settings, "SHAREPOINT_ROOT_FOLDERS", None)
            or getattr(settings, "SHAREPOINT_ROOT_FOLDER", None)
            or ""
        ).strip()
        env_names = [f.strip().strip("/") for f in raw_env.split(",") if f.strip().strip("/")]
        if env_names:
            if client:
                try:
                    scoped_roots = await importer.scope_roots(client, target_drive, db=db)
                    for r in scoped_roots:
                        root_map[r["id"]] = r.get("name") or r.get("path") or "root"
                except Exception as e:
                    logger.warning("scope_roots failed with Graph: %s", e)

            # Also match folder rows in the index if client didn't resolve them
            for fid, (pid, fname) in folder_map.items():
                fpath = get_folder_path(fid)
                for env_name in env_names:
                    if fname.lower() == env_name.lower() or fpath.lower() == env_name.lower():
                        if fid not in root_map:
                            root_map[fid] = env_name
        else:
            # No scope roots configured: include all files under virtual root
            root_map = None

    def find_scope_root(parent_id: Optional[str]) -> Optional[str]:
        if root_map is None:
            # If no explicit root map configured, top-level folder is the root
            curr = parent_id
            visited = set()
            top = None
            while curr and curr not in visited and curr in folder_map:
                visited.add(curr)
                p_id, p_name = folder_map[curr]
                top = p_name
                curr = p_id
            return top or "root"

        curr = parent_id
        visited = set()
        while curr and curr not in visited:
            if curr in root_map:
                return root_map[curr]
            visited.add(curr)
            if curr not in folder_map:
                break
            curr = folder_map[curr][0]
        return None

    def get_ancestor_folder_ids(folder_id: Optional[str]) -> List[str]:
        if not folder_id or folder_id not in folder_map:
            return []
        res = []
        curr = folder_map[folder_id][0]
        visited = set()
        while curr and curr in folder_map and curr not in visited:
            visited.add(curr)
            res.append(curr)
            curr = folder_map[curr][0]
        return res

    # Hidden folders
    hidden_rows = db.query(SharePointHiddenFolder.folder_id).filter(
        (SharePointHiddenFolder.drive_id == target_drive) | (SharePointHiddenFolder.drive_id == None)
    ).all()
    hidden_folder_ids: Set[str] = {r[0] for r in hidden_rows}

    # Load all supported files from the index
    items = db.query(SharePointIndexItem).filter(
        SharePointIndexItem.drive_id == target_drive,
        SharePointIndexItem.is_folder == False,
        SharePointIndexItem.ext.in_([".pptx", ".pdf", "pptx", "pdf"]),
    ).all()

    # Preload deck name index to filter duplicate decks once per request
    deck_index = existing_deck_names(db)

    # Folders containing ingested decks for affinity calculation (up to 2 levels)
    folders_with_ingested_decks: Set[str] = {
        it.parent_id for it in items
        if it.parent_id and find_deck_in_index(deck_index, it.name)
    }

    # Precompute exclude rules
    exclude_phrases, exclude_single_words = get_exclude_rules()

    target_root_filter = folder.strip().lower() if folder and folder.strip() and folder.strip().lower() != "all" else None
    search_q = q.strip().lower() if q and q.strip() else None
    now = datetime.now(timezone.utc)

    bucket_counts = {"likely": 0, "other": 0, "excluded": 0}
    view_candidates: List[Dict[str, Any]] = []

    for it in items:
        matched_root = find_scope_root(it.parent_id)
        if matched_root is None:
            continue

        # Filter by scope root if specified
        if target_root_filter and matched_root.lower() != target_root_filter:
            continue

        # Skip files already in platform
        if find_deck_in_index(deck_index, it.name):
            continue

        folder_path = get_folder_path(it.parent_id)

        # Filter by search query q if specified
        if search_q:
            if search_q not in it.name.lower() and search_q not in folder_path.lower():
                continue

        # Filter by modified date
        if not is_matching_modified_filter(it.modified, modified, now):
            continue

        ancestors = get_ancestor_folder_ids(it.parent_id)
        bucket = classify_file(
            name=it.name,
            folder_path=folder_path,
            size=it.size,
            ext=it.ext,
            folder_id=it.parent_id,
            include_pdf=include_pdf,
            hidden_folder_ids=hidden_folder_ids,
            ancestor_folder_ids=ancestors,
            folders_with_affinity=folders_with_ingested_decks,
            exclude_phrases=exclude_phrases,
            exclude_single_words=exclude_single_words,
        )

        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        # Check if item belongs to requested view
        normalized_view = (view or "likely").lower()
        include_in_view = False
        if normalized_view == "likely":
            include_in_view = (bucket == "likely")
        elif normalized_view == "other":
            include_in_view = (bucket == "other")
        elif normalized_view == "all":
            include_in_view = (bucket in ("likely", "other"))
        elif normalized_view == "excluded":
            include_in_view = (bucket == "excluded")

        if include_in_view:
            view_candidates.append({
                "id": it.id,
                "drive_id": it.drive_id,
                "name": it.name,
                "folder_id": it.parent_id,
                "folder_path": folder_path,
                "size": it.size or 0,
                "last_modified": it.modified.isoformat() if it.modified else None,
                "modified_dt": it.modified,
                "root": matched_root,
                "bucket": bucket,
            })

    # Sort newest first, undated last
    view_candidates.sort(
        key=lambda f: (f["modified_dt"] is not None, f["modified_dt"] or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    total = len(view_candidates)
    paged = view_candidates[offset : offset + limit]

    result_files = [
        {
            "id": f["id"],
            "drive_id": f["drive_id"],
            "name": f["name"],
            "folder_id": f["folder_id"],
            "folder_path": f["folder_path"],
            "size": f["size"],
            "last_modified": f["last_modified"],
            "root": f["root"],
            "bucket": f["bucket"],
        }
        for f in paged
    ]

    return {
        "count": len(result_files),
        "total": total,
        "files": result_files,
        "buckets": bucket_counts,
        "index": index_state,
    }
