from datetime import datetime, timedelta
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import func

from loopback.config import settings
from loopback.geo import to_geohash, haversine_m
from loopback.maps import get_mapbox_routes
from loopback.llm import triage_with_llm, choose_routes_with_llm
from loopback.models import Task, Report


ACTIVE_TASK_STATUSES = ("NEW", "IN_PROGRESS")

# ---------- Department routing rules ----------
def choose_department(category: str, severity_1to5: int) -> str:
    cat = (category or "").strip().lower()
    mapping = {
        "transit": "CTA_OPS",
        "cta": "CTA_OPS",
        "lighting": "CITY_311",
        "pothole": "CITY_311",
        "sidewalk": "CITY_311",
        "accessibility": "CITY_311",
        "safety": "SECURITY",
    }
    dept = mapping.get(cat, "CITY_311")
    if cat == "safety" and severity_1to5 >= 4:
        return "SECURITY"
    return dept

# ---------- Base severity scoring ----------
def compute_base_severity(avg_user_priority: float, unique_user_count: int) -> int:
    # avg priority 1..5 -> 0..1
    pr = max(1.0, min(5.0, float(avg_user_priority or 1.0)))
    pr_norm = (pr - 1.0) / 4.0

    # unique reporters -> 0..1 (soft cap 10)
    crowd = min(max(int(unique_user_count or 0), 0) / 10.0, 1.0)

    score_0_1 = 0.65 * pr_norm + 0.35 * crowd
    sev = 1 + int(round(score_0_1 * 4))
    return max(1, min(5, sev))

# ---------- Report -> Task aggregation pipeline ----------
def create_report_and_update_task(
    db: Session,
    *,
    user_id: str | None,
    category: str,
    description: str,
    user_priority: int,
    lat: float,
    lon: float,
    location_text: str | None,
) -> dict[str, Any]:
    category = category.strip()
    gh = to_geohash(lat, lon, settings.GEOHASH_PRECISION)

    # 1) find-or-create task by (category + exact lat/lon) only for active statuses
    task = (
        db.query(Task)
        .filter(
            Task.category == category,
            Task.lat == lat,
            Task.lon == lon,
            Task.status.in_(ACTIVE_TASK_STATUSES),
        )
        .order_by(Task.updated_at.desc())
        .first()
    )
    if task is None:
        task = Task(category=category, geohash=gh, lat=lat, lon=lon, status="NEW")
        db.add(task)
        db.flush()

    # 2) create report linked to task
    report = Report(
        user_id=user_id,
        task_id=task.task_id,
        description=description,
        category=category,
        user_priority=user_priority,
        lat=lat,
        lon=lon,
        geohash=gh,
    )
    db.add(report)
    db.flush()

    # 3) recompute aggregates from reports for this task
    agg = (
        db.query(
            func.count(Report.report_id),
            func.avg(Report.user_priority),
            func.count(func.distinct(Report.user_id)),
        )
        .filter(Report.task_id == task.task_id)
        .one()
    )
    report_count = int(agg[0] or 0)
    avg_priority = float(agg[1] or 0.0)

    # distinct user_id: postgres will count 1 NULL if present; we want anonymous not counted
    # quick fix: if there is at least one NULL user_id, subtract 1
    null_count = db.query(func.count(Report.report_id)).filter(Report.task_id == task.task_id, Report.user_id.is_(None)).scalar() or 0
    distinct_count = int(agg[2] or 0)
    unique_users = max(0, distinct_count - (1 if null_count > 0 else 0))

    task.report_count = report_count
    task.avg_user_priority = avg_priority
    task.unique_user_count = unique_users

    # 4) compute base severity + proposed dept
    base_sev = compute_base_severity(avg_priority if avg_priority > 0 else user_priority, unique_users)
    dept_guess = choose_department(category, base_sev)

    # 5) LLM triage (optional; fallback if no key)
    loc_text = location_text or f"near ({lat:.5f}, {lon:.5f})"
    llm = triage_with_llm(
        category=category,
        location_text=loc_text,
        report_count=report_count,
        unique_user_count=unique_users,
        avg_user_priority=avg_priority if avg_priority > 0 else float(user_priority),
        base_severity_1to5=base_sev,
        proposed_department=dept_guess,
        sample_reports=[description],
    )

    if llm is None:
        final_sev = base_sev
        assigned_dept = dept_guess
        reason = f"Base severity from avg_priority={avg_priority:.2f} and unique_users={unique_users}."
        draft = f"To {assigned_dept}: Please investigate a {category} issue at {loc_text}. Reported by {report_count} submissions. Requested action: assess and resolve."
    else:
        final_sev = llm.final_severity_1to5
        assigned_dept = llm.department
        reason = llm.reason
        draft = llm.complaint_draft

    task.base_severity_1to5 = base_sev
    task.final_severity_1to5 = final_sev
    task.assigned_dept_id = assigned_dept
    task.severity_reason = reason
    task.complaint_draft = draft
    task.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(task)
    db.refresh(report)

    return {
        "report": report,
        "task": task,
    }

