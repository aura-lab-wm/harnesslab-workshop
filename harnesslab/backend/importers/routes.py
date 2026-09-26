"""FastAPI routes for the external-harness importer.

    POST /api/import           {path, results_dir, source?, model?}  -> starts a background import
    GET  /api/import/status                                          -> the current/last import
    GET  /api/import/sources                                         -> the adapter registry
    GET  /api/import/detect?path=                                    -> sniff a path

Progress is published on the same SSE bus `app.py` uses (`harnesslab.backend.app.BUS`),
imported **lazily inside the function** so this module never participates in an import
cycle with app.py (app.py appends `include_router` at its very end, so a top-level
`from ..app import BUS` here would be a cycle).

If you embed this router in a different application that has no such bus, set the
module-level callback instead and no import of app.py is attempted:

    from harnesslab.backend.importers import routes
    routes.PUBLISH = lambda kind, **data: my_bus.publish(kind, **data)

`PUBLISH` defaults to `None`, which means "find app.BUS lazily, and stay silent if it is
not there".
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import import_path, list_sources, detect_detail, RUNS_ROOT

router = APIRouter(prefix="/api/import")

#: Optional callback `(kind: str, **data) -> None`. Set it to bypass the app.py event bus.
PUBLISH = None

STATE: dict = {"status": "idle", "progress": None, "error": None, "result": None,
               "path": "", "results_dir": "", "source": "", "started_at": None, "finished_at": None,
               "history": []}
_LOCK = threading.Lock()


def _publish(kind: str, **data) -> None:
    """Publish on the caller-supplied callback, else on app.py's BUS, else nowhere."""
    if PUBLISH is not None:
        try:
            PUBLISH(kind, **data)
        except Exception:
            pass
        return
    try:
        from harnesslab.backend.app import BUS       # lazy: avoids a circular import at module load
    except Exception:
        return
    try:
        BUS.publish(kind, **data)
    except Exception:
        pass


#: Contention on the captured directory's single-writer lock is normal operation -- the watcher
#: mid-batch, or another import -- not a failure of this request. Its own message names the flock
#: file, which is an absolute path under the operator's home, and this payload is frozen verbatim
#: into `--export`; so the sentence the page and the export see is written here instead.
LOCKED_SENTENCE = ("another capture writer holds the captured directory -- the watcher mid-batch, "
                   "or another import. Wait for it, or pause the watcher, then try again.")


def _said(e: BaseException) -> str:
    """What the page shows for a failed import."""
    if type(e).__name__ == "CaptureLocked":
        return LOCKED_SENTENCE
    return f"{type(e).__name__}: {e}"


def hoist_api_routes(app) -> None:
    """Starlette matches routes in registration order and app.py registers the SPA catch-all
    `GET /{path:path}` before anything appended at the end of the file, so a router included
    afterwards is shadowed once `frontend/dist` exists. Stable-sort the catch-all to the end.
    Idempotent, and harmless when there is no catch-all."""
    app.router.routes.sort(key=lambda r: 1 if str(getattr(r, "path", "")) == "/{path:path}" else 0)


class ImportIn(BaseModel):
    path: str
    results_dir: str
    source: Optional[str] = None
    model: Optional[str] = None                # model_override: label every imported run as this model


@router.get("/sources")
def sources():
    """The adapters, with a one-line description and the file patterns each accepts."""
    return {"sources": list_sources(), "runs_root": RUNS_ROOT}


@router.get("/detect")
def detect_route(path: str):
    """Sniff a file or directory: the winning adapter, per-adapter confidence, session count."""
    return detect_detail(path)


@router.get("/status")
def status():
    return STATE


@router.post("")
@router.post("/")
def start(body: ImportIn):
    with _LOCK:
        if STATE["status"] == "running":
            raise HTTPException(409, "an import is already running")
        p = os.path.expanduser(body.path or "")
        if not p or not os.path.exists(p):
            raise HTTPException(400, f"no such path: {body.path}")
        rd = (body.results_dir or "").strip()
        if not rd or "/" in rd or rd.startswith("."):
            raise HTTPException(400, "bad results dir name")
        src = body.source or None
        if src is None:
            from . import detect
            src = detect(p) or None
            if src is None:
                raise HTTPException(400, "could not detect the trace format; pick a source explicitly "
                                         "(GET /api/import/sources lists them)")
        STATE.update(status="running", progress=[0, 0], error=None, result=None, path=p,
                     results_dir=rd, source=src, started_at=time.time(), finished_at=None)

    def work():
        _publish("import", status="running", out=rd, source=src)
        try:
            def prog(done, total, label):
                STATE["progress"] = [done, total]
                _publish("import", status="running", out=rd, source=src, progress=[done, total], label=label)
            res = import_path(p, rd, source=src, model_override=body.model or None, progress=prog)
            STATE.update(status="done", result=res, finished_at=time.time())
            STATE["history"] = ([{k: res[k] for k in ("source", "results_dir", "imported", "skipped",
                                                      "harness_ids", "n_tasks", "outcomes_known",
                                                      "outcomes_unknown")}] + STATE["history"])[:10]
        except Exception as e:
            STATE.update(status="error", error=_said(e), finished_at=time.time())
        _publish("import", status=STATE["status"], out=rd, source=src, result=STATE.get("result"),
                 error=STATE.get("error"))

    threading.Thread(target=work, daemon=True).start()
    return {"status": "running", "results_dir": rd, "source": src}
