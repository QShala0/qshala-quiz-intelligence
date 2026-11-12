from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.services import trash

router = APIRouter(prefix="/trash", tags=["Trash"])


@router.get("")
def list_trash(db: Session = Depends(get_db)):
    """Recently deleted decks / question banks that can still be restored (kept TRASH_RETENTION_DAYS, default 30)."""
    items = trash.list_snapshots(db)
    db.commit()
    return {"retention_days": trash.retention_days(), "items": items}


@router.post("/{snapshot_id}/restore")
def restore_trash(snapshot_id: str, db: Session = Depends(get_db)):
    """Put a deleted deck / question bank back. Rows that exist now are left alone."""
    try:
        restored = trash.restore(db, snapshot_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such snapshot (it may have expired).")
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad snapshot id.")
    db.commit()
    try:
        from backend.app.services.prompt_quiz import engine as _pq
        _pq.invalidate_bank_cache()
    except Exception:
        pass
    return {"status": "ok", "restored": restored, "total": sum(restored.values())}