# ---------- Routing recommendation with flags ----------
def _route_flag(polyline: list[tuple[float, float]], issues: list[dict]) -> dict:
    if not polyline:
        return {"level": "GREEN", "max_severity": 0, "issue_count": 0, "message": "No geometry."}

    step = max(1, len(polyline) // 40)
    pts = polyline[::step]

    max_sev = 0
    count = 0
    for iss in issues:
        for (lat, lon) in pts:
            if haversine_m(lat, lon, iss["lat"], iss["lon"]) <= settings.ISSUE_NEAR_ROUTE_METERS:
                count += 1
                max_sev = max(max_sev, int(iss["final_severity_1to5"]))
                break

    if max_sev >= 4:
        return {"level": "RED", "max_severity": max_sev, "issue_count": count,
                "message": "High severity issues reported along this route. Better to avoid."}
    if max_sev == 3 or count >= 3:
        return {"level": "YELLOW", "max_severity": max_sev, "issue_count": count,
                "message": "Some moderate issues reported along this route. Use caution."}
    return {"level": "GREEN", "max_severity": max_sev, "issue_count": count,
            "message": "No significant issues reported near this route."}


def _is_between_start_end(
    *,
    point_lat: float,
    point_lon: float,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    corridor_buffer_m: float = 500.0,
) -> bool:
    direct = haversine_m(start_lat, start_lon, end_lat, end_lon)
    via_point = (
        haversine_m(start_lat, start_lon, point_lat, point_lon)
        + haversine_m(point_lat, point_lon, end_lat, end_lon)
    )
    return via_point <= (direct + corridor_buffer_m)


def _recent_corridor_issues(
    db: Session,
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    days_window: int,
) -> list[dict[str, Any]]:
    cutoff = datetime.utcnow() - timedelta(days=days_window)
    rows = (
        db.query(
            Report.lat,
            Report.lon,
            Report.category,
            Report.created_at,
            Task.final_severity_1to5,
        )
        .join(Task, Task.task_id == Report.task_id)
        .filter(
            Report.created_at >= cutoff,
            Report.lat.isnot(None),
            Report.lon.isnot(None),
        )
        .all()
    )

    issues: list[dict[str, Any]] = []
    for lat, lon, category, created_at, final_sev in rows:
        lat_f = float(lat)
        lon_f = float(lon)
        if not _is_between_start_end(
            point_lat=lat_f,
            point_lon=lon_f,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
        ):
            continue
        issues.append(
            {
                "lat": lat_f,
                "lon": lon_f,
                "final_severity_1to5": int(final_sev or 1),
                "category": category,
                "created_at": created_at.isoformat() if created_at is not None else None,
            }
        )

    return issues

def recommend_routes(
    db: Session,
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    mode: str,
) -> dict[str, Any]:
    days_window = 7

    routes = get_mapbox_routes(
        start_lat=start_lat, start_lon=start_lon,
        end_lat=end_lat, end_lon=end_lon,
        mode=mode,
        max_routes=settings.MAX_MAPBOX_ROUTES,
    )
    if not routes:
        raise ValueError("No routes returned by Mapbox")

    issues = _recent_corridor_issues(
        db,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        days_window=days_window,
    )

    scored = []
    for idx, r in enumerate(routes):
        flag = _route_flag(r.polyline, issues)
        hazard_rank = {"GREEN": 0, "YELLOW": 1, "RED": 2}[flag["level"]]
        route_id = f"route_{idx}"
        scored.append((route_id, r, flag, hazard_rank))

    llm_candidates: list[dict[str, Any]] = []
    for route_id, route, flag, hazard_rank in scored:
        llm_candidates.append(
            {
                "route_id": route_id,
                "name": route.name,
                "distance_m": route.distance_m,
                "duration_s": route.duration_s,
                "hazard_level": flag["level"],
                "hazard_rank": hazard_rank,
                "max_severity": flag["max_severity"],
                "nearby_issue_count": flag["issue_count"],
            }
        )

    decision = choose_routes_with_llm(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        mode=mode,
        days_window=days_window,
        candidates=llm_candidates,
        considered_issue_count=len(issues),
    )

    by_id = {route_id: (route, flag, hazard_rank) for route_id, route, flag, hazard_rank in scored}

    if decision is not None:
        avoid_route, avoid_flag, _ = by_id[decision.avoid_route_id]
        recommended_route, recommended_flag, _ = by_id[decision.recommended_route_id]
        selection_reason = decision.reason
        selection_meta = decision.meta
    else:
        worst = sorted(scored, key=lambda x: (-x[3], -x[2]["max_severity"], -x[2]["issue_count"], -x[1].duration_s, -x[1].distance_m))[0]
        best = sorted(scored, key=lambda x: (x[3], x[2]["max_severity"], x[2]["issue_count"], x[1].duration_s, x[1].distance_m))[0]

        if worst[0] == best[0] and len(scored) > 1:
            alternatives = [x for x in sorted(scored, key=lambda x: (x[3], x[2]["max_severity"], x[2]["issue_count"], x[1].duration_s, x[1].distance_m)) if x[0] != worst[0]]
            best = alternatives[0]

        avoid_route, avoid_flag, _ = worst[1], worst[2], worst[3]
        recommended_route, recommended_flag, _ = best[1], best[2], best[3]
        selection_reason = "Fallback selection from hazard severity, issue density, and duration."
        selection_meta = {"source": "heuristic"}

    def pack(route, flag, label: str):
        return {
            "label": label,
            "name": route.name,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "flag": flag,
            "polyline": route.polyline,
            "analysis_window_days": days_window,
            "considered_corridor_issue_count": len(issues),
        }

    return {
        "route_a": pack(avoid_route, avoid_flag, "avoid"),
        "route_b": pack(recommended_route, recommended_flag, "recommended"),
        "selection_reason": selection_reason,
        "selection_meta": selection_meta,
    }