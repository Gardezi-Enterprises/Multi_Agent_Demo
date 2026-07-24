"""FastAPI backend for Taseer's Agent.

Serves the JSON/SSE API and, in production, the built React SPA from ./static.
All business logic lives in the transport-agnostic `multi_agent_system` package;
this module is only the HTTP layer: auth, sessions, streaming and file transfer.
"""

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from multi_agent_system import accounts, auth, commands, config
from multi_agent_system.tools import email_tools
from multi_agent_system.config import (
    ALLOWED_UPLOAD_SUFFIXES,
    MAX_MESSAGE_CHARS,
    MAX_UPLOAD_BYTES,
    OUTPUT_DIR,
    RATE_LIMIT_TURNS,
    RATE_LIMIT_WINDOW,
    UPLOAD_DIR,
)
from multi_agent_system.db.database import init_db
from multi_agent_system.logging_config import get_logger, setup_logging
from multi_agent_system.sessions import store
from serving import (
    RateLimiter,
    ObservableTrace,
    build_message,
    downloads_from_trace,
    event_to_json,
)

log = get_logger("api")

SESSION_COOKIE = "mas_session"
AUTH_COOKIE = "mas_auth"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SAFE_NAME_TABLE = str.maketrans({c: "_" for c in '<>:"/\\|?*\0'})

STARTED_AT = time.time()
turn_limiter = RateLimiter(RATE_LIMIT_TURNS, RATE_LIMIT_WINDOW)
login_limiter = auth.AttemptLimiter(config.AUTH_MAX_ATTEMPTS, config.AUTH_LOCKOUT)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # The SPA bundle is same-origin; no external hosts are permitted.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


# --- lifespan ----------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    try:
        warnings = config.validate()
    except config.ConfigError as exc:
        log.error("Configuration error: %s", exc)
        raise SystemExit(1) from exc
    for warning in warnings:
        log.warning(warning)
    init_db()
    # Bootstrap the first admin from env if the accounts table is empty, so an
    # upgrade from the old env-only auth keeps working with no migration step.
    if config.AUTH_ENABLED:
        # Seed the sender address as the owner's recovery email so password
        # reset works out of the box on a fresh deployment.
        accounts.ensure_seed_from_env(
            config.AUTH_USERNAME, config.AUTH_PASSWORD_HASH, config.AUTH_PASSWORD,
            email=config.EMAIL_FROM,
        )
    log.info("Taseer's Agent API ready")
    for key, value in config.summary().items():
        log.info("  %-22s %s", key, value)
    yield


