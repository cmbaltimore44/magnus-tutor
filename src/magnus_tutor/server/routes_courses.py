"""Profile and courses."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import courses as C
from ..config import write_yaml_atomic

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


def course_json(c: C.Course) -> dict:
    return {"slug": c.slug, "name": c.name, **c.to_yaml(), "is_writing": c.is_writing}


@router.get("/profile")
async def get_profile(request: Request):
    p = st(request).p
    return {"profile": C.load_profile(p), "text": C.profile_text(C.load_profile(p)), "path": str(p.profile)}


@router.put("/profile")
async def put_profile(request: Request):
    data = await request.json()
    write_yaml_atomic(st(request).p.profile, data)
    return await get_profile(request)


@router.get("/courses")
async def list_courses(request: Request, archived: bool = False):
    return [course_json(c) for c in C.list_courses(st(request).p, include_archived=archived)]


@router.get("/courses/{slug}")
async def get_course(slug: str, request: Request):
    c = C.load_course(slug, st(request).p)
    if not c:
        raise HTTPException(404, "no such course")
    return course_json(c)


@router.post("/courses")
async def create_course(request: Request):
    d = await request.json()
    if not d.get("title"):
        raise HTTPException(400, "title is required")
    allowed = {k: d[k] for k in ("description", "topics", "notation_conventions", "schedule", "grading") if d.get(k)}
    c = C.create_course(
        d["title"], code=d.get("code", ""), short=d.get("short", ""), term=d.get("term", ""), instructor=d.get("instructor", ""),
        kind=d.get("kind") or [], languages=d.get("languages") or [], p=st(request).p, **allowed,
    )
    if st(request).extras.get("ingest"):
        st(request).extras["ingest"].refresh_watch()
    return course_json(c)


@router.patch("/courses/{slug}")
async def update_course(slug: str, request: Request):
    p = st(request).p
    c = C.load_course(slug, p)
    if not c:
        raise HTTPException(404, "no such course")
    d = await request.json()
    for k, v in d.items():
        # Folders and prompt-override paths are hand-edited in course.yaml only.
        if k in C.Course.__dataclass_fields__ and k not in ("slug", "folders", "prompt_overrides"):
            setattr(c, k, v)
    C.save_course(c, p)
    return course_json(c)


@router.post("/courses/archive-term")
async def archive_term(request: Request):
    d = await request.json()
    return {"archived": C.archive_term(d["term"], st(request).p)}
