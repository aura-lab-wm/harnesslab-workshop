"""HTTP choke point for private results directories (captured sessions).

A request that names a private results directory — as a path segment, or as the value of a query
parameter that names a results directory (DIR_PARAMS) — is answered 404 on any route unless it opts
in with the `X-Harnesslab-Private: 1` header. One middleware instead of a check in every router
means a route added later is covered automatically.
A custom header cannot be sent cross-origin without a CORS preflight, and this app grants none, so a
web page cannot opt in on the user's behalf. JSON request bodies are not inspected (see the capture
spine Plan 2 notes).
"""
from __future__ import annotations

from typing import Callable

from fastapi.responses import JSONResponse

from .results_scope import is_private

OPT_IN_HEADER = "x-harnesslab-private"

# The query parameters that name a results directory: `dir` (harness_tools, sentinel_ext), `results`
# (field), `also` (the experiment's extra levels), `name` in case a future route takes it as a query.
# Only these are read, because a value like ?pattern=captured or ?outcome=captured names something
# else entirely and hiding those routes would be a bug, not a protection. `also` is comma-separated,
# so every value is split the same way -- harmless for the single-valued ones.
DIR_PARAMS = frozenset({"dir", "results", "also", "name"})


def private_results_guard(runs_root_of: Callable[[], str]):
    async def guard(request, call_next):
        if request.headers.get(OPT_IN_HEADER) != "1":
            root = runs_root_of()
            names = [seg for seg in request.url.path.split("/") if seg]
            names += [part.strip() for key, value in request.query_params.multi_items()
                      if key in DIR_PARAMS for part in value.split(",")]
            if any(is_private(root, name) for name in names):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)
    return guard
