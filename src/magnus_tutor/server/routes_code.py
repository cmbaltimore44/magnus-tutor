"""Code workspace: languages and sandboxed runs. Runs are always started by the
student (the Run button); code the tutor suggests is shown in the editor first."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from .. import languages as L
from .. import sandbox

router = APIRouter(prefix="/api")


def st(request: Request):
    return request.app.state.tutor


@router.get("/languages")
async def languages(request: Request):
    p = st(request).p
    langs = L.load(p)
    py = sandbox.sandbox_python(p, create=False)
    out = []
    for key, spec in langs.items():
        ok = L.requirement_ok(spec, py) if "{python}" not in str(spec.get("requires", "")) else True
        out.append({"key": key, "highlight": spec.get("highlight", key), "mode": spec.get("mode", "run"), "ok": ok,
                    "filename": spec.get("filename"), "extensions": spec.get("extensions", [])})
    return out


@router.post("/code/run")
async def run(request: Request):
    body = await request.json()
    files = body.get("files") or {}
    if not files or sum(len(v) for v in files.values()) > 200_000:
        raise HTTPException(400, "no code, or more than 200 KB")
    s = st(request)
    backend = s.settings.get("sandbox", {}).get("backend", "subprocess")
    res = await asyncio.to_thread(
        sandbox.run, files, body.get("language"), stdin=body.get("stdin", ""), tests=body.get("tests") or None, p=s.p, backend=backend,
    )
    return {**res.as_dict(), "summary": res.summary()}


@router.post("/languages/check")
async def check(request: Request):
    return await asyncio.to_thread(sandbox.check_languages, st(request).p)
