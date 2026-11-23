from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, BigInteger, Text, DateTime, Boolean, Index
from backend.app.database import Base


class SharePointIndexItem(Base):
    __tablename__ = "sharepoint_index_items"

    id = Column(String(255), primary_key=True)  # Microsoft Graph driveItem.id
    drive_id = Column(String(255), nullable=False, index=True)
    parent_id = Column(String(255), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    is_folder = Column(Boolean, default=False, nullable=False, index=True)
    size = Column(BigInteger, default=0, nullable=True)
    modified = Column(DateTime(timezone=True), nullable=True)
    etag = Column(String(100), nullable=True)
    ext = Column(String(50), nullable=True, index=True)  # e.g. .pptx, .pdf
    last_seen_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_sp_index_items_drive_folder", "drive_id", "is_folder"),
        Index("ix_sp_index_items_drive_ext", "drive_id", "ext"),
    )


class SharePointIndexState(Base):
    __tablename__ = "sharepoint_index_state"

    drive_id = Column(String(255), primary_key=True)
    delta_link = Column(Text, nullable=True)
    status = Column(String(50), default="IDLE")  # IDLE, RUNNING, ERROR
    lease_until = Column(DateTime(timezone=True), nullable=True)
    scan_started_at = Column(DateTime(timezone=True), nullable=True)
    last_full_at = Column(DateTime(timezone=True), nullable=True)
    last_step_at = Column(DateTime(timezone=True), nullable=True)
    items_indexed = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
