import uuid
from sqlalchemy import Column, Text, Integer, Float, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from loopback.db import Base


class Department(Base):
    __tablename__ = "departments"

    dept_id = Column(Text, primary_key=True)           # text per your DB
    dept_name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    task_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    category = Column(Text, nullable=False)
    geohash = Column(Text, nullable=True)

    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)

    report_count = Column(Integer, nullable=False, default=0)
    unique_user_count = Column(Integer, nullable=False, default=0)
    avg_user_priority = Column(Float, nullable=False, default=0.0)

    base_severity_1to5 = Column(Integer, nullable=True)
    final_severity_1to5 = Column(Integer, nullable=True)
    severity_reason = Column(Text, nullable=True)

    assigned_dept_id = Column(Text, nullable=True)
    complaint_draft = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="open")

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now(), nullable=False)


class Report(Base):
    __tablename__ = "reports"

    report_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), nullable=False)
    task_id = Column(UUID(as_uuid=True), nullable=True)

    description = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    user_priority = Column(Integer, nullable=False, default=3)

    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    geohash = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)

    xp_points = Column(Integer, nullable=False, default=0)
    streak = Column(Integer, nullable=False, default=0)
    level = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)


class DeptWorker(Base):
    __tablename__ = "dept_workers"

    worker_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dept_id = Column(Text, nullable=False)

    name = Column(Text, nullable=False)
    role = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)


class AssignedTask(Base):
    __tablename__ = "assigned_tasks"

    assignment_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), nullable=False)
    worker_id = Column(UUID(as_uuid=True), nullable=False)

    assigned_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    notes = Column(Text, nullable=True)


class UserAction(Base):
    __tablename__ = "user_actions"

    action_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)

    action_type = Column(Text, nullable=False)
    xp_earned = Column(Integer, nullable=False, default=0)

    report_id = Column(UUID(as_uuid=True), nullable=True)
    task_id = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)