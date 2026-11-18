"""StudentFlow — оқу үлгерімін есепке алатын FastAPI жүйесі.

SQLite деректері сақталады; versioned SQL migrations инициализируют схему при запуске.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from migrations import migrate_database

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("STUDENTFLOW_DB", str(BASE_DIR / "ак.db"))).resolve()
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
PASSWORDS = PasswordHasher()
APP_ENV = os.getenv("APP_ENV", "development").lower()
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").lower()
CSP_FRAME_ANCESTORS = os.getenv(
    "CSP_FRAME_ANCESTORS", "'self'" if APP_ENV == "production" else ""
).strip()
if any(char in CSP_FRAME_ANCESTORS for char in "\r\n;"):
    raise RuntimeError("CSP_FRAME_ANCESTORS contains invalid characters")
if APP_ENV == "production" and not CSP_FRAME_ANCESTORS:
    raise RuntimeError("CSP_FRAME_ANCESTORS must be set in production")
if COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise RuntimeError("COOKIE_SAMESITE must be lax, strict, or none")
if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
    raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=1")
ROLES = {"admin": "Әкімші", "teacher": "Оқытушы", "student": "Студент"}
LOGGER = logging.getLogger("studentflow")
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,40}\Z")
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 8
LOGIN_IP_MAX_ATTEMPTS = 30


def login_rate_key(scope: str, value: str) -> str:
    material = f"{scope}:{value}".encode()
    return hmac.new(SESSION_SECRET_KEY.encode("utf-8"), material, hashlib.sha256).hexdigest()


def login_rate_keys(request: Request, username: str) -> tuple[tuple[str, int], ...]:
    ip = request.client.host if request.client else "unknown"
    return (
        (login_rate_key("ip", ip), LOGIN_IP_MAX_ATTEMPTS),
        (login_rate_key("user", username), LOGIN_MAX_ATTEMPTS),
    )


def login_is_limited(request: Request, username: str) -> bool:
    now = int(time.time())
    with db() as conn:
        for key, maximum in login_rate_keys(request, username):
            row = conn.execute(
                "SELECT failures, window_started, blocked_until FROM auth_attempts WHERE rate_key = ?",
                (key,),
            ).fetchone()
            if row and row["blocked_until"] > now:
                return True
            if row and row["window_started"] + LOGIN_WINDOW_SECONDS <= now and row["failures"] >= maximum:
                conn.execute("DELETE FROM auth_attempts WHERE rate_key = ?", (key,))
    return False


def record_login_failure(request: Request, username: str) -> None:
    now = int(time.time())
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM auth_attempts WHERE window_started + ? <= ? AND blocked_until <= ?",
            (LOGIN_WINDOW_SECONDS, now, now),
        )
        for key, maximum in login_rate_keys(request, username):
            row = conn.execute(
                "SELECT failures, window_started FROM auth_attempts WHERE rate_key = ?", (key,)
            ).fetchone()
            if row is None or row["window_started"] + LOGIN_WINDOW_SECONDS <= now:
                failures, started = 1, now
            else:
                failures, started = row["failures"] + 1, row["window_started"]
            blocked_until = now + LOGIN_WINDOW_SECONDS if failures >= maximum else 0
            conn.execute("""
                INSERT INTO auth_attempts(rate_key, failures, window_started, blocked_until)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(rate_key) DO UPDATE SET
                    failures = excluded.failures,
                    window_started = excluded.window_started,
                    blocked_until = excluded.blocked_until
            """, (key, failures, started, blocked_until))


def clear_login_failures(username: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM auth_attempts WHERE rate_key = ?", (login_rate_key("user", username),))


def session_secret() -> str:
    """Для localhost секрет сохраняется в файле; в production нужен SESSION_SECRET."""
    provided = os.getenv("SESSION_SECRET")
    if os.getenv("APP_ENV", "development").lower() == "production":
        if not provided:
            raise RuntimeError("SESSION_SECRET must be configured in production")
        if not COOKIE_SECURE:
            raise RuntimeError("COOKIE_SECURE=1 is required in production")
    if provided:
        if len(provided) < 32:
            raise RuntimeError("SESSION_SECRET must be at least 32 characters")
        return provided
    secret_file = BASE_DIR / ".session_secret"
    try:
        descriptor = os.open(str(secret_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(secrets.token_hex(32))
    return secret_file.read_text(encoding="utf-8").strip()


SESSION_SECRET_KEY = session_secret()
DUMMY_PASSWORD_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate_database(DB_PATH)
    yield


app = FastAPI(title="StudentFlow — Студенттерді есепке алу жүйесі", docs_url="/api/docs", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="studentflow_session",
    max_age=60 * 60 * 8,
    same_site=COOKIE_SAMESITE,
    https_only=COOKIE_SECURE,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    frame_policy = f"; frame-ancestors {CSP_FRAME_ANCESTORS}" if CSP_FRAME_ANCESTORS else ""
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'" + frame_policy,
    )
    if COOKIE_SECURE:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"База данных не найдена: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def auth_stamp(password_hash: str) -> str:
    return hmac.new(
        SESSION_SECRET_KEY.encode("utf-8"), password_hash.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def current_user(request: Request) -> dict | None:
    uid = request.session.get("uid")
    if not isinstance(uid, int):
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT id, username, full_name, role, password_hash FROM users WHERE id = ?", (uid,)
        ).fetchone()
    if row is None or not hmac.compare_digest(
        str(request.session.get("auth_stamp", "")), auth_stamp(row["password_hash"])
    ):
        request.session.clear()
        return None
    return {key: row[key] for key in ("id", "username", "full_name", "role")}


def require_role(request: Request, *allowed: str) -> dict:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401)
    if user["role"] not in allowed:
        raise HTTPException(status_code=403, detail="Бұл бөлімге кіруге рұқсатыңыз жоқ")
    return user


def csrf(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def check_csrf(request: Request, posted: str) -> None:
    stored = request.session.get("csrf", "")
    if not stored or not hmac.compare_digest(str(stored), posted):
        raise HTTPException(status_code=403, detail="Қауіпсіздік тексеруі өтпеді. Бетті жаңартыңыз")


def render(request: Request, page: str, *, status_code: int = 200, **context):
    user = current_user(request)
    flash = request.session.pop("flash", None)
    return TEMPLATES.TemplateResponse(
        request=request,
        name=page,
        context={
            "user": user,
            "roles": ROLES,
            "csrf_token": csrf(request),
            "flash": flash,
            **context,
        },
        status_code=status_code,
    )


def go(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def notify(request: Request, path: str, message: str, kind: str = "success") -> RedirectResponse:
    request.session["flash"] = {"text": message, "kind": kind}
    return go(path)


def fail(request: Request, path: str, message: str) -> RedirectResponse:
    return notify(request, path, message, kind="error")


def clean(value: str, max_length: int = 100) -> str:
    value = " ".join(value.strip().split())
    if not value or len(value) > max_length:
        raise ValueError(f"Мәтін 1–{max_length} таңбадан тұруы керек")
    return value


def valid_username(value: str) -> str:
    value = value.strip().lower()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("Логин: 3–40 таңба, тек латын әріптері, сандар, '.', '_' және '-'")
    return value


def valid_password(value: str, *, optional: bool = False) -> str | None:
    if optional and not value:
        return None
    if len(value) < 8 or len(value) > 200:
        raise ValueError("Құпиясөз 8–200 таңбадан тұруы керек")
    return PASSWORDS.hash(value)


def valid_score(value: int) -> int:
    if not 0 <= value <= 100:
        raise ValueError("Баға 0 мен 100 арасында болуы керек")
    return value


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        return go("/login")
    return render(
        request,
        "error.html",
        status_code=exc.status_code,
        code=exc.status_code,
        message=str(exc.detail or "Қате орын алды"),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    LOGGER.exception("Unhandled error on %s", request.url.path)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="error.html",
        context={
            "user": None,
            "roles": ROLES,
            "csrf_token": csrf(request),
            "flash": None,
            "code": 500,
            "message": "Ішкі қате орын алды. Біраздан кейін қайта көріңіз.",
        },
        status_code=500,
    )


@app.get("/")
def home(request: Request):
    return go("/dashboard" if current_user(request) else "/login")


@app.get("/healthz", include_in_schema=False)
def healthcheck():
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
    except (sqlite3.Error, FileNotFoundError):
        LOGGER.exception("Health check failed")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}


@app.get("/login")
def login_page(request: Request):
    if current_user(request):
        return go("/dashboard")
    return render(request, "login.html")


@app.get("/register")
def register_page(request: Request):
    if current_user(request):
        return go("/dashboard")
    return render(request, "register.html")


@app.post("/register")
def register_submit(
    request: Request,
    username: str = Form(""),
    full_name: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    group_name: str = Form(""),
    csrf_token: str = Form(""),
):
    if current_user(request):
        return go("/dashboard")
    check_csrf(request, csrf_token)
    try:
        username = valid_username(username)
        full_name = clean(full_name)
        if password != password_confirm:
            raise ValueError("Құпиясөздер сәйкес келмейді")
        password_hash = valid_password(password)
        group_name = clean(group_name, 40) if group_name.strip() else "Топ көрсетілмеген"
        with db() as conn:
            cursor = conn.execute(
                "INSERT INTO users(username, full_name, password_hash, role) VALUES(?, ?, ?, ?)",
                (username, full_name, password_hash, "student"),
            )
            conn.execute(
                "INSERT INTO students(user_id, group_name) VALUES(?, ?)",
                (cursor.lastrowid, group_name),
            )
            user_id = cursor.lastrowid
    except ValueError as exc:
        return fail(request, "/register", str(exc))
    except sqlite3.IntegrityError:
        return fail(request, "/register", "Бұл логин тіркелген. Басқа логин таңдаңыз")
    # Rotate the session on account creation, then sign the new student in.
    request.session.clear()
    request.session["uid"] = user_id
    request.session["auth_stamp"] = auth_stamp(password_hash)
    request.session["csrf"] = secrets.token_urlsafe(32)
    return notify(request, "/dashboard", "Тіркелу сәтті аяқталды. Қош келдіңіз!")


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    check_csrf(request, csrf_token)
    username = username.strip().lower()
    rate_key = username if USERNAME_PATTERN.fullmatch(username) else "<invalid>"
    if login_is_limited(request, rate_key):
        return fail(request, "/login", "Кіру әрекеті уақытша шектелді. 15 минуттан кейін қайталап көріңіз")
    verified = False
    row = None
    if len(username) <= 40 and len(password) <= 200:
        with db() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        candidate_hash = row["password_hash"] if row and row["password_hash"] != "TEST_ONLY" else DUMMY_PASSWORD_HASH
        try:
            verified = PASSWORDS.verify(candidate_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            verified = False
    if not verified:
        record_login_failure(request, rate_key)
        return fail(request, "/login", "Логин немесе құпиясөз дұрыс емес")
    clear_login_failures(rate_key)
    request.session.clear()
    request.session["uid"] = row["id"]
    request.session["auth_stamp"] = auth_stamp(row["password_hash"])
    request.session["csrf"] = secrets.token_urlsafe(32)
    return go("/dashboard")


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    check_csrf(request, csrf_token)
    request.session.clear()
    return go("/login")


@app.get("/account")
def account_page(request: Request):
    user = require_role(request, "admin", "teacher", "student")
    return render(request, "account.html", account=user)


@app.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    password_confirm: str = Form(""),
    csrf_token: str = Form(""),
):
    user = require_role(request, "admin", "teacher", "student")
    check_csrf(request, csrf_token)
    if new_password != password_confirm:
        return fail(request, "/account", "Жаңа құпиясөздер сәйкес келмейді")
    with db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    verified = False
    if row and row["password_hash"] != "TEST_ONLY":
        try:
            verified = PASSWORDS.verify(row["password_hash"], current_password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            verified = False
    if not verified:
        return fail(request, "/account", "Қазіргі құпиясөз дұрыс емес")
    if current_password == new_password:
        return fail(request, "/account", "Жаңа құпиясөз бұрынғысынан өзгеше болуы керек")
    try:
        new_hash = valid_password(new_password)
    except ValueError as exc:
        return fail(request, "/account", str(exc))
    with db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
    request.session.clear()
    return notify(request, "/login", "Құпиясөз жаңартылды. Қайта кіріңіз")


@app.get("/dashboard")
def dashboard(request: Request):
    user = require_role(request, "admin", "teacher", "student")
    return go("/" + user["role"])


# ─── ЗЕРТХАНАЛЫҚ ЖҰМЫС №10 ──────────────────────────────────────────────────
# Эти GET-страницы не изменяют SQLite. JavaScript работает только с учебным
# планом в localStorage; авторизация и роли по-прежнему проверяются сервером.
@app.get("/lab10")
def lab10_editor(request: Request):
    require_role(request, "admin", "teacher", "student")
    return render(request, "lab10_editor.html")


@app.get("/lab10/plan")
def lab10_plan(request: Request):
    require_role(request, "admin", "teacher", "student")
    return render(request, "lab10_plan.html")


@app.get("/lab10/map")
def lab10_map(request: Request):
    require_role(request, "admin", "teacher", "student")
    return render(request, "lab10_map.html")


# ─── ЗЕРТХАНАЛЫҚ ЖҰМЫС №12: ҚАУІПСІЗДІК МЕХАНИЗМДЕРІ ───────────────────────
@app.get("/lab12")
def lab12_security(request: Request):
    require_role(request, "admin", "teacher", "student")
    return render(request, "lab12.html", test_result=None, test_input="")


@app.post("/lab12/security-test")
def lab12_security_test(
    request: Request,
    search: str = Form(""),
    csrf_token: str = Form(""),
):
    require_role(request, "admin", "teacher", "student")
    check_csrf(request, csrf_token)
    search = search.strip()[:80]
    # The input is always a bound parameter; it is never interpolated into SQL.
    with db() as conn:
        result_count = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username LIKE ?", (f"%{search}%",)
        ).fetchone()[0]
    return render(
        request, "lab12.html", test_result=result_count, test_input=search
    )


# ─── ӘКІМШІ ──────────────────────────────────────────────────────────────────

@app.get("/admin")
def admin_dashboard(request: Request, q: str = ""):
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401)
    if user["role"] != "admin":
        return go("/dashboard")
    q = q.strip()[:80]
    pattern = f"%{q}%"
    with db() as conn:
        all_users = [dict(row) for row in conn.execute("SELECT role FROM users")]
        users = [dict(row) for row in conn.execute("""
            SELECT u.id, u.username, u.full_name, u.role, s.group_name
            FROM users u LEFT JOIN students s ON u.id = s.user_id
            WHERE (? = '' OR u.username LIKE ? OR u.full_name LIKE ?)
            ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'teacher' THEN 1 ELSE 2 END,
                     u.id
        """, (q, pattern, pattern))]
        subjects = [dict(row) for row in conn.execute("""
            SELECT sub.id, sub.name, sub.teacher_id, u.full_name AS teacher,
                   (SELECT COUNT(*) FROM grades g WHERE g.subject_id = sub.id) AS grade_count
            FROM subjects sub JOIN users u ON sub.teacher_id = u.id
            ORDER BY sub.id DESC
        """)]
        teachers = [dict(row) for row in conn.execute(
            "SELECT id, full_name FROM users WHERE role = 'teacher' ORDER BY full_name"
        )]
        grade_count = conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0]
    stats = {
        "users": len(all_users),
        "students": sum(u["role"] == "student" for u in all_users),
        "teachers": sum(u["role"] == "teacher" for u in all_users),
        "grades": grade_count,
    }
    return render(request, "admin.html", users=users, teachers=teachers, subjects=subjects, stats=stats, q=q)


@app.post("/admin/users")
def add_user(
    request: Request,
    username: str = Form(""),
    full_name: str = Form(""),
    role: str = Form(""),
    password: str = Form(""),
    group_name: str = Form(""),
    csrf_token: str = Form(""),
):
    require_role(request, "admin")
    check_csrf(request, csrf_token)
    try:
        username = valid_username(username)
        full_name = clean(full_name)
        if role not in ROLES:
            raise ValueError("Рөлді таңдаңыз")
        password_hash = valid_password(password)
        group_name = clean(group_name, 40) if role == "student" else ""
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO users(username, full_name, password_hash, role) VALUES(?, ?, ?, ?)",
                (username, full_name, password_hash, role),
            )
            if role == "student":
                conn.execute(
                    "INSERT INTO students(user_id, group_name) VALUES(?, ?)",
                    (cur.lastrowid, group_name),
                )
    except ValueError as exc:
        return fail(request, "/admin", str(exc))
    except sqlite3.IntegrityError:
        return fail(request, "/admin", "Мұндай логин бұрыннан бар")
    return notify(request, "/admin", "Жаңа пайдаланушы қосылды")


@app.get("/admin/users/{user_id}/edit")
def edit_user_page(request: Request, user_id: int):
    require_role(request, "admin")
    with db() as conn:
        row = conn.execute("""
            SELECT u.id, u.username, u.full_name, u.role, s.group_name
            FROM users u LEFT JOIN students s ON s.user_id = u.id
            WHERE u.id = ?
        """, (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Пайдаланушы табылмады")
    return render(request, "edit_user.html", editing=dict(row))


@app.post("/admin/users/{user_id}/edit")
def edit_user_submit(
    request: Request,
    user_id: int,
    username: str = Form(""),
    full_name: str = Form(""),
    role: str = Form(""),
    password: str = Form(""),
    group_name: str = Form(""),
    csrf_token: str = Form(""),
):
    admin = require_role(request, "admin")
    check_csrf(request, csrf_token)
    path = f"/admin/users/{user_id}/edit"
    try:
        username = valid_username(username)
        full_name = clean(full_name)
        if role not in ROLES:
            raise ValueError("Рөл дұрыс таңдалмаған")
        password_hash = valid_password(password, optional=True)
        if role == "student":
            group_name = clean(group_name, 40)
        with db() as conn:
            old = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if old is None:
                raise HTTPException(404, "Пайдаланушы табылмады")
            if admin["id"] == user_id and role != "admin":
                raise ValueError("Өзіңіздің әкімші рөліңізді өзгерте алмайсыз")
            if old["role"] == "admin" and role != "admin":
                total = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
                if total <= 1:
                    raise ValueError("Жүйеде кемінде бір әкімші қалуы керек")
            if old["role"] == "teacher" and role != "teacher":
                taught = conn.execute("SELECT COUNT(*) FROM subjects WHERE teacher_id = ?", (user_id,)).fetchone()[0]
                if taught:
                    raise ValueError("Алдымен оқытушының пәндерін басқа оқытушыға ауыстырыңыз")
            if old["role"] == "student" and role != "student":
                grade_count = conn.execute("""
                    SELECT COUNT(*) FROM grades g
                    JOIN students s ON s.id = g.student_id
                    WHERE s.user_id = ?
                """, (user_id,)).fetchone()[0]
                if grade_count:
                    raise ValueError("Бағалары бар студенттің рөлін өзгертуге болмайды")

            conn.execute(
                "UPDATE users SET username = ?, full_name = ?, role = ? WHERE id = ?",
                (username, full_name, role, user_id),
            )
            if password_hash:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            if role == "student":
                present = conn.execute("SELECT id FROM students WHERE user_id = ?", (user_id,)).fetchone()
                if present:
                    conn.execute("UPDATE students SET group_name = ? WHERE user_id = ?", (group_name, user_id))
                else:
                    conn.execute("INSERT INTO students(user_id, group_name) VALUES (?, ?)", (user_id, group_name))
            elif old["role"] == "student":
                conn.execute("DELETE FROM students WHERE user_id = ?", (user_id,))
    except ValueError as exc:
        return fail(request, path, str(exc))
    except sqlite3.IntegrityError:
        return fail(request, path, "Логин қайталанған немесе дерекқор байланысы бұзылған")
    return notify(request, "/admin", "Пайдаланушы деректері жаңартылды")


@app.post("/admin/users/{user_id}/delete")
def delete_user(request: Request, user_id: int, csrf_token: str = Form("")):
    admin = require_role(request, "admin")
    check_csrf(request, csrf_token)
    if user_id == admin["id"]:
        return fail(request, "/admin", "Өз аккаунтыңызды өшіре алмайсыз")
    with db() as conn:
        old = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Пайдаланушы табылмады")
        if old["role"] == "admin":
            count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
            if count <= 1:
                return fail(request, "/admin", "Соңғы әкімшіні өшіруге болмайды")
        if old["role"] == "teacher":
            count = conn.execute("SELECT COUNT(*) FROM subjects WHERE teacher_id = ?", (user_id,)).fetchone()[0]
            if count:
                return fail(request, "/admin", "Оқытушының пәндері бар. Алдымен оларды ауыстырыңыз")
        if old["role"] == "student":
            count = conn.execute("""
                SELECT COUNT(*) FROM grades g
                JOIN students s ON g.student_id = s.id
                WHERE s.user_id = ?
            """, (user_id,)).fetchone()[0]
            if count:
                return fail(request, "/admin", "Студенттің бағалары бар. Деректерді сақтау үшін өшіруге болмайды")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return notify(request, "/admin", "Пайдаланушы өшірілді")


@app.post("/admin/subjects")
def add_subject(
    request: Request,
    name: str = Form(""),
    teacher_id: int = Form(0),
    csrf_token: str = Form(""),
):
    require_role(request, "admin")
    check_csrf(request, csrf_token)
    try:
        name = clean(name, 120)
        with db() as conn:
            teacher = conn.execute("SELECT id FROM users WHERE id = ? AND role = 'teacher'", (teacher_id,)).fetchone()
            if teacher is None:
                raise ValueError("Оқытушыны дұрыс таңдаңыз")
            exists = conn.execute(
                "SELECT 1 FROM subjects WHERE name = ? AND teacher_id = ?", (name, teacher_id)
            ).fetchone()
            if exists:
                raise ValueError("Бұл пән осы оқытушыға бұрын қосылған")
            conn.execute("INSERT INTO subjects(name, teacher_id) VALUES (?, ?)", (name, teacher_id))
    except ValueError as exc:
        return fail(request, "/admin", str(exc))
    return notify(request, "/admin", "Пән қосылды")


@app.get("/admin/subjects/{subject_id}/edit")
def edit_subject_page(request: Request, subject_id: int):
    require_role(request, "admin")
    with db() as conn:
        subject = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
        teachers = [dict(row) for row in conn.execute(
            "SELECT id, full_name FROM users WHERE role = 'teacher' ORDER BY full_name"
        )]
    if subject is None:
        raise HTTPException(404, "Пән табылмады")
    return render(request, "edit_subject.html", editing=dict(subject), teachers=teachers)


@app.post("/admin/subjects/{subject_id}/edit")
def edit_subject_submit(
    request: Request,
    subject_id: int,
    name: str = Form(""),
    teacher_id: int = Form(0),
    csrf_token: str = Form(""),
):
    require_role(request, "admin")
    check_csrf(request, csrf_token)
    try:
        name = clean(name, 120)
        with db() as conn:
            if not conn.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
                raise HTTPException(404, "Пән табылмады")
            if not conn.execute("SELECT 1 FROM users WHERE id = ? AND role = 'teacher'", (teacher_id,)).fetchone():
                raise ValueError("Оқытушыны таңдаңыз")
            duplicate = conn.execute(
                "SELECT 1 FROM subjects WHERE name = ? AND teacher_id = ? AND id != ?",
                (name, teacher_id, subject_id),
            ).fetchone()
            if duplicate:
                raise ValueError("Бұл пән осы оқытушыға бұрын қосылған")
            conn.execute("UPDATE subjects SET name = ?, teacher_id = ? WHERE id = ?",
                         (name, teacher_id, subject_id))
    except ValueError as exc:
        return fail(request, f"/admin/subjects/{subject_id}/edit", str(exc))
    return notify(request, "/admin", "Пән жаңартылды")


@app.post("/admin/subjects/{subject_id}/delete")
def delete_subject(request: Request, subject_id: int, csrf_token: str = Form("")):
    require_role(request, "admin")
    check_csrf(request, csrf_token)
    with db() as conn:
        if not conn.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            raise HTTPException(404, "Пән табылмады")
        count = conn.execute("SELECT COUNT(*) FROM grades WHERE subject_id = ?", (subject_id,)).fetchone()[0]
        if count:
            return fail(request, "/admin", "Бағалары бар пәнді өшіруге болмайды")
        conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    return notify(request, "/admin", "Пән өшірілді")


# ─── ОҚЫТУШЫ ─────────────────────────────────────────────────────────────────

@app.get("/teacher")
def teacher_dashboard(request: Request, q: str = "", subject_id: int = 0):
    teacher = require_role(request, "teacher")
    q = q.strip()[:80]
    pattern = f"%{q}%"
    with db() as conn:
        subjects = [dict(row) for row in conn.execute(
            "SELECT id, name FROM subjects WHERE teacher_id = ? ORDER BY name", (teacher["id"],)
        )]
        students = [dict(row) for row in conn.execute("""
            SELECT s.id, u.full_name, s.group_name
            FROM students s JOIN users u ON s.user_id = u.id
            WHERE u.role = 'student'
            ORDER BY u.full_name
        """)]
        grades = [dict(row) for row in conn.execute("""
            SELECT g.id, g.score, g.created_at, s.group_name, u.full_name AS student,
                   sub.name AS subject
            FROM grades g
            JOIN students s ON g.student_id = s.id
            JOIN users u ON s.user_id = u.id
            JOIN subjects sub ON g.subject_id = sub.id
            WHERE sub.teacher_id = ?
              AND (? = '' OR u.full_name LIKE ? OR s.group_name LIKE ? OR sub.name LIKE ?)
              AND (? = 0 OR sub.id = ?)
            ORDER BY g.created_at DESC, g.id DESC
        """, (teacher["id"], q, pattern, pattern, pattern, subject_id, subject_id))]
    average = round(sum(g["score"] for g in grades) / len(grades), 1) if grades else None
    return render(request, "teacher.html", subjects=subjects, students=students,
                  grades=grades, average=average, q=q, selected_subject=subject_id)


@app.post("/teacher/grades")
def add_grade(
    request: Request,
    subject_id: int = Form(0),
    student_id: int = Form(0),
    score: int = Form(-1),
    csrf_token: str = Form(""),
):
    teacher = require_role(request, "teacher")
    check_csrf(request, csrf_token)
    try:
        score = valid_score(score)
        with db() as conn:
            owned = conn.execute(
                "SELECT 1 FROM subjects WHERE id = ? AND teacher_id = ?", (subject_id, teacher["id"])
            ).fetchone()
            student = conn.execute("""
                SELECT 1 FROM students s JOIN users u ON u.id = s.user_id
                WHERE s.id = ? AND u.role = 'student'
            """, (student_id,)).fetchone()
            if owned is None or student is None:
                raise ValueError("Студент немесе пән дұрыс таңдалмаған")
            conn.execute("INSERT INTO grades(student_id, subject_id, score) VALUES (?, ?, ?)",
                         (student_id, subject_id, score))
    except ValueError as exc:
        return fail(request, "/teacher", str(exc))
    return notify(request, "/teacher", "Баға сәтті сақталды")


def owned_grade(conn: sqlite3.Connection, grade_id: int, teacher_id: int):
    return conn.execute("""
        SELECT g.id, g.score, g.created_at, u.full_name AS student,
               s.group_name, sub.name AS subject
        FROM grades g
        JOIN students s ON g.student_id = s.id
        JOIN users u ON s.user_id = u.id
        JOIN subjects sub ON g.subject_id = sub.id
        WHERE g.id = ? AND sub.teacher_id = ?
    """, (grade_id, teacher_id)).fetchone()


@app.get("/teacher/grades/{grade_id}/edit")
def edit_grade_page(request: Request, grade_id: int):
    teacher = require_role(request, "teacher")
    with db() as conn:
        grade = owned_grade(conn, grade_id, teacher["id"])
    if grade is None:
        raise HTTPException(404, "Баға табылмады")
    return render(request, "edit_grade.html", editing=dict(grade))


@app.post("/teacher/grades/{grade_id}/edit")
def edit_grade_submit(
    request: Request,
    grade_id: int,
    score: int = Form(-1),
    csrf_token: str = Form(""),
):
    teacher = require_role(request, "teacher")
    check_csrf(request, csrf_token)
    try:
        score = valid_score(score)
    except ValueError as exc:
        return fail(request, f"/teacher/grades/{grade_id}/edit", str(exc))
    with db() as conn:
        if owned_grade(conn, grade_id, teacher["id"]) is None:
            raise HTTPException(404, "Баға табылмады")
        conn.execute("UPDATE grades SET score = ? WHERE id = ?", (score, grade_id))
    return notify(request, "/teacher", "Баға жаңартылды")


@app.post("/teacher/grades/{grade_id}/delete")
def delete_grade(request: Request, grade_id: int, csrf_token: str = Form("")):
    teacher = require_role(request, "teacher")
    check_csrf(request, csrf_token)
    with db() as conn:
        if owned_grade(conn, grade_id, teacher["id"]) is None:
            raise HTTPException(404, "Баға табылмады")
        conn.execute("DELETE FROM grades WHERE id = ?", (grade_id,))
    return notify(request, "/teacher", "Баға өшірілді")


# ─── СТУДЕНТ ─────────────────────────────────────────────────────────────────

@app.get("/student")
def student_dashboard(request: Request):
    user = require_role(request, "student")
    with db() as conn:
        profile = conn.execute(
            "SELECT id, group_name FROM students WHERE user_id = ?", (user["id"],)
        ).fetchone()
        grades = []
        if profile:
            grades = [dict(row) for row in conn.execute("""
                SELECT g.id, g.score, g.created_at, sub.id AS subject_id, sub.name AS subject,
                       t.full_name AS teacher
                FROM grades g
                JOIN subjects sub ON g.subject_id = sub.id
                JOIN users t ON sub.teacher_id = t.id
                WHERE g.student_id = ?
                ORDER BY g.created_at DESC, g.id DESC
            """, (profile["id"],))]
    average = round(sum(g["score"] for g in grades) / len(grades), 1) if grades else None
    unique_subjects = len({g["subject_id"] for g in grades})
    return render(request, "student.html", profile=dict(profile) if profile else None,
                  grades=grades, average=average, subject_count=unique_subjects)