app = FastAPI(title="Taseer's Agent", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


# --- auth plumbing -----------------------------------------------------------


def _cookie_kwargs(max_age: int) -> dict:
    return {
        "max_age": max_age, "httponly": True, "samesite": "lax",
        "secure": config.COOKIE_SECURE, "path": "/",
    }


def _issue_login(response: Response, account: dict) -> None:
    token = auth.issue_token(
        account["username"], config.SECRET_KEY, config.AUTH_MAX_AGE,
        salt=accounts.token_salt(account),
    )
    response.set_cookie(AUTH_COOKIE, token, **_cookie_kwargs(config.AUTH_MAX_AGE))


def current_account(request: Request) -> Optional[dict]:
    """Resolve the signed-in account from the auth cookie, or None."""
    if not config.AUTH_ENABLED:
        return {"username": "local", "is_admin": True}
    token = request.cookies.get(AUTH_COOKIE, "")
    username = auth.token_username(token)
    if not username:
        return None
    account = accounts.get(username)
    if not account:
        return None
    verified = auth.read_token(token, config.SECRET_KEY, salt=accounts.token_salt(account))
    return account if verified else None


def require_user(request: Request) -> dict:
    account = current_account(request)
    if account is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return account


def require_admin(account: dict = Depends(require_user)) -> dict:
    if not account.get("is_admin"):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return account


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def signup_open() -> bool:
    """First account bootstraps freely; after that only if explicitly opened."""
    return accounts.count() == 0 or config.AUTH_ALLOW_SIGNUP


def get_session(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE)
    session = store.get_or_create(sid)
    if session.id != sid:
        response.set_cookie(SESSION_COOKIE, session.id, **_cookie_kwargs(86400))
    return session


def rotate_session(request: Request, response: Response) -> None:
    """Drop any existing conversation session and start a fresh one.

    Called on login/signup so a new user never inherits the previous user's
    conversation memory on a shared browser.
    """
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        store.drop(old)
    fresh = store.get_or_create()
    response.set_cookie(SESSION_COOKIE, fresh.id, **_cookie_kwargs(86400))


# --- models ------------------------------------------------------------------


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SignupBody(Credentials):
    email: str = ""
    code: str = ""


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class NewAccount(Credentials):
    email: str = ""
    is_admin: bool = False


class EmailUpdate(BaseModel):
    email: str = Field(max_length=254)


class ForgotBody(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)


class ResetBody(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class ChatBody(BaseModel):
    message: str = ""
    files: list[str] = []


# --- health & meta -----------------------------------------------------------


@app.get("/api/health")
def health(request: Request):
    try:
        warnings = config.validate(require_model=False)
        ok = True
    except config.ConfigError as exc:
        warnings, ok = [str(exc)], False
    payload = {"status": "ok" if ok else "degraded",
               "uptime_seconds": round(time.time() - STARTED_AT, 1)}
    # Operator detail only for signed-in callers; probes get liveness alone.
    if current_account(request) is not None:
        payload["config"] = config.summary()
        payload["sessions"] = store.stats()
        payload["warnings"] = warnings
    return JSONResponse(payload, status_code=200 if ok else 503)


@app.get("/api/meta")
def meta(_: dict = Depends(require_user)):
    return {"commands": commands.as_json()}


# --- authentication ----------------------------------------------------------


@app.get("/api/auth/me")
def whoami(request: Request):
    # first_run tells the UI this signup will create the owner (admin) account.
    first_run = accounts.count() == 0
    account = current_account(request)
    if account is None:
        return {"authenticated": False, "signup_open": signup_open(), "first_run": first_run}
    return {
        "authenticated": True,
        "username": account["username"],
        "is_admin": bool(account.get("is_admin")),
        "email": account.get("email") or "",
        "signup_open": signup_open(),
        "first_run": first_run,
    }


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    key = client_ip(request)
    blocked, retry = login_limiter.blocked(key)
    if blocked:
        raise HTTPException(429, f"Too many attempts. Try again in {retry} seconds.")
    account = accounts.verify(body.username, body.password)
    if account is None:
        login_limiter.record_failure(key)
        log.warning("failed login for '%s' from %s", body.username[:40], key)
        raise HTTPException(401, "Incorrect username or password.")
    login_limiter.clear(key)
    _issue_login(response, account)
    rotate_session(request, response)  # fresh conversation for the new sign-in
    log.info("login: '%s' from %s", account["username"], key)
    return {"username": account["username"], "is_admin": bool(account["is_admin"])}


@app.post("/api/auth/signup")
def signup(body: SignupBody, request: Request, response: Response):
    if not signup_open():
        raise HTTPException(403, "Sign-ups are closed. Ask an administrator for an account.")
    first = accounts.count() == 0
    # A configured code is required for non-bootstrap public sign-ups.
    if not first and config.AUTH_SIGNUP_CODE:
        if not secrets.compare_digest(body.code or "", config.AUTH_SIGNUP_CODE):
            raise HTTPException(403, "Invalid sign-up code.")
    result = accounts.create(body.username, body.password, is_admin=first, email=body.email)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    account = accounts.get(body.username)
    _issue_login(response, account)
    rotate_session(request, response)  # fresh conversation for the new account
    log.info("signup: '%s'%s", account["username"], " (first admin)" if first else "")
    return {"username": account["username"], "is_admin": bool(account["is_admin"])}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    # Drop the conversation session too, so nothing carries to the next sign-in.
    old = request.cookies.get(SESSION_COOKIE)
    if old:
        store.drop(old)
    response.delete_cookie(AUTH_COOKIE, path="/")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.post("/api/auth/password")
def change_password(body: PasswordChange, response: Response,
                    account: dict = Depends(require_user)):
    if accounts.verify(account["username"], body.current_password) is None:
        raise HTTPException(403, "Current password is incorrect.")
    result = accounts.set_password(account["username"], body.new_password)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    # Re-issue against the new hash so this session survives and older ones die.
    _issue_login(response, accounts.get(account["username"]))
    return {"status": "ok"}


@app.post("/api/auth/email")
def set_recovery_email(body: EmailUpdate, account: dict = Depends(require_user)):
    result = accounts.set_email(account["username"], body.email)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    return {"status": "ok", "email": result["email"]}


# --- password reset (emailed link) ------------------------------------------


def _reset_base_url(request: Request) -> str:
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    # Honour a reverse proxy's forwarded scheme/host, else the request's own.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@app.post("/api/auth/forgot")
def forgot_password(body: ForgotBody, request: Request):
    key = client_ip(request)
    blocked, retry = login_limiter.blocked(key)
    if blocked:
        raise HTTPException(429, f"Too many attempts. Try again in {retry} seconds.")

    ident = body.identifier.strip()
    account = accounts.get(ident) or accounts.get_by_email(ident)
    # Only actually send when the account exists AND has a recovery email, but
    # always respond the same so the endpoint never reveals which accounts or
    # emails exist.
    if account and account.get("email"):
        login_limiter.record_failure(key)  # rate-limit even successful lookups
        token = auth.issue_reset_token(
            account["username"], config.SECRET_KEY, config.RESET_TOKEN_TTL,
            accounts.token_salt(account),
        )
        link = f"{_reset_base_url(request)}/reset?token={token}"
        minutes = config.RESET_TOKEN_TTL // 60
        body_text = (
            f"Hello {account['username']},\n\n"
            "We received a request to reset your password for Taseer's Agent.\n"
            f"Open this link to choose a new password (valid for {minutes} minutes):\n\n"
            f"{link}\n\n"
            "If you did not request this, you can ignore this email — your password "
            "will not change."
        )
        try:
            email_tools.send_email(
                recipient=account["email"],
                subject="Reset your Taseer's Agent password",
                body=body_text,
                signature="Taseer's Agent",
            )
            log.info("password reset link sent for '%s'", account["username"])
        except Exception:
            log.error("failed to send reset email for '%s'", account["username"], exc_info=True)
    else:
        log.info("password reset requested for unknown/emailless identifier")

    return {"status": "ok",
            "message": "If an account with a recovery email matches, a reset link has been sent."}


@app.post("/api/auth/reset")
def reset_password(body: ResetBody, response: Response):
    username = auth.token_username(body.token)
    account = accounts.get(username) if username else None
    if account is None:
        raise HTTPException(400, "This reset link is invalid or has expired.")
    verified = auth.read_reset_token(body.token, config.SECRET_KEY, accounts.token_salt(account))
    if verified is None:
        raise HTTPException(400, "This reset link is invalid or has expired.")

    result = accounts.set_password(account["username"], body.new_password)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    # Changing the password rotates the salt, so this token and any old sessions
    # are now dead. Sign the user in fresh.
    _issue_login(response, accounts.get(account["username"]))
    log.info("password reset completed for '%s'", account["username"])
    return {"status": "ok", "username": account["username"]}


# --- admin: account management ----------------------------------------------


@app.get("/api/accounts")
def list_accounts(_: dict = Depends(require_admin)):
    return {"accounts": accounts.list_accounts()}


@app.post("/api/accounts")
def add_account(body: NewAccount, admin: dict = Depends(require_admin)):
    result = accounts.create(body.username, body.password, is_admin=body.is_admin, email=body.email)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    log.info("admin '%s' created account '%s'", admin["username"], body.username)
    return result["account"]


@app.delete("/api/accounts/{username}")
def remove_account(username: str, admin: dict = Depends(require_admin)):
    if username.lower() == admin["username"].lower():
        raise HTTPException(400, "You cannot delete your own account.")
    result = accounts.delete(username)
    if result["status"] != "success":
        raise HTTPException(400, result["message"])
    log.info("admin '%s' deleted account '%s'", admin["username"], username)
    return {"status": "ok"}


# --- reset -------------------------------------------------------------------


@app.post("/api/reset")
def reset(request: Request, response: Response, _: dict = Depends(require_user)):
    session = get_session(request, response)
    store.reset(session.id)
    return {"status": "ok"}


# --- files -------------------------------------------------------------------


@app.post("/api/upload")
async def upload(request: Request, response: Response,
                 file: UploadFile = File(...), _: dict = Depends(require_user)):
    get_session(request, response)
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024*1024)} MB limit.")
    if not raw:
        raise HTTPException(400, "The uploaded file is empty.")

    original = Path(file.filename or "upload").name
    suffix = Path(original).suffix.lower()
    if ALLOWED_UPLOAD_SUFFIXES and suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            415, f"'{suffix or 'no extension'}' is not accepted. "
            f"Allowed: {', '.join(sorted(ALLOWED_UPLOAD_SUFFIXES))}.")

    safe = original.translate(SAFE_NAME_TABLE).strip("._") or "upload"
    target = UPLOAD_DIR / safe
    stem, n = target.stem, 1
    while target.exists():
        target = UPLOAD_DIR / f"{stem}_{n}{suffix}"
        n += 1
    target.write_bytes(raw)

    from multi_agent_system.documents import extract_text
    from serving import size_label
    _, error = extract_text(str(target))
    log.info("upload %s (%s)", target.name, size_label(target))
    return {"name": target.name, "path": str(target), "size": size_label(target),
            "readable": not error, "note": error or ""}


