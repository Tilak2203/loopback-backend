import json
import re
from dataclasses import dataclass
from typing import Optional, Literal, Any, Dict

import requests

from loopback.config import settings

DepartmentId = Literal["CTA_OPS", "CITY_311", "SECURITY", "COMMUNITY"]

@dataclass(frozen=True)
class LLMTriageResult:
    final_severity_1to5: int
    reason: str
    department: DepartmentId
    complaint_draft: str
    meta: Dict[str, Any]

_PROMPT = """You are a civic operations triage assistant for a city.
Return STRICT JSON only (no extra text).

Goal:
Given an aggregated city issue (category + location + crowd signal + avg priority),
1) produce a severity rating (1..5),
2) choose the department,
3) write a professional complaint draft that can be sent to that department.

Rules:
- Severity should primarily follow base_severity, but you may adjust by at most +/- 1.
- You must consider TWO factors in your reasoning:
  (a) avg_user_priority and (b) unique_user_count.
- The complaint draft must include the location_text, category, and what action is requested.
- If immediate danger is mentioned, add: "If this is an emergency or someone is in immediate danger, call 911."

Output JSON schema:
{
  "final_severity_1to5": 1..5,
  "reason": "string",
  "department": "CTA_OPS|CITY_311|SECURITY|COMMUNITY",
  "complaint_draft": "string",
  "meta": { ... }
}
"""

def _clamp_llm_severity(base: int, llm: int) -> int:
    base = max(1, min(5, base))
    llm = max(1, min(5, llm))
    delta = llm - base
    if delta > settings.MAX_LLM_SEVERITY_ADJUST:
        return base + settings.MAX_LLM_SEVERITY_ADJUST
    if delta < -settings.MAX_LLM_SEVERITY_ADJUST:
        return base - settings.MAX_LLM_SEVERITY_ADJUST
    return llm

def _extract_json(text: str) -> dict:
    """
    Gemini usually returns clean JSON if instructed, but sometimes wraps it.
    This tries:
      1) direct json.loads
      2) extract first {...} block
    """
    text = text.strip()

    # Remove optional markdown fences: ```json ... ```
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    # Attempt direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    def _find_balanced_object_candidates(raw: str) -> list[str]:
        candidates: list[str] = []
        start_idx: Optional[int] = None
        depth = 0
        in_str = False
        escaped = False

        for i, ch in enumerate(raw):
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue

            if ch == "{":
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        candidates.append(raw[start_idx : i + 1])
                        start_idx = None

        return candidates

    # Try balanced JSON-object candidates in order of size (largest first).
    candidates = _find_balanced_object_candidates(text)
    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            return json.loads(candidate)
        except Exception:
            pass

        # One lenient cleanup pass for common model formatting mistakes.
        cleaned = candidate
        cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"')
        cleaned = cleaned.replace("\u2018", "'").replace("\u2019", "'")
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            return json.loads(cleaned)
        except Exception:
            pass

    raise ValueError("LLM output was not valid JSON")

def _gemini_generate_text(system_prompt: str, user_text: str) -> str:
    """
    Calls Gemini generateContent and returns plain text output.
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("Missing GEMINI_API_KEY")

    model = getattr(settings, "GEMINI_MODEL", None) or "gemini-2.5-flash"

    # Gemini REST endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    params = {"key": settings.GEMINI_API_KEY}

    # Use a single text part that includes system + user for simplicity & compatibility
    # (Some versions support 'systemInstruction', but this works reliably.)
    combined = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_text}"

    body = {
        "contents": [
            {"role": "user", "parts": [{"text": combined}]}
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 600,
            "responseMimeType": "application/json",
        },
    }

    resp = requests.post(url, params=params, json=body, timeout=25)
    if resp.status_code != 200:
        raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:400]}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates")

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_parts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    if not text_parts:
        raise ValueError("Gemini response missing text content")

    return "\n".join(text_parts)

def triage_with_llm(
    *,
    category: str,
    location_text: str,
    report_count: int,
    unique_user_count: int,
    avg_user_priority: float,
    base_severity_1to5: int,
    proposed_department: str,
    sample_reports: list[str],
) -> Optional[LLMTriageResult]:
    # If no Gemini key, fallback to None (your service.py already handles fallback)
    if not getattr(settings, "GEMINI_API_KEY", ""):
        return None

    payload = {
        "category": category,
        "location_text": location_text,
        "aggregates": {
            "report_count": report_count,
            "unique_user_count": unique_user_count,
            "avg_user_priority": round(avg_user_priority, 2),
            "base_severity_1to5": base_severity_1to5,
            "proposed_department": proposed_department,
        },
        "sample_reports": sample_reports[:5],
    }

    try:
        # Call Gemini
        text = _gemini_generate_text(_PROMPT, json.dumps(payload, ensure_ascii=False))

        # Parse JSON
        data = _extract_json(text)
    except Exception:
        return None

    llm_sev = int(data.get("final_severity_1to5", base_severity_1to5))
    final_sev = _clamp_llm_severity(base_severity_1to5, llm_sev)

    dept = str(data.get("department", proposed_department)).upper()
    if dept not in {"CTA_OPS", "CITY_311", "SECURITY", "COMMUNITY"}:
        dept = proposed_department

    reason = str(data.get("reason", "")).strip()[:800] or "LLM triage result."
    draft = str(data.get("complaint_draft", "")).strip()[:2000] or f"Please investigate {category} at {location_text}."

    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        meta = {"raw_meta": meta}

    return LLMTriageResult(
        final_severity_1to5=final_sev,
        reason=reason,
        department=dept,  # type: ignore
        complaint_draft=draft,
        meta=meta,
    )


_ROUTE_PROMPT = """You are a route safety analysis assistant.
Return STRICT JSON only (no extra text).

