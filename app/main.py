import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api import assistant, auth, dashboard, habits, news, profile, reviews, tasks
from app.core.config import get_settings
from app.core.database import init_db
from app.core.security import current_user

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs are noisy and not needed
log = logging.getLogger("fenris")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.enable_scheduler:
        from app.scheduler import jobs
        jobs.start()
    yield
    if settings.enable_scheduler:
        from app.scheduler import jobs
        jobs.scheduler.shutdown(wait=False)


app = FastAPI(title="Fenris", lifespan=lifespan,
              docs_url="/docs" if settings.enable_docs else None, redoc_url=None,
              openapi_url="/openapi.json" if settings.enable_docs else None)
app.include_router(auth.router)  # public: register / login
for r in (tasks, habits, news, dashboard, assistant, reviews, profile):
    app.include_router(r.router, dependencies=[Depends(current_user)])  # everything else needs a login


@app.middleware("http")
async def security_layer(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):  # CSRF defence in depth (cookie is also SameSite=Lax)
        origin = request.headers.get("origin")
        if origin:
            hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
            if urlparse(origin).netloc not in hosts:
                return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = CSP
    if request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Internal error. Check the server log."}, status_code=500)


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
