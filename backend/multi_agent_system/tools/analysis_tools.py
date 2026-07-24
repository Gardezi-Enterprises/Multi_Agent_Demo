"""Resume analysis tools — owned by the Resume Analyzer Agent.

The analysis is domain-agnostic. Rather than matching against a fixed list of
technology skills, the tool asks the model to extract whatever skills the resume
actually contains and to name the department niche itself — so a cardiologist, a
chef, a paralegal and a Kubernetes engineer are all categorised sensibly, and no
taxonomy needs maintaining when a new field appears.

Callers may optionally constrain the classification to their own department
list, which keeps the tool usable inside an organisation that has fixed teams.

Contact details are still extracted deterministically in Python, because regex
is both cheaper and more reliable than a model for that, and it is not
domain-specific.
"""

import json
import re
from typing import List, Optional

from google.genai import types

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# Deliberately permissive: candidates are then validated on digit count, which
# keeps date ranges like "2020-2024" from being mistaken for phone numbers.
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{1,4}\)[\s.\-]?)?\d(?:[\d\s.\-]{6,16}\d)")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s,;|]+", re.I)

ANALYSIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "candidate_name": {"type": "STRING", "description": "Full name, or empty string if absent."},
        "primary_department": {
            "type": "STRING",
            "description": (
                "The single department niche or professional field this candidate "
                "best belongs to, named naturally (e.g. 'Data Science', "
                "'Cardiology', 'Corporate Law', 'Culinary Arts')."
            ),
        },
        "confidence": {
            "type": "NUMBER",
            "description": "Confidence in the primary_department, between 0 and 1.",
        },
        "seniority": {
            "type": "STRING",
            "description": "e.g. Intern, Junior, Mid-level, Senior, Lead, Executive. Empty if unclear.",
        },
        "years_of_experience": {
            "type": "NUMBER",
            "description": "Total years of professional experience. Use 0 if not stated.",
        },
        "summary": {"type": "STRING", "description": "A two-sentence profile summary."},
        "skills": {
            "type": "ARRAY",
            "description": "Every distinct skill, tool, technique or qualification found.",
            "items": {"type": "STRING"},
        },
        "skill_groups": {
            "type": "ARRAY",
            "description": "The same skills grouped under natural category headings.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING"},
                    "skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["category", "skills"],
            },
        },
        "department_breakdown": {
            "type": "ARRAY",
            "description": "Every plausible department niche, with a 0-1 match score, strongest first.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "department": {"type": "STRING"},
                    "score": {"type": "NUMBER"},
                    "matched_skills": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["department", "score", "matched_skills"],
            },
        },
        "education": {"type": "ARRAY", "items": {"type": "STRING"}},
        "certifications": {"type": "ARRAY", "items": {"type": "STRING"}},
        "employers": {"type": "ARRAY", "items": {"type": "STRING"}},
        "strengths": {"type": "ARRAY", "items": {"type": "STRING"}},
        "gaps": {
            "type": "ARRAY",
            "description": "Notable omissions or weaknesses a recruiter should probe.",
            "items": {"type": "STRING"},
        },
    },
    "required": ["candidate_name", "primary_department", "confidence", "skills"],
}

PROMPT = """You are an expert resume analyst. Analyse the resume below and return JSON.

Rules:
- Extract only what the resume actually supports. Never invent skills, employers,
  degrees or dates. Use an empty string or empty list when something is absent.
- Name the department niche in the candidate's own professional language. Do not
  force a technology label onto a non-technology resume: a nurse belongs to
  "Nursing", not "Data Science".
- department_breakdown should list every field the candidate plausibly fits,
  scored 0-1 and sorted strongest first.
- years_of_experience is the total professional span in years; use 0 if unstated.
{constraint}
RESUME:
\"\"\"
{resume}
\"\"\"
"""


def _extract_phone(text: str) -> str:
    """Return the first candidate holding a plausible phone-number digit count."""
    for match in PHONE_RE.finditer(text):
        candidate = match.group(0).strip(" .-")
        if 10 <= len(re.sub(r"\D", "", candidate)) <= 15:
            return candidate
    return ""


def analyze_resume_text(
    resume_text: str, departments: Optional[List[str]] = None
) -> dict:
    """Analyse resume text: extract skills and categorise into a department niche.

    Works for any profession — engineering, medicine, law, finance, hospitality,
    education and so on. The department niche is inferred from the resume itself
    unless a list of allowed departments is supplied.

    Args:
        resume_text: The full plain text of the resume to analyse.
        departments: Optional list of department names to choose from, e.g.
            ["Engineering", "Sales", "Design"]. When omitted, the most fitting
            department is inferred and named automatically.

    Returns:
        A dict with status "success" containing the candidate's contact details,
        extracted skills (flat and grouped), the primary department niche with a
        confidence score, a ranked department breakdown, education,
        certifications, strengths and gaps; or status "error" with a message.
    """
    # Imported here so the tool module stays importable without an API key,
    # which keeps the tool tests offline.
    from ..config import GEMINI_MODEL
    from ..core.runtime import get_client

    if not resume_text or len(resume_text.strip()) < 30:
        return {
            "status": "error",
            "message": "resume_text is empty or too short to analyse. Provide the full resume text.",
        }

    constraint = ""
    if departments:
        allowed = ", ".join(str(d) for d in departments if str(d).strip())
        if allowed:
            constraint = (
                f"- primary_department MUST be exactly one of: {allowed}. "
                "Choose the closest fit even if imperfect.\n"
            )

    prompt = PROMPT.format(constraint=constraint, resume=resume_text.strip())

    email_match = EMAIL_RE.search(resume_text)

    try:
        response = get_client().models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ANALYSIS_SCHEMA,
                temperature=0,
            ),
        )
        data = json.loads(response.text or "{}")
    except Exception as exc:
        # Contact details are still useful when the model call fails.
        return {
            "status": "error",
            "message": f"Resume analysis failed: {type(exc).__name__}: {exc}",
            "email": email_match.group(0) if email_match else "",
            "phone": _extract_phone(resume_text),
        }

    skills = [s for s in data.get("skills", []) if s]
    breakdown = sorted(
        data.get("department_breakdown", []) or [],
        key=lambda d: -float(d.get("score") or 0),
    )
    years = data.get("years_of_experience")

    return {
        "status": "success",
        "candidate_name": data.get("candidate_name", ""),
        # Regex wins for contact details: it cannot hallucinate an address.
        "email": email_match.group(0) if email_match else "",
        "phone": _extract_phone(resume_text),
        "links": URL_RE.findall(resume_text)[:5],
        "primary_department": data.get("primary_department") or "Unclassified",
        "confidence": round(float(data.get("confidence") or 0), 3),
        "seniority": data.get("seniority", ""),
        "years_of_experience": int(years) if years else None,
        "summary": data.get("summary", ""),
        "total_skills_found": len(skills),
        "skills": skills,
        "skill_groups": data.get("skill_groups", []),
        "department_breakdown": breakdown,
        "education": data.get("education", []),
        "certifications": data.get("certifications", []),
        "employers": data.get("employers", []),
        "strengths": data.get("strengths", []),
        "gaps": data.get("gaps", []),
    }
