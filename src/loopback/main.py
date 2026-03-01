from contextlib import asynccontextmanager
import logging
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loopback.config import settings
from loopback.db import Base, engine, get_db
from loopback.models import (
    Department, Task, Report, User, DeptWorker, AssignedTask, UserAction
)
from loopback.schemas import (
    # your existing smart report + routes recommend schemas
    ReportCreateRequest, ReportCreateResponse,
    DepartmentTasksResponse, TaskOut,
    RouteRecommendRequest, RouteRecommendResponse,
    LLMRouteRecommendRequest, LLMRouteRecommendResponse,

    # CRUD schemas
    DepartmentCreate, DepartmentOut,
    TaskCreate,
    ReportCreate, ReportOut,
    UserCreate, UserOut,
    DeptWorkerCreate, DeptWorkerOut,
    AssignedTaskCreate, AssignedTaskOut,
    UserActionCreate, UserActionOut,
)
from loopback.services import create_report_and_update_task, recommend_routes, recommend_routes_with_llm

logger = logging.getLogger(__name__)


def seed_departments(db: Session) -> None:
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
    try:
        Base.metadata.create_all(bind=engine)

        db = next(get_db())
        try:
            seed_departments(db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning(
            "Database unavailable during startup; continuing without DB initialization: %s",
            exc
        )

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="LoopBack API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ============================================================
    # SMART ENDPOINTS (your existing business logic)
    # ============================================================

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
            tasks=[TaskOut.model_validate(t) for t in tasks],
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

    @app.post("/routes/llm-recommend", response_model=LLMRouteRecommendResponse)
    def routes_llm_recommend(payload: LLMRouteRecommendRequest, db: Session = Depends(get_db)):
        try:
            rec = recommend_routes_with_llm(
                db,
                start_lat=payload.start.lat,
                start_lon=payload.start.lon,
                end_lat=payload.end.lat,
                end_lon=payload.end.lon,
                mode=payload.mode,
            )
            return LLMRouteRecommendResponse(
                avoid_route=rec["avoid_route"],
                recommended_route=rec["recommended_route"],
                window_days=rec["window_days"],
                generated_by=rec["generated_by"],
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ============================================================
    # CRUD ENDPOINTS FOR ALL TABLES (GET list, GET by id, POST)
    # ============================================================

    # -------------------- departments --------------------
    @app.get("/departments", response_model=list[DepartmentOut])
    def list_departments(db: Session = Depends(get_db)):
        return db.query(Department).order_by(Department.dept_id.asc()).all()

    @app.get("/departments/{dept_id}", response_model=DepartmentOut)
    def get_department(dept_id: str, db: Session = Depends(get_db)):
        d = db.query(Department).filter(Department.dept_id == dept_id).first()
        if not d:
            raise HTTPException(status_code=404, detail="Department not found")
        return d

    @app.post("/departments", response_model=DepartmentOut, status_code=201)
    def create_department(payload: DepartmentCreate, db: Session = Depends(get_db)):
        d = Department(**payload.model_dump())
        db.add(d)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Department already exists or violates constraints")
        db.refresh(d)
        return d

    # -------------------- tasks --------------------
    @app.get("/tasks", response_model=list[TaskOut])
    def list_tasks(limit: int = 200, db: Session = Depends(get_db)):
        return (
            db.query(Task)
            .order_by(Task.updated_at.desc())
            .limit(min(limit, 500))
            .all()
        )

    @app.get("/tasks/{task_id}", response_model=TaskOut)
    def get_task(task_id: UUID, db: Session = Depends(get_db)):
        t = db.query(Task).filter(Task.task_id == task_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Task not found")
        return t

    @app.post("/tasks", response_model=TaskOut, status_code=201)
    def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
        t = Task(**payload.model_dump())
        db.add(t)
        db.commit()
        db.refresh(t)
        return t

    # -------------------- reports (raw CRUD) --------------------
    @app.get("/reports/raw", response_model=list[ReportOut])
    def list_reports_raw(
        user_id: UUID | None = None,
        task_id: UUID | None = None,
        limit: int = 200,
        db: Session = Depends(get_db),
    ):
        q = db.query(Report)
        if user_id:
            q = q.filter(Report.user_id == user_id)
        if task_id:
            q = q.filter(Report.task_id == task_id)
        return q.order_by(Report.created_at.desc()).limit(min(limit, 500)).all()

    @app.get("/reports/raw/{report_id}", response_model=ReportOut)
    def get_report_raw(report_id: UUID, db: Session = Depends(get_db)):
        r = db.query(Report).filter(Report.report_id == report_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")
        return r

    @app.post("/reports/raw", response_model=ReportOut, status_code=201)
    def create_report_raw(payload: ReportCreate, db: Session = Depends(get_db)):
        r = Report(**payload.model_dump())
        db.add(r)
        db.commit()
        db.refresh(r)
        return r

    # -------------------- users --------------------
    @app.get("/users", response_model=list[UserOut])
    def list_users(limit: int = 200, db: Session = Depends(get_db)):
        return db.query(User).order_by(User.created_at.desc()).limit(min(limit, 500)).all()

    @app.get("/users/{user_id}", response_model=UserOut)
    def get_user(user_id: UUID, db: Session = Depends(get_db)):
        u = db.query(User).filter(User.user_id == user_id).first()
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        return u

    @app.post("/users", response_model=UserOut, status_code=201)
    def create_user(payload: UserCreate, db: Session = Depends(get_db)):
        u = User(**payload.model_dump())
        db.add(u)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Email already exists")
        db.refresh(u)
        return u

    # -------------------- dept_workers --------------------
    @app.get("/dept-workers", response_model=list[DeptWorkerOut])
    def list_dept_workers(limit: int = 200, db: Session = Depends(get_db)):
        return db.query(DeptWorker).order_by(DeptWorker.created_at.desc()).limit(min(limit, 500)).all()

    @app.get("/dept-workers/{worker_id}", response_model=DeptWorkerOut)
    def get_dept_worker(worker_id: UUID, db: Session = Depends(get_db)):
        w = db.query(DeptWorker).filter(DeptWorker.worker_id == worker_id).first()
        if not w:
            raise HTTPException(status_code=404, detail="Worker not found")
        return w

    @app.post("/dept-workers", response_model=DeptWorkerOut, status_code=201)
    def create_dept_worker(payload: DeptWorkerCreate, db: Session = Depends(get_db)):
        w = DeptWorker(**payload.model_dump())
        db.add(w)
        db.commit()
        db.refresh(w)
        return w

    # -------------------- assigned_tasks --------------------
    @app.get("/assigned-tasks", response_model=list[AssignedTaskOut])
    def list_assigned_tasks(
        task_id: UUID | None = None,
        worker_id: UUID | None = None,
        limit: int = 200,
        db: Session = Depends(get_db),
    ):
        q = db.query(AssignedTask)
        if task_id:
            q = q.filter(AssignedTask.task_id == task_id)
        if worker_id:
            q = q.filter(AssignedTask.worker_id == worker_id)
        return q.order_by(AssignedTask.assigned_at.desc()).limit(min(limit, 500)).all()

    @app.get("/assigned-tasks/{assignment_id}", response_model=AssignedTaskOut)
    def get_assigned_task(assignment_id: UUID, db: Session = Depends(get_db)):
        a = db.query(AssignedTask).filter(AssignedTask.assignment_id == assignment_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="Assignment not found")
        return a

    @app.post("/assigned-tasks", response_model=AssignedTaskOut, status_code=201)
    def create_assigned_task(payload: AssignedTaskCreate, db: Session = Depends(get_db)):
        a = AssignedTask(**payload.model_dump())
        db.add(a)
        db.commit()
        db.refresh(a)
        return a

    # -------------------- user_actions --------------------
    @app.get("/user-actions", response_model=list[UserActionOut])
    def list_user_actions(
        user_id: UUID | None = None,
        task_id: UUID | None = None,
        report_id: UUID | None = None,
        limit: int = 200,
        db: Session = Depends(get_db),
    ):
        q = db.query(UserAction)
        if user_id:
            q = q.filter(UserAction.user_id == user_id)
        if task_id:
            q = q.filter(UserAction.task_id == task_id)
        if report_id:
            q = q.filter(UserAction.report_id == report_id)
        return q.order_by(UserAction.created_at.desc()).limit(min(limit, 500)).all()

    @app.get("/user-actions/{action_id}", response_model=UserActionOut)
    def get_user_action(action_id: UUID, db: Session = Depends(get_db)):
        a = db.query(UserAction).filter(UserAction.action_id == action_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="Action not found")
        return a

    @app.post("/user-actions", response_model=UserActionOut, status_code=201)
    def create_user_action(payload: UserActionCreate, db: Session = Depends(get_db)):
        a = UserAction(**payload.model_dump())
        db.add(a)
        db.commit()
        db.refresh(a)
        return a

    return app


app = create_app()