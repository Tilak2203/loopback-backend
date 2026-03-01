import uuid
from datetime import datetime
from sqlalchemy import (
    String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from loopback.db import Base

class User(Base):
    __tablename__ = "users"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    xp_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class Department(Base):
    __tablename__ = "departments"
    dept_id: Mapped[str] = mapped_column(String, primary_key=True)     # CTA_OPS, CITY_311, SECURITY
    dept_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

class Task(Base):
    """
    Deduped unique issue bucket: (category + geohash)
    Stores aggregates + LLM outputs.
    """
    __tablename__ = "tasks"
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    geohash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    report_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unique_user_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_user_priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    base_severity_1to5: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    final_severity_1to5: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    severity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    assigned_dept_id: Mapped[str | None] = mapped_column(String, ForeignKey("departments.dept_id"), nullable=True)
    complaint_draft: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String, default="NEW", nullable=False)  # NEW/ACK/IN_PROGRESS/RESOLVED

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("category", "geohash", name="uq_task_category_geohash"),
    )

class Report(Base):
    """
    Raw user submission linked to the deduped task_id.
    """
    __tablename__ = "reports"
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.task_id"), nullable=False, index=True)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    user_priority: Mapped[int] = mapped_column(Integer, nullable=False)  # 1..5 (validated in API)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geohash: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)