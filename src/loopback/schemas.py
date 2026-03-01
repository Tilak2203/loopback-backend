from datetime import datetime
from typing import Optional, List, Any, Dict, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


DeptId = Literal["CTA_OPS", "CITY_311", "SECURITY", "COMMUNITY"]


# -------------------- Smart endpoints --------------------
class ReportCreateRequest(BaseModel):
    user_id: Optional[str] = None
    category: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2000)
    user_priority: int = Field(ge=1, le=5)
    lat: float
    lon: float
    location_text: Optional[str] = Field(default=None, max_length=200)


class ReportCreateResponse(BaseModel):
    report_id: str
    task_id: str
    category: str
    geohash: str

    report_count: int
    unique_user_count: int
    avg_user_priority: float

    base_severity_1to5: int
    final_severity_1to5: int
    assigned_dept_id: str
    complaint_draft: str
    severity_reason: str


class DepartmentTasksResponse(BaseModel):
    department: str
    tasks: list[Any]


class RouteRecommendRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    mode: str = Field(default="walk")


class RouteRecommendResponse(BaseModel):
    route_a: Dict[str, Any]
    route_b: Dict[str, Any]


class LatLonPoint(BaseModel):
    lat: float
    lon: float


class LLMRouteRecommendRequest(BaseModel):
    start: LatLonPoint
    end: LatLonPoint
    mode: str = Field(default="walk")


class LLMRouteRecommendResponse(BaseModel):
    avoid_route: Dict[str, Any]
    recommended_route: Dict[str, Any]
    window_days: int
    generated_by: str


# -------------------- Departments --------------------
class DepartmentCreate(BaseModel):
    dept_id: str
    dept_name: str
    description: Optional[str] = None


class DepartmentOut(BaseModel):
    dept_id: str
    dept_name: str
    description: Optional[str] = None

    class Config:
        from_attributes = True


# -------------------- Tasks --------------------
class TaskCreate(BaseModel):
    category: str
    geohash: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    report_count: int = 0
    unique_user_count: int = 0
    avg_user_priority: float = 0.0

    base_severity_1to5: Optional[int] = None
    final_severity_1to5: Optional[int] = None
    severity_reason: Optional[str] = None

    assigned_dept_id: Optional[str] = None
    complaint_draft: Optional[str] = None
    status: str = "NEW"


class TaskOut(BaseModel):
    task_id: UUID
    category: str
    geohash: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    report_count: int
    unique_user_count: int
    avg_user_priority: float
    base_severity_1to5: Optional[int] = None
    final_severity_1to5: Optional[int] = None
    severity_reason: Optional[str] = None
    assigned_dept_id: Optional[str] = None
    complaint_draft: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# -------------------- Reports (raw CRUD) --------------------
class ReportCreate(BaseModel):
    user_id: UUID
    task_id: Optional[UUID] = None
    description: str
    category: str
    user_priority: int = 3
    lat: Optional[float] = None
    lon: Optional[float] = None
    geohash: Optional[str] = None


class ReportOut(BaseModel):
    report_id: UUID
    user_id: UUID
    task_id: Optional[UUID] = None
    description: str
    category: str
    user_priority: int
    lat: Optional[float] = None
    lon: Optional[float] = None
    geohash: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# -------------------- Users --------------------
class UserCreate(BaseModel):
    name: str
    email: EmailStr


class UserOut(BaseModel):
    user_id: UUID
    name: str
    email: EmailStr
    xp_points: int
    streak: int
    level: int
    created_at: datetime

    class Config:
        from_attributes = True


# -------------------- Dept Workers --------------------
class DeptWorkerCreate(BaseModel):
    dept_id: str
    name: str
    role: Optional[str] = None


class DeptWorkerOut(BaseModel):
    worker_id: UUID
    dept_id: str
    name: str
    role: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# -------------------- Assigned Tasks --------------------
class AssignedTaskCreate(BaseModel):
    task_id: UUID
    worker_id: UUID
    notes: Optional[str] = None


class AssignedTaskOut(BaseModel):
    assignment_id: UUID
    task_id: UUID
    worker_id: UUID
    assigned_at: datetime
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# -------------------- User Actions --------------------
class UserActionCreate(BaseModel):
    user_id: UUID
    action_type: str
    xp_earned: int = 0
    report_id: Optional[UUID] = None
    task_id: Optional[UUID] = None


class UserActionOut(BaseModel):
    action_id: UUID
    user_id: UUID
    action_type: str
    xp_earned: int
    report_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    created_at: datetime

    class Config:
        from_attributes = True


# -------------------- List wrappers (optional) --------------------
class DepartmentList(BaseModel):
    items: List[DepartmentOut]


class TaskList(BaseModel):
    items: List[TaskOut]


class ReportList(BaseModel):
    items: List[ReportOut]


class UserList(BaseModel):
    items: List[UserOut]


class DeptWorkerList(BaseModel):
    items: List[DeptWorkerOut]


class AssignedTaskList(BaseModel):
    items: List[AssignedTaskOut]


class UserActionList(BaseModel):
    items: List[UserActionOut]