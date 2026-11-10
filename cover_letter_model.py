"""Strict document validation; preserve additional JSON fields during editing."""
from datetime import date
from copy import deepcopy


def validate_cover_letter(data):
    if not isinstance(data, dict):
        raise ValueError("cover_letter_data must be an object")
    groups = {
        "applicant": ("name", "email", "phone", "location", "linkedin", "portfolio"),
        "recipient": ("company", "name", "job_title", "address"),
    }
    for group, fields in groups.items():
        if not isinstance(data.get(group), dict):
            raise ValueError(f"{group} must be an object")
        for field in fields:
            value = data[group].get(field, "")
            if not isinstance(value, str):
                raise ValueError(f"{group}.{field} must be text")
    if not data["applicant"].get("name", "").strip():
        raise ValueError("Applicant name is required")
    for field in ("date", "subject", "greeting", "closing", "signature"):
        if not isinstance(data.get(field), str):
            raise ValueError(f"{field} must be text")
    try:
        date.fromisoformat(data["date"])
    except ValueError:
        raise ValueError("Date must use YYYY-MM-DD") from None
    paragraphs = data.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs or any(not isinstance(p, str) for p in paragraphs):
        raise ValueError("Paragraphs must be a non-empty array of strings")
    if not any(p.strip() for p in paragraphs):
        raise ValueError("At least one paragraph must contain text")
    return deepcopy(data)
