from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, Literal

DeptId = Literal["CTA_OPS", "CITY_311", "SECURITY", "COMMUNITY"]

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

class TaskOut(BaseModel):
    task_id: str
    category: str
    geohash: str
    lat: float
    lon: float
    report_count: int
    unique_user_count: int
    avg_user_priority: float
    base_severity_1to5: int
    final_severity_1to5: int
    assigned_dept_id: Optional[str]
    status: str
    complaint_draft: Optional[str]
    severity_reason: Optional[str]

class DepartmentTasksResponse(BaseModel):
    department: str
    tasks: list[TaskOut]

class RouteRecommendRequest(BaseModel):
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    mode: str = Field(default="walk")  # walk|drive|bike

class RouteRecommendResponse(BaseModel):
    route_a: Dict[str, Any]
    route_b: Dict[str, Any]