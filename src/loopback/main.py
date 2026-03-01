from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from loopback.db import Base, engine, get_db
from loopback.models import Department, Task
from loopback.schemas import (
    ReportCreateRequest, ReportCreateResponse,
    DepartmentTasksResponse, TaskOut,
    RouteRecommendRequest, RouteRecommendResponse
)
from loopback.services import create_report_and_update_task, recommend_routes


def seed_departments(db: Session) -> None:
    """
    Insert core departments if missing.
    Safe to run on every startup.
    """
    seeds = [
        ("CTA_OPS", "CTA Operations", None),
        ("CITY_311", "City Services / 311", None),
        ("SECURITY", "Campus/Community Security", None),
        ("COMMUNITY", "Community Review", None),
    ]

    existing = {d.dept_id for d in db.query(Department.dept_id).all()}
    for dept_id, dept_name, desc in seeds:
        if dept_id not in existing:
            db.add(Department(dept_id=dept_id, dept_name=dept_name, description=desc))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables (hackathon-friendly). In production you'd use migrations.
    Base.metadata.create_all(bind=engine)

    # Seed departments
    db = next(get_db())
    try:
        seed_departments(db)
    finally:
        db.close()

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="LoopBack API", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/reports", response_model=ReportCreateResponse)
    def create_report(payload: ReportCreateRequest, db: Session = Depends(get_db)):
        try:
            res = create_report_and_update_task(
                db,
                user_id=payload.user_id,
                category=payload.category,
                description=payload.description,
                user_priority=payload.user_priority,
                lat=payload.lat,
                lon=payload.lon,
                location_text=payload.location_text,
            )
            report = res["report"]
            task = res["task"]

            return ReportCreateResponse(
                report_id=str(report.report_id),
                task_id=str(task.task_id),
                category=task.category,
                geohash=task.geohash,
                report_count=task.report_count,
                unique_user_count=task.unique_user_count,
                avg_user_priority=float(task.avg_user_priority),
                base_severity_1to5=task.base_severity_1to5,
                final_severity_1to5=task.final_severity_1to5,
                assigned_dept_id=task.assigned_dept_id or "CITY_311",
                complaint_draft=task.complaint_draft or "",
                severity_reason=task.severity_reason or "",
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/departments/{dept_id}/tasks", response_model=DepartmentTasksResponse)
    def department_tasks(dept_id: str, db: Session = Depends(get_db)):
        dept = dept_id.upper()
        tasks = (
            db.query(Task)
            .filter(Task.assigned_dept_id == dept)
            .order_by(Task.final_severity_1to5.desc(), Task.updated_at.desc())
            .limit(200)
            .all()
        )
        return DepartmentTasksResponse(
            department=dept,
            tasks=[
                TaskOut(
                    task_id=str(t.task_id),
                    category=t.category,
                    geohash=t.geohash,
                    lat=t.lat,
                    lon=t.lon,
                    report_count=t.report_count,
                    unique_user_count=t.unique_user_count,
                    avg_user_priority=float(t.avg_user_priority),
                    base_severity_1to5=t.base_severity_1to5,
                    final_severity_1to5=t.final_severity_1to5,
                    assigned_dept_id=t.assigned_dept_id,
                    status=t.status,
                    complaint_draft=t.complaint_draft,
                    severity_reason=t.severity_reason,
                )
                for t in tasks
            ],
        )

    @app.post("/routes/recommend", response_model=RouteRecommendResponse)
    def routes_recommend(payload: RouteRecommendRequest, db: Session = Depends(get_db)):
        try:
            rec = recommend_routes(
                db,
                start_lat=payload.start_lat,
                start_lon=payload.start_lon,
                end_lat=payload.end_lat,
                end_lon=payload.end_lon,
                mode=payload.mode,
            )
            return RouteRecommendResponse(route_a=rec["route_a"], route_b=rec["route_b"])
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    return app


app = create_app()