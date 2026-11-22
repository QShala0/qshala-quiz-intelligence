import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, Text, DateTime, JSON
from backend.app.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class CustomPptTemplate(Base):
    __tablename__ = "custom_ppt_templates"
    __table_args__ = {"extend_existing": True}

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False, index=True)
    # category: 'qshala_school' (QShala School) or 'zvq' (Zerodha Varsity Quiz)
    category = Column(String(50), nullable=False, default="qshala_school", index=True)
    description = Column(Text, nullable=True)
    
    # Path to the clean master .pptx stored on disk or R2
    storage_path = Column(String(512), nullable=False)
    file_size_bytes = Column(Integer, default=0)
    preview_image_path = Column(String(512), nullable=True)
    
    # Geometry & Dimensions
    aspect_ratio = Column(String(20), default="16:9")  # "16:9" or "4:3"
    slide_width_in = Column(Float, default=13.333)
    slide_height_in = Column(Float, default=7.5)
    
    # Typography
    title_font = Column(String(100), default="Causten Round")
    title_size_pt = Column(Float, default=44.0)
    title_color_hex = Column(String(20), default="#1E293B")
    
    body_font = Column(String(100), default="Causten Round Regular")
    body_size_pt = Column(Float, default=28.0)  # Uniform 28pt body standard
    body_color_hex = Column(String(20), default="#0F172A")
    
    header_font = Column(String(100), default="Causten Round Bold")
    header_size_pt = Column(Float, default=36.0)
    header_color_hex = Column(String(20), default="#000000")
    
    # Layout and placeholder mapping metadata
    layout_metadata = Column(JSON, default=dict)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
