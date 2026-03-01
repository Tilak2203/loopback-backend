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
CRIME_KEYWORDS = (
    "crime", "safety", "security", "theft", "robbery", "assault", "violence",
    "harassment", "gun", "shooting", "battery", "burglary", "vandalism", "mugging"
)

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

def recommend_routes(
    db: Session,
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    mode: str,
) -> dict[str, Any]:
    routes = get_mapbox_routes(
        start_lat=start_lat, start_lon=start_lon,
        end_lat=end_lat, end_lon=end_lon,
        mode=mode,
        max_routes=settings.MAX_MAPBOX_ROUTES,
    )
    if not routes:
        raise ValueError("No routes returned by routing provider")

    # For MVP: pull up to 500 tasks (enough for hackathon demo). This powers the flags.
    tasks = db.query(Task).filter(Task.final_severity_1to5 >= 1).order_by(Task.final_severity_1to5.desc()).limit(500).all()
    issues = [{"lat": t.lat, "lon": t.lon, "final_severity_1to5": t.final_severity_1to5, "category": t.category} for t in tasks]

    scored = []
    for r in routes:
        flag = _route_flag(r.polyline, issues)
        hazard_rank = {"GREEN": 0, "YELLOW": 1, "RED": 2}[flag["level"]]
        scored.append((r, flag, hazard_rank))

    # Route A = default route
    route_a, flag_a, _ = scored[0]

    # Route B = recommended (lowest hazard, then duration)
    best = sorted(scored, key=lambda x: (x[2], x[1]["max_severity"], x[0].duration_s, x[0].distance_m))[0]
    route_b, flag_b, _ = best

    def pack(route, flag):
        return {
            "name": route.name,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "flag": flag,
            "polyline": route.polyline,
        }

    return {"route_a": pack(route_a, flag_a), "route_b": pack(route_b, flag_b)}


def _incident_near_route(polyline: list[tuple[float, float]], lat: float, lon: float) -> bool:
    if not polyline:
        return False
    step = max(1, len(polyline) // 40)
    for p_lat, p_lon in polyline[::step]:
        if haversine_m(p_lat, p_lon, lat, lon) <= settings.ISSUE_NEAR_ROUTE_METERS:
            return True
    return False


def _is_crime_related(category: str | None, description: str | None) -> bool:
    text = f"{category or ''} {description or ''}".lower()
    return any(keyword in text for keyword in CRIME_KEYWORDS)


def recommend_routes_with_llm(
    db: Session,
    *,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    mode: str,
) -> dict[str, Any]:
    routes = get_mapbox_routes(
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        mode=mode,
        max_routes=settings.MAX_MAPBOX_ROUTES,
    )
    if not routes:
        raise ValueError("No routes returned by routing provider")

    since_dt = datetime.utcnow() - timedelta(days=7)

    recent_rows = (
        db.query(Report, Task.final_severity_1to5)
        .outerjoin(Task, Task.task_id == Report.task_id)
        .filter(
            Report.created_at >= since_dt,
            Report.lat.isnot(None),
            Report.lon.isnot(None),
        )
        .order_by(Report.created_at.desc())
        .limit(2000)
        .all()
    )

    route_summaries: list[dict[str, Any]] = []
    for idx, route in enumerate(routes):
        route_incidents: list[dict[str, Any]] = []
        for report, task_sev in recent_rows:
            lat = float(report.lat)
            lon = float(report.lon)
            if not _incident_near_route(route.polyline, lat, lon):
                continue

            severity = int(task_sev or report.user_priority or 1)
            crime_related = _is_crime_related(report.category, report.description)

            route_incidents.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "severity": max(1, min(5, severity)),
                    "crime_related": crime_related,
                    "category": report.category,
                    "created_at": report.created_at.isoformat() if report.created_at else None,
                }
            )

        total = len(route_incidents)
        crime_count = sum(1 for item in route_incidents if item["crime_related"])
        max_severity = max((item["severity"] for item in route_incidents), default=0)
        avg_severity = (
            sum(item["severity"] for item in route_incidents) / total if total else 0.0
        )
        risk_score = round(crime_count * 3.0 + max_severity * 2.0 + avg_severity + total * 0.5, 3)

        route_summaries.append(
            {
                "index": idx,
                "name": route.name,
                "distance_m": route.distance_m,
                "duration_s": route.duration_s,
                "incident_count": total,
                "crime_related_count": crime_count,
                "max_severity": max_severity,
                "avg_severity": round(avg_severity, 2),
                "risk_score": risk_score,
                "incidents": route_incidents[:80],
            }
        )

    llm_choice = choose_routes_with_llm(
        start={"lat": start_lat, "lon": start_lon},
        end={"lat": end_lat, "lon": end_lon},
        mode=mode,
        window_days=7,
        route_summaries=route_summaries,
    )

    if llm_choice:
        avoid_idx = int(llm_choice["avoid_route_index"])
        recommended_idx = int(llm_choice["recommended_route_index"])
        generated_by = "llm"
        avoid_reason = llm_choice.get("avoid_reason", "Higher incident risk on this corridor.")
        recommended_reason = llm_choice.get("recommended_reason", "Lower incident risk with safer profile.")
    else:
        ranked = sorted(
            route_summaries,
            key=lambda item: (-item["risk_score"], -item["max_severity"], item["duration_s"]),
        )
        avoid_idx = int(ranked[0]["index"])

        ranked_safe = sorted(
            route_summaries,
            key=lambda item: (item["risk_score"], item["max_severity"], item["duration_s"]),
        )
        recommended_idx = int(ranked_safe[0]["index"])
        if len(route_summaries) > 1 and recommended_idx == avoid_idx:
            recommended_idx = int(ranked_safe[1]["index"])

        generated_by = "rules"
        avoid_reason = "Highest route risk score from last 7 days incidents between start and end."
        recommended_reason = "Lowest route risk score with fewer/less severe incidents in last 7 days."

    def pack(route_idx: int, reason: str) -> dict[str, Any]:
        route = routes[route_idx]
        summary = route_summaries[route_idx]
        return {
            "index": route_idx,
            "name": route.name,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "reason": reason,
            "incident_summary": {
                "window_days": 7,
                "incident_count": summary["incident_count"],
                "crime_related_count": summary["crime_related_count"],
                "max_severity": summary["max_severity"],
                "avg_severity": summary["avg_severity"],
                "risk_score": summary["risk_score"],
            },
            "polyline": route.polyline,
        }

    return {
        "avoid_route": pack(avoid_idx, avoid_reason),
        "recommended_route": pack(recommended_idx, recommended_reason),
        "window_days": 7,
        "generated_by": generated_by,
    }