@app.get("/api/download")
def download(f: str, request: Request, _: dict = Depends(require_user)):
    target = (OUTPUT_DIR / Path(f).name).resolve()
    try:
        target.relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "Forbidden")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    # Optional friendly download name (?as=resume.docx), sanitised to a bare
    # filename so it cannot inject header content or path separators.
    requested = request.query_params.get("as", "")
    download_name = Path(requested).name if requested else target.name
    download_name = "".join(c for c in download_name if c.isprintable() and c not in '"\\/') or target.name
    return FileResponse(target, filename=download_name, media_type="application/octet-stream")


# --- chat (SSE) --------------------------------------------------------------


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


@app.post("/api/chat/stream")
async def chat_stream(body: ChatBody, request: Request, response: Response,
                      account: dict = Depends(require_user)):
    session = get_session(request, response)
    message = (body.message or "").strip()
    files = [f for f in (body.files or []) if f]
    if not message and not files:
        raise HTTPException(400, "Message is empty.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(413, f"Message exceeds {MAX_MESSAGE_CHARS} characters.")
    if not turn_limiter.allow(session.id):
        raise HTTPException(
            429, f"Rate limit reached ({RATE_LIMIT_TURNS} messages per "
            f"{RATE_LIMIT_WINDOW}s). Please wait a moment.")

    loop = asyncio.get_running_loop()
    bridge: asyncio.Queue = asyncio.Queue()
    result: dict = {}
    started = time.monotonic()

    # The agent turn is synchronous and runs in a worker thread; its trace
    # callback hands events back to the event loop thread-safely.
    def emit(item):
        loop.call_soon_threadsafe(bridge.put_nowait, item)

    trace = ObservableTrace(lambda e: emit(("trace", e)))

    def run_turn():
        try:
            prompt = build_message(message, files)
            with session.lock:
                session.turns += 1
                result["answer"] = session.agent.chat(prompt, trace=trace)
        except Exception as exc:  # noqa: BLE001 - reported to the client generically
            result["error"] = str(exc)
            log.error("chat turn failed session=%s", session.id[:8], exc_info=True)
        finally:
            emit(("done", None))

    async def stream():
        yield _sse({"type": "start"})
        worker = loop.run_in_executor(None, run_turn)
        try:
            while True:
                try:
                    kind, event = await asyncio.wait_for(bridge.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield _sse({"type": "ping"})  # keep proxies from timing out
                    continue
                if kind == "done":
                    break
                if event.kind != "final":
                    yield _sse({"type": "trace", "event": event_to_json(event)})
            await worker
            if "error" in result:
                yield _sse({"type": "error",
                            "error": "The assistant could not complete this request.",
                            "error_id": secrets.token_hex(6)})
            else:
                yield _sse({"type": "done", "answer": result.get("answer", ""),
                            "trace": [event_to_json(e) for e in trace if e.kind != "final"],
                            "downloads": downloads_from_trace(trace)})
        finally:
            await worker  # ensure the turn finishes even if the client disconnects
        log.info("chat session=%s turns=%d %dms", session.id[:8], session.turns,
                 int((time.monotonic() - started) * 1000))

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- static SPA (mounted last so /api wins) ----------------------------------


if STATIC_DIR.is_dir():
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SpaStatic(StaticFiles):
        """Serve built assets; fall back to index.html for client-side routes.

        Starlette's StaticFiles *raises* 404 for a missing path rather than
        returning it, so the SPA fallback is done in an except handler.
        """

        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404 and not path.startswith("api"):
                    return await super().get_response("index.html", scope)
                raise

    app.mount("/", SpaStatic(directory=str(STATIC_DIR), html=True), name="spa")
else:
    @app.get("/")
    def dev_root():
        return {"status": "ok", "note": "Frontend not built. Run the Vite dev server "
                "(npm run dev in frontend/) or `npm run build`.",
                "docs": "/api/docs"}
