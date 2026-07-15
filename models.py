import json
from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any, Union

class Education(BaseModel):
    degree: str = ""
    field_of_study: str = ""
    institution: str = ""
    start_year: str = ""
    end_year: str = ""

class Experience(BaseModel):
    job_title: str = ""
    company: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""

class Project(BaseModel):
    name: str = ""
    description: str = ""
    technologies: List[str] = Field(default_factory=list)

class Certification(BaseModel):
    name: str = ""
    issuer: str = ""
    year: str = ""

class Links(BaseModel):
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""

class Resume(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    summary: str = ""
    skills: List[str] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    links: Links = Field(default_factory=Links)


class ProjectBlock(BaseModel):
    project: str = ""
    period: str = ""
    bullets: List[str] = Field(default_factory=list)


class ExperienceV2(BaseModel):
    company: str = ""
    location: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    project_blocks: List[ProjectBlock] = Field(default_factory=list)


class EducationV2(BaseModel):
    degree: str = ""
    institution: str = ""
    period: str = ""


class ResumeV2(BaseModel):
    name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: Links = Field(default_factory=Links)
    summary: str = ""
    skills: Dict[str, str] = Field(default_factory=dict)
    experience: List[ExperienceV2] = Field(default_factory=list)
    education: List[EducationV2] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)


ResumeLike = Union[Resume, ResumeV2]


def _looks_like_resume_v2_payload(data: Dict[str, Any]) -> bool:
    skills = data.get("skills")
    if isinstance(skills, dict):
        return True

    experience = data.get("experience") or []
    if isinstance(experience, list):
        for item in experience:
            if not isinstance(item, dict):
                continue
            if "project_blocks" in item or ("title" in item and "job_title" not in item):
                return True

    education = data.get("education") or []
    if isinstance(education, list):
        for item in education:
            if isinstance(item, dict) and "period" in item:
                return True

    return False


def _skills_list_to_dict(skills: Any) -> Dict[str, str]:
    if isinstance(skills, dict):
        return {
            str(key).strip(): str(value).strip()
            for key, value in skills.items()
            if str(key).strip() and str(value).strip()
        }
    if not isinstance(skills, list):
        return {}

    grouped: Dict[str, str] = {}
    for raw_skill in skills:
        text = str(raw_skill or "").strip()
        if not text:
            continue
        if ":" in text:
            category, values = text.split(":", 1)
            category = category.strip()
            values = values.strip()
            if category and values:
                grouped[category] = values
        else:
            grouped.setdefault("Additional", "")
            grouped["Additional"] = ", ".join(
                part for part in [grouped["Additional"], text] if part
            )
    return grouped


def _normalize_resume_v2_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(data)
    normalized["title"] = str(
        normalized.get("title") or normalized.get("header_title") or ""
    ).strip()
    normalized["skills"] = _skills_list_to_dict(normalized.get("skills"))

    normalized_experience = []
    for item in normalized.get("experience") or []:
        if not isinstance(item, dict):
            continue
        item_copy = dict(item)
        item_copy["title"] = str(
            item_copy.get("title") or item_copy.get("job_title") or ""
        ).strip()
        item_copy["project_blocks"] = item_copy.get("project_blocks") or []
        normalized_experience.append(item_copy)
    normalized["experience"] = normalized_experience

    normalized_education = []
    for item in normalized.get("education") or []:
        if not isinstance(item, dict):
            continue
        item_copy = dict(item)
        if not item_copy.get("period"):
            start = str(item_copy.get("start_year") or "").strip()
            end = str(item_copy.get("end_year") or "").strip()
            item_copy["period"] = " - ".join(part for part in [start, end] if part)
        normalized_education.append(item_copy)
    normalized["education"] = normalized_education

    return normalized


def _normalize_legacy_resume_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(data)
    for key in ["skills", "experience", "education", "projects", "certifications", "languages"]:
        if normalized.get(key) is None:
            normalized[key] = []
    if normalized.get("links") is None:
        normalized["links"] = {}
    return normalized


def is_resume_v2_payload(data: Any) -> bool:
    return isinstance(data, dict) and _looks_like_resume_v2_payload(data)


def is_resume_v2_model(resume: Any) -> bool:
    return isinstance(resume, ResumeV2)


def parse_resume_data(data: Any) -> ResumeLike:
    if isinstance(data, (Resume, ResumeV2)):
        return data
    if not isinstance(data, dict):
        raise TypeError("Resume data must be a dictionary or resume model.")
    if _looks_like_resume_v2_payload(data):
        return ResumeV2.model_validate(_normalize_resume_v2_payload(data))
    return Resume.model_validate(_normalize_legacy_resume_payload(data))


def parse_resume_json_text(text: str) -> ResumeLike:
    return parse_resume_data(json.loads(text))

# --- Pydantic models for LLM structured output ---
class SummaryOutput(BaseModel):
    summary: str

class SkillsOutput(BaseModel):
    skills: List[str]

class ExperienceListOutput(BaseModel):
    experience: List[Experience]

class SingleExperienceOutput(BaseModel):
    experience: Experience

class ProjectListOutput(BaseModel):
    projects: List[Project]

class SingleProjectOutput(BaseModel):
    project: Project

class ValidationResponse(BaseModel):
    is_valid: bool
    reason: str


class ATSKeywordPlan(BaseModel):
    hard_skills: List[str] = Field(default_factory=list)
    soft_skills: List[str] = Field(default_factory=list)


class JobPostingIntakeOutput(BaseModel):
    is_job_posting: bool = True
    job_title: str = ""
    company: str = ""
    location: str = ""
    level: str = ""
    description: str = ""
    hard_skills: List[str] = Field(default_factory=list)
    soft_skills: List[str] = Field(default_factory=list)


class ATSResumeRewriteOutput(BaseModel):
    header_title: str = ""
    summary: str
    skills: List[str] = Field(default_factory=list)
    experience: List[Experience] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)


class ResumeV2ChangeLogItem(BaseModel):
    type: str = ""
    before: Optional[str] = None
    after: Optional[str] = None
    reason: str = ""


class ResumePatchBulletEdit(BaseModel):
    bullet_id: str
    new_text: str
    edit_reason: str = ""


class ResumePatchBulletDrop(BaseModel):
    bullet_id: str
    drop_reason: str = ""


class ResumePatchProjectBlockReorder(BaseModel):
    experience_id: str
    new_bullet_order: List[str]


class ResumePatchSkillCategoryReorder(BaseModel):
    category: str
    new_skill_order: List[str]


class ResumePatchExperienceProjectReorder(BaseModel):
    experience_id: str
    new_project_block_order: List[str]


class ResumePatch(BaseModel):
    target_role_family: str = ""
    summary_rewrite: Optional[str] = None
    skill_category_reorders: List[ResumePatchSkillCategoryReorder] = Field(default_factory=list)
    experience_project_reorders: List[ResumePatchExperienceProjectReorder] = Field(default_factory=list)
    project_bullet_reorders: List[ResumePatchProjectBlockReorder] = Field(default_factory=list)
    bullet_edits: List[ResumePatchBulletEdit] = Field(default_factory=list)
    bullet_drops: List[ResumePatchBulletDrop] = Field(default_factory=list)
    keyword_coverage_notes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class CoverLetterOutput(BaseModel):
    cover_letter: str

class Config:
    extra = 'allow'