Task:
Given candidate routes between a start and end location, and incident summaries restricted to the last N days
and near each route corridor, choose:
1) one route to avoid (highest risk), and
2) one preferred route (lowest risk).

Rules:
- Only use the provided route + incident summary data.
- Prefer fewer crime-related incidents and lower severities.
- If tied, prefer shorter duration.
- recommended_route_index must be different from avoid_route_index when multiple routes exist.

Output JSON schema:
{
  "avoid_route_index": 0,
  "recommended_route_index": 1,
  "avoid_reason": "string",
  "recommended_reason": "string",
  "notes": "string"
}
"""


def choose_routes_with_llm(
    *,
    start: dict[str, float],
    end: dict[str, float],
    mode: str,
    window_days: int,
    route_summaries: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not getattr(settings, "GEMINI_API_KEY", ""):
        return None

    payload = {
        "start": start,
        "end": end,
        "mode": mode,
        "window_days": window_days,
        "routes": route_summaries,
    }

    try:
        text = _gemini_generate_text(_ROUTE_PROMPT, json.dumps(payload, ensure_ascii=False))
        data = _extract_json(text)
    except Exception:
        return None

    if not route_summaries:
        return None

    max_index = len(route_summaries) - 1
    try:
        avoid_idx = int(data.get("avoid_route_index", 0))
    except Exception:
        avoid_idx = 0
    try:
        rec_idx = int(data.get("recommended_route_index", 0))
    except Exception:
        rec_idx = 0

    avoid_idx = max(0, min(max_index, avoid_idx))
    rec_idx = max(0, min(max_index, rec_idx))

    if len(route_summaries) > 1 and rec_idx == avoid_idx:
        rec_idx = 1 if avoid_idx == 0 else 0

    return {
        "avoid_route_index": avoid_idx,
        "recommended_route_index": rec_idx,
        "avoid_reason": str(data.get("avoid_reason", "Higher incident risk on this corridor.")).strip()[:500],
        "recommended_reason": str(data.get("recommended_reason", "Lower incident risk with safer profile.")).strip()[:500],
        "notes": str(data.get("notes", "")).strip()[:500],
    }


_TOMORROW_PLAN_PROMPT = """You are a commute wellbeing planner.
Return STRICT JSON only (no extra text).

Task:
Given recommended/avoid route safety summaries for tomorrow, produce:
1) one concrete "Do this" action,
2) one concrete "Avoid this" action,
3) a tomorrow wellbeing score (1-100) and short outlook.

Rules:
- Keep titles short and actionable.
- Keep details practical and specific to provided risk data.
- Do not invent facts not present in input.

Output JSON schema:
{
  "do_this": {"title": "string", "detail": "string"},
  "avoid_this": {"title": "string", "detail": "string"},
  "wellbeing": {"score_1to100": 72, "outlook": "string", "reason": "string"}
}
"""


def generate_tomorrow_plan_with_llm(
    *,
    mode: str,
    window_days: int,
    recommended_route: dict[str, Any],
    avoid_route: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not getattr(settings, "GEMINI_API_KEY", ""):
        return None

    payload = {
        "mode": mode,
        "window_days": window_days,
        "recommended_route": recommended_route,
        "avoid_route": avoid_route,
    }

    try:
        text = _gemini_generate_text(_TOMORROW_PLAN_PROMPT, json.dumps(payload, ensure_ascii=False))
        data = _extract_json(text)
    except Exception:
        return None

    do_this = data.get("do_this") if isinstance(data.get("do_this"), dict) else {}
    avoid_this = data.get("avoid_this") if isinstance(data.get("avoid_this"), dict) else {}
    wellbeing = data.get("wellbeing") if isinstance(data.get("wellbeing"), dict) else {}

    try:
        score = int(wellbeing.get("score_1to100", 70))
    except Exception:
        score = 70
    score = max(1, min(100, score))

    return {
        "do_this": {
            "title": str(do_this.get("title", "Take the recommended route tomorrow")).strip()[:120],
            "detail": str(do_this.get("detail", "Use the recommended route for lower incident exposure.")).strip()[:400],
        },
        "avoid_this": {
            "title": str(avoid_this.get("title", "Avoid higher-risk route options")).strip()[:120],
            "detail": str(avoid_this.get("detail", "Skip routes with higher recent incident severity and density.")).strip()[:400],
        },
        "wellbeing": {
            "score_1to100": score,
            "outlook": str(wellbeing.get("outlook", "Moderately positive")).strip()[:120],
            "reason": str(wellbeing.get("reason", "Based on recent route incident profile and safer alternative availability.")).strip()[:400],
        },
    }