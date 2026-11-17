from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, BigInteger, Text, DateTime, Index
from backend.app.database import Base

class SharePointSyncState(Base):
    __tablename__ = "sharepoint_sync_state"

    id = Column(String(50), primary_key=True)  # e.g., 'qshala_main_library'
    site_id = Column(String(255), nullable=True)
    drive_id = Column(String(255), nullable=True)
    delta_token = Column(Text, nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="IDLE")  # IDLE, SYNCING, FAILED
    total_files_tracked = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SharePointFile(Base):
    __tablename__ = "sharepoint_files"

    id = Column(String(255), primary_key=True)  # Microsoft Graph driveItem.id
    name = Column(String(255), nullable=False)
    web_url = Column(Text, nullable=True)
    etag = Column(String(100), nullable=True)
    c_tag = Column(String(100), nullable=True)
    file_size_bytes = Column(BigInteger, default=0)
    last_modified_date_time = Column(DateTime(timezone=True), nullable=True)
    blob_url = Column(Text, nullable=True)
    # DISCOVERED (delta sync), SELECTED (chosen in the importer, waiting for transfer), TRANSFERRING, QUEUED (job created),
    # COMPLETED, FAILED, DELETED, SKIPPED_DUPLICATE (identical file already in QShala)
    sync_status = Column(String(50), default="DISCOVERED")
    error_message = Column(Text, nullable=True)
    drive_id = Column(String(255), nullable=True)
    folder_path = Column(Text, nullable=True)
    quick_xor_hash = Column(String(128), nullable=True)      # Graph's content hash of the SharePoint copy
    file_sha256 = Column(String(64), nullable=True)           # sha256 of the bytes we downloaded (matches documents.file_sha256)
    match_document_id = Column(String(36), nullable=True)     # existing document this file duplicates, when found
    batch_id = Column(String(36), nullable=True)               # one importer click = one batch
    selected_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    __table_args__ = (Index("ix_sharepoint_files_batch_id", "batch_id"), Index("ix_sharepoint_files_sync_status", "sync_status"))
