"""FastAPI surface: /chat for the UI, /a2a + agent card for other agents, /runs for the trace view,
and the built React app served at / when web/dist exists."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .a2a import agent_card, extract_text, jsonrpc_error, task_from_result
from .analytics import SiteAnalytics
from .config import ROOT, Settings
from .limits import Verdict, VisitorLimiter, bearer_ok, client_ip
from .service import Copilot
from .service import runs as runs_table

log = logging.getLogger("bicopilot")
WEB_DIST = ROOT / "web" / "dist"
ADMIN_HTML = ROOT / "admin" / "index.html"


class VisitRequest(BaseModel):
    visitor_id: str = Field(min_length=12, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    session_id: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    path: str = Field(default="/", max_length=160)
    referrer: str = Field(default="direct", max_length=200)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    pending_question: str = ""
    history: list[dict[str, str]] = Field(default_factory=list)
    session_id: str = ""


def create_app(settings: Settings | None = None, copilot: Copilot | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.copilot = copilot or Copilot(settings)
        app.state.analytics = SiteAnalytics(app.state.copilot.engine, settings.secret_salt, runs_table)
        yield

    app = FastAPI(title="bi-copilot", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"] if "*" in origins else origins, allow_methods=["*"], allow_headers=["*"]
    )
    limiter = VisitorLimiter(settings.visitor_per_minute, settings.visitor_daily_limit, settings.secret_salt)

    def admit(request: Request) -> Verdict:
        """Visitor and site-wide limits for the two public question endpoints."""
        verdict = limiter.check(client_ip(request, settings.trusted_proxy_hops))
        if not verdict.allowed:
            return verdict
        c: Copilot = request.app.state.copilot
        if settings.global_daily_limit > 0 and c.model_answers_today() >= settings.global_daily_limit:
            return Verdict(
                False, 3600, "site_limit", "The copilot has reached its daily limit. Please come back tomorrow."
            )
        return verdict

    def refuse(verdict: Verdict) -> JSONResponse:
        body: dict[str, Any] = {"detail": verdict.detail, "code": verdict.code}
        if verdict.left_today is not None:
            body["questions_left"] = verdict.left_today
        return JSONResponse(body, status_code=429, headers={"Retry-After": str(verdict.retry_after)})

    def require_admin(request: Request) -> None:
        if not settings.admin_token or not bearer_ok(request, settings.admin_token):
            raise HTTPException(401, "admin token required")


    @app.get("/admin", include_in_schema=False)
    def admin_page() -> FileResponse:
        if not settings.admin_token:
            raise HTTPException(503, "ADMIN_TOKEN must be configured to enable the dashboard")
        return FileResponse(
            ADMIN_HTML,
            headers={
                "Cache-Control": "no-store",
                "X-Robots-Tag": "noindex, nofollow",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            },
        )

    @app.post("/analytics/visit", status_code=204)
    def record_visit(data: VisitRequest, request: Request) -> Response:
        # A visit is a public page load, not a confirmed distinct human.
        # Accept same-site requests only; cross-site visitor beacons cannot read responses.
        origin = request.headers.get("origin", "")
        expected = str(request.base_url).rstrip("/")
        if origin and origin != expected and origin not in origins:
            raise HTTPException(403, "origin not allowed")
        try:
            request.app.state.analytics.record_visit(
                data.visitor_id,
                data.session_id,
                data.path,
                data.referrer,
            )
        except Exception:
            log.exception("failed to record visit")
        return Response(status_code=204)

    @app.get("/admin/api/overview")
    def admin_overview(request: Request, days: int = 30):
        require_admin(request)
        if days not in (7, 30, 90):
            raise HTTPException(422, "days must be 7, 30, or 90")
        return request.app.state.analytics.summary(days)

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        c: Copilot = request.app.state.copilot
        return {
            "ok": True,
            "as_of": c.as_of,
            "llm": c.llm.name,
            "vector_backend": c.retriever.backend,
            "reranker": c.retriever.reranker.name,
            "cache": c.cache.name,
            "dialect": c.engine.dialect.name,
        }

    @app.post("/chat")
    def chat(req: ChatRequest, request: Request):
        c: Copilot = request.app.state.copilot
        verdict = admit(request)
        if not verdict.allowed:
            return refuse(verdict)
        try:
            result = c.ask(
                req.question, pending_question=req.pending_question, history=req.history[-6:], session_id=req.session_id
            )
        except Exception as exc:  # the graph has its own error paths; this is the last resort
            log.exception("chat failed")
            raise HTTPException(500, f"copilot failed: {exc}") from exc
        if verdict.left_today is not None:
            result["questions_left"] = verdict.left_today
        return result

    @app.get("/catalog")
    def catalog(request: Request) -> dict[str, Any]:
        c: Copilot = request.app.state.copilot
        return {
            "metrics": [
                {
                    "id": k,
                    "label": v["label"],
                    "description": v["description"],
                    "format": v.get("format"),
                    "aliases": v.get("aliases", []),
                }
                for k, v in c.semantic.metrics.items()
            ],
            "dimensions": [
                {"id": k, "column": v["column"], "aliases": v.get("aliases", []), "values": v.get("values", [])}
                for k, v in c.semantic.dimensions.items()
            ],
            "rules": [r["text"] for r in c.semantic.rules],
            "as_of": c.as_of,
        }

    @app.get("/runs")
    def runs(request: Request, limit: int = 50) -> list[dict[str, Any]]:
        require_admin(request)
        c: Copilot = request.app.state.copilot
        return [{k: v for k, v in r.items() if k not in ("trace", "plan")} for r in c.recent_runs(min(limit, 200))]

    @app.get("/runs/{run_id}")
    def run(run_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        c: Copilot = request.app.state.copilot
        row = c.run(run_id)
        if not row:
            raise HTTPException(404, "run not found")
        return row

    # ----- A2A ------------------------------------------------------------------------------
    @app.get("/.well-known/agent.json")
    def card() -> dict[str, Any]:
        return agent_card(settings)

    @app.post("/a2a")
    async def a2a(request: Request) -> JSONResponse:
        c: Copilot = request.app.state.copilot
        verdict = admit(request)
        if not verdict.allowed:
            return JSONResponse(
                jsonrpc_error(None, -32000, verdict.detail),
                status_code=429,
                headers={"Retry-After": str(verdict.retry_after)},
            )
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(jsonrpc_error(None, -32700, "parse error"), status_code=400)
        req_id = body.get("id")
        if body.get("jsonrpc") != "2.0" or body.get("method") != "message/send":
            return JSONResponse(
                jsonrpc_error(req_id, -32601, "method not found; supported: message/send"), status_code=404
            )
        params = body.get("params") or {}
        message = params.get("message") or {}
        text = extract_text(message)
        if not text:
            return JSONResponse(jsonrpc_error(req_id, -32602, "message has no text part"), status_code=400)
        meta = message.get("metadata") or {}
        result = c.ask(
            text,
            pending_question=meta.get("pending_question", ""),
            session_id=f"a2a:{message.get('contextId', '')}"[:64],
        )
        task = task_from_result(
            result, task_id=params.get("id") or message.get("taskId"), context_id=message.get("contextId")
        )
        return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": task})

    # ----- UI ---------------------------------------------------------------------------------
    app.mount("/admin", StaticFiles(directory=ROOT / "admin"), name="admin-assets")
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")
        demo_dir = WEB_DIST / "demo"
        if demo_dir.exists():
            app.mount("/demo", StaticFiles(directory=demo_dir), name="demo")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIST / "index.html")

    else:

        @app.get("/")
        def index_missing() -> dict[str, str]:
            return {"hint": "UI not built. Run `cd web && npm install && npm run build`, or use POST /chat."}

    return app


def run_server() -> None:  # pragma: no cover
    import uvicorn

    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


app = create_app()  # for `uvicorn bicopilot.api:app`
