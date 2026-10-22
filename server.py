#!/usr/bin/env python3
"""AutoQuest's small, dependency-free SQLite application server."""

import argparse
import base64
import binascii
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
from urllib.parse import unquote, urlsplit
import uuid


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AUTOQUEST_DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "autoquest.sqlite3"
SESSION_COOKIE = "autoquest_session"
SESSION_HOURS = 12
PASSWORD_ITERATIONS = 600_000
MAX_JSON_BYTES = 40 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 6
COOKIE_SECURE = os.environ.get("AUTOQUEST_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}

PLANS = {
    "basic": {"label": "Basic Listing", "price": 2500, "listing_limit": 3},
    "pro": {"label": "Seller Pro", "price": 7500, "listing_limit": 15},
    "enterprise": {"label": "Enterprise", "price": None, "listing_limit": 100},
}
BODY_TYPES = {"suv", "sedan", "hatchback", "wagon", "pickup", "van", "coupe"}
TRANSMISSIONS = {"Automatic", "Manual"}
FUELS = {"Petrol", "Diesel", "Hybrid", "Electric"}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$")
TENANT_PERMISSION_KEYS = {
    "can_create_listings",
    "can_manage_listings",
    "can_manage_contact",
}
DEFAULT_TENANT_PERMISSIONS = {
    "can_create_listings": False,
    "can_manage_listings": False,
    "can_manage_contact": False,
}

SEED_LISTINGS = [
    ("Toyota Land Cruiser Prado TX", "Toyota", "Land Cruiser Prado TX", 2021, 8650000, 38000, "Kilimani, Nairobi", "suv", "Automatic", "Diesel", "2.8L", "Pearl white", True, "A refined, ready-for-anywhere 4x4.", "https://images.unsplash.com/photo-1519641471654-76ce0107ad1b?auto=format&fit=crop&w=900&q=80"),
    ("Mazda CX-5 AWD", "Mazda", "CX-5", 2020, 3950000, 46000, "Westlands, Nairobi", "suv", "Automatic", "Petrol", "2.5L", "Soul red", False, "Smart design, comfortable cabin and a composed drive.", "https://images.unsplash.com/photo-1519583272095-6433daf26b6e?auto=format&fit=crop&w=900&q=80"),
    ("Mercedes-Benz C200", "Mercedes-Benz", "C200", 2019, 4200000, 51000, "Muthaiga, Nairobi", "sedan", "Automatic", "Petrol", "1.5L", "Obsidian black", True, "An elegant saloon with a beautifully finished interior.", "https://images.unsplash.com/photo-1618843479313-40f8afb4b4d8?auto=format&fit=crop&w=900&q=80"),
    ("Subaru Forester XT", "Subaru", "Forester XT", 2020, 4650000, 42000, "Ridgeways, Nairobi", "suv", "Automatic", "Petrol", "2.0L", "Crystal white", False, "A practical all-wheel-drive companion for every weekend.", "https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=900&q=80"),
    ("Toyota Premio F-EX", "Toyota", "Premio F-EX", 2018, 2180000, 62000, "Nyali, Mombasa", "sedan", "Automatic", "Petrol", "1.8L", "Silver", False, "A comfortable and dependable everyday saloon.", "https://images.unsplash.com/photo-1552519507-da3b142c6e3d?auto=format&fit=crop&w=900&q=80"),
    ("Volkswagen Golf GTI", "Volkswagen", "Golf GTI", 2020, 3750000, 34000, "Nakuru", "hatchback", "Automatic", "Petrol", "2.0L", "Deep black", True, "A lively, well-equipped hatchback for the open road.", "https://images.unsplash.com/photo-1502161254066-6c74afbf07aa?auto=format&fit=crop&w=900&q=80"),
]
LOGIN_FAILURES = {}
LOGIN_FAILURES_LOCK = threading.Lock()
LOGIN_WINDOW = timedelta(minutes=15)
MAX_LOGIN_FAILURES = 10


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc)


def db_connect(path=None):
    connection = sqlite3.connect(path or DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def hash_recovery_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt_hex, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_password(password):
    if not isinstance(password, str) or len(password) < 6 or len(password) > 256:
        raise ApiError(400, "Use a password between 6 and 256 characters.")


def initialize_database(path=None):
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with db_connect(path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'tenant')),
                plan TEXT,
                subscription_status TEXT,
                subscription_expires TEXT,
                whatsapp TEXT NOT NULL DEFAULT '',
                recovery_hash TEXT,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                can_create_listings INTEGER NOT NULL DEFAULT 1,
                can_manage_listings INTEGER NOT NULL DEFAULT 1,
                can_manage_contact INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listings (
                id TEXT PRIMARY KEY,
                user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                make TEXT NOT NULL,
                model TEXT NOT NULL,
                year INTEGER NOT NULL,
                price INTEGER NOT NULL,
                mileage INTEGER NOT NULL,
                location TEXT NOT NULL,
                body_type TEXT NOT NULL,
                transmission TEXT NOT NULL,
                fuel TEXT NOT NULL,
                engine TEXT NOT NULL,
                colour TEXT NOT NULL,
                description TEXT NOT NULL,
                featured INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'sold')),
                images_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS listings_user_status_idx ON listings(user_id, status);
            CREATE INDEX IF NOT EXISTS listings_created_idx ON listings(created_at);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "recovery_hash" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN recovery_hash TEXT")
        for column in TENANT_PERMISSION_KEYS:
            if column not in user_columns:
                db.execute(
                    f"ALTER TABLE users ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1"
                )
        if db.execute("SELECT COUNT(*) FROM listings WHERE user_id IS NULL").fetchone()[0] == 0:
            created_at = utc_now().isoformat()
            for index, car in enumerate(SEED_LISTINGS):
                (title, make, model, year, price, mileage, location, body_type,
                 transmission, fuel, engine, colour, featured, description, image) = car
                db.execute(
                    """INSERT INTO listings VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (
                        f"seed-{index + 1}", title, make, model, year, price, mileage,
                        location, body_type, transmission, fuel, engine, colour,
                        description, int(featured), json.dumps([image]), created_at,
                    ),
                )


def create_account(db, email, name, password, role="tenant", plan=None,
                   subscription_status="pending", must_change_password=False,
                   permissions=None):
    email = email.strip().lower() if isinstance(email, str) else ""
    name = name.strip() if isinstance(name, str) else ""
    valid_email = bool(EMAIL_RE.fullmatch(email))
    if not valid_email:
        raise ApiError(400, "Enter a valid email address.")
    if not name or len(name) > 100:
        raise ApiError(400, "Name must be between 1 and 100 characters.")
    validate_password(password)
    if role not in {"admin", "tenant"}:
        raise ApiError(400, "Invalid account role.")
    if role == "tenant" and plan not in PLANS:
        raise ApiError(400, "Select a valid subscription plan.")
    permissions = validate_tenant_permissions(
        DEFAULT_TENANT_PERMISSIONS if permissions is None else permissions
    )
    account_id = uuid.uuid4().hex
    recovery_code = secrets.token_urlsafe(24)
    try:
        db.execute(
            """INSERT INTO users
               (id, email, name, password_hash, role, plan, subscription_status,
                recovery_hash, must_change_password, can_create_listings,
                can_manage_listings, can_manage_contact, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id, email, name, hash_password(password), role, plan,
                subscription_status if role == "tenant" else None,
                hash_recovery_code(recovery_code), int(must_change_password),
                int(permissions["can_create_listings"]),
                int(permissions["can_manage_listings"]),
                int(permissions["can_manage_contact"]),
                utc_now().isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "email" in str(error).lower():
            raise ApiError(409, "An account with that email already exists.") from error
        raise
    return account_id, recovery_code


def validate_tenant_permissions(permissions):
    if (
        not isinstance(permissions, dict)
        or set(permissions) != TENANT_PERMISSION_KEYS
        or any(type(value) is not bool for value in permissions.values())
    ):
        raise ApiError(400, "Choose valid seller permissions.")
    return permissions


def account_json(row):
    subscription_status = row["subscription_status"]
    expires = row["subscription_expires"]
    if subscription_status == "active" and expires and expires <= utc_now().isoformat():
        subscription_status = "expired"
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "plan": row["plan"],
        "subscription_status": subscription_status,
        "subscription_expires": expires,
        "whatsapp": row["whatsapp"],
        "must_change_password": bool(row["must_change_password"]),
        "permissions": {
            "can_create_listings": bool(row["can_create_listings"]),
            "can_manage_listings": bool(row["can_manage_listings"]),
            "can_manage_contact": bool(row["can_manage_contact"]),
        },
    }


def admin_account_exists():
    with db_connect() as db:
        return db.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone() is not None


class AutoQuestHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AutoQuestHandler(BaseHTTPRequestHandler):
    server_version = "AutoQuest"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.client_address[0]} - {fmt % args}\n")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if COOKIE_SECURE:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https://images.unsplash.com; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def _json(self, status, payload, headers=()):
        content = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ApiError(400, "Invalid request size.") from error
        if length < 1 or length > MAX_JSON_BYTES:
            raise ApiError(413, "Request must include a body smaller than 40 MB.")
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ApiError(400, "Request body must be valid JSON.") from error
        if not isinstance(body, dict):
            raise ApiError(400, "Request body must be an object.")
        return body

    def _check_origin(self):
        origin = self.headers.get("Origin")
        parsed = urlsplit(origin or "")
        if not parsed.netloc or parsed.netloc.lower() != self.headers.get("Host", "").lower():
            raise ApiError(403, "Request origin is not allowed.")
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
        expected_scheme = forwarded_proto or ("https" if COOKIE_SECURE else "http")
        if parsed.scheme != expected_scheme:
            raise ApiError(403, "Request origin is not allowed.")

    def _current_account(self, required=True):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            raw_token = cookies[SESSION_COOKIE].value
        except (KeyError, CookieError, AttributeError):
            if required:
                raise ApiError(401, "Sign in to continue.")
            return None
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = utc_now().isoformat()
        with db_connect() as db:
            row = db.execute(
                """SELECT users.*, sessions.expires_at AS session_expires
                   FROM sessions JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ?""",
                (token_hash,),
            ).fetchone()
            if row and row["session_expires"] > now:
                return account_json(row)
            if row:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        if required:
            raise ApiError(401, "Your session has expired. Please sign in again.")
        return None

    def _require_role(self, roles):
        account = self._current_account()
        if account["role"] not in roles:
            raise ApiError(403, "You do not have permission to do that.")
        return account

    def _subscription_active(self, account):
        if account["role"] == "admin":
            return True
        if account["subscription_status"] != "active":
            return False
        expires = account["subscription_expires"]
        return not expires or expires > utc_now().isoformat()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/api/"):
            try:
                self._api_get(path)
            except ApiError as error:
                self._error(error.status, str(error))
            except (OSError, sqlite3.Error) as error:
                print(f"API read failed: {error}", file=sys.stderr)
                self._error(500, "The request could not be completed.")
            return
        self._serve_static(path)

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        try:
            self._check_origin()
            if path == "/api/auth/login":
                self._login()
            elif path == "/api/auth/setup":
                self._setup_admin()
            elif path == "/api/auth/register":
                self._register_tenant()
            elif path == "/api/auth/logout":
                self._logout()
            elif path == "/api/auth/recovery":
                self._recover_password()
            elif path == "/api/auth/password":
                self._change_password()
            elif path == "/api/listings":
                self._create_listing()
            elif path == "/api/tenants":
                self._create_tenant()
            elif path == "/api/settings":
                self._save_settings()
            else:
                raise ApiError(404, "Route not found.")
        except ApiError as error:
            self._error(error.status, str(error))
        except (OSError, sqlite3.Error, ValueError) as error:
            print(f"API write failed: {error}", file=sys.stderr)
            self._error(500, "The request could not be completed.")

    def do_PATCH(self):
        try:
            self._check_origin()
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            body = self._read_json()
            if len(parts) == 3 and parts[:2] == ["api", "listings"]:
                self._update_listing(parts[2], body)
            elif len(parts) == 3 and parts[:2] == ["api", "tenants"]:
                self._update_tenant(parts[2], body)
            elif parts == ["api", "account"]:
                self._save_tenant_contact(body)
            else:
                raise ApiError(404, "Route not found.")
        except ApiError as error:
            self._error(error.status, str(error))
        except (OSError, sqlite3.Error, ValueError) as error:
            print(f"API update failed: {error}", file=sys.stderr)
            self._error(500, "The request could not be completed.")

    def do_DELETE(self):
        try:
            self._check_origin()
            parts = unquote(urlsplit(self.path).path).strip("/").split("/")
            if len(parts) != 3 or parts[:2] != ["api", "listings"]:
                raise ApiError(404, "Route not found.")
            self._delete_listing(parts[2])
        except ApiError as error:
            self._error(error.status, str(error))
        except (OSError, sqlite3.Error) as error:
            print(f"API delete failed: {error}", file=sys.stderr)
            self._error(500, "The request could not be completed.")

    def _api_get(self, path):
        if path == "/api/listings":
            with db_connect() as db:
                rows = db.execute(
                    """SELECT listings.*, users.name AS seller_name, users.whatsapp AS seller_whatsapp,
                              users.role AS seller_role, users.plan AS seller_plan,
                              users.subscription_status AS seller_subscription_status,
                              users.subscription_expires AS seller_subscription_expires
                       FROM listings LEFT JOIN users ON users.id = listings.user_id
                       WHERE listings.status = 'active'
                       ORDER BY listings.featured DESC, listings.created_at DESC"""
                ).fetchall()
            listings = []
            for row in rows:
                if row["user_id"] is not None:
                    if row["seller_role"] != "admin" and (
                        row["seller_subscription_status"] != "active"
                        or (row["seller_subscription_expires"] and row["seller_subscription_expires"] <= utc_now().isoformat())
                    ):
                        continue
                listings.append(self._listing_json(row))
            self._json(200, {"listings": listings})
            return
        if path == "/api/auth/session":
            account = self._current_account(required=False)
            self._json(200, {
                "account": account,
                "setup_required": not admin_account_exists(),
            })
            return
        if path == "/api/settings":
            with db_connect() as db:
                row = db.execute("SELECT value FROM settings WHERE key = 'whatsapp'").fetchone()
            self._json(200, {"whatsapp": row["value"] if row else ""})
            return
        if path == "/api/tenants":
            self._require_role({"admin"})
            with db_connect() as db:
                rows = db.execute(
                    """SELECT users.*, COUNT(listings.id) AS listing_count
                       FROM users LEFT JOIN listings ON listings.user_id = users.id
                       WHERE users.role = 'tenant' GROUP BY users.id ORDER BY users.created_at DESC"""
                ).fetchall()
            tenants = []
            for row in rows:
                tenant = account_json(row)
                tenant["listing_count"] = row["listing_count"]
                tenant["plan_label"] = PLANS[tenant["plan"]]["label"]
                tenant["plan_price"] = PLANS[tenant["plan"]]["price"]
                tenants.append(tenant)
            self._json(200, {"tenants": tenants})
            return
        if path == "/api/account/listings":
            account = self._current_account()
            if account["must_change_password"]:
                raise ApiError(403, "Change your temporary password before continuing.")
            with db_connect() as db:
                rows = db.execute(
                    "SELECT * FROM listings WHERE user_id = ? ORDER BY created_at DESC",
                    (account["id"],),
                ).fetchall()
            self._json(200, {"listings": [self._listing_json(row) for row in rows]})
            return
        raise ApiError(404, "Route not found.")

    def _listing_json(self, row):
        return {
            "id": row["id"],
            "title": row["title"],
            "make": row["make"],
            "model": row["model"],
            "year": row["year"],
            "price": row["price"],
            "mileage": row["mileage"],
            "location": row["location"],
            "bodyType": row["body_type"],
            "transmission": row["transmission"],
            "fuel": row["fuel"],
            "engine": row["engine"],
            "colour": row["colour"],
            "description": row["description"],
            "featured": bool(row["featured"]),
            "status": row["status"],
            "images": json.loads(row["images_json"]),
            "sellerName": (row["seller_name"] or "") if "seller_name" in row.keys() else "",
            "sellerWhatsApp": (row["seller_whatsapp"] or "") if "seller_whatsapp" in row.keys() else "",
        }

    def _login(self):
        body = self._read_json()
        email = body.get("email", "")
        password = body.get("password", "")
        if not isinstance(email, str) or not isinstance(password, str):
            raise ApiError(400, "Enter your email and password.")
        email = email.strip().lower()
        if len(email) > 254 or len(password) > 256:
            raise ApiError(400, "Email or password is too long.")
        now = utc_now()
        with LOGIN_FAILURES_LOCK:
            failures = [attempt for attempt in LOGIN_FAILURES.get(self.client_address[0], []) if now - attempt < LOGIN_WINDOW]
            LOGIN_FAILURES[self.client_address[0]] = failures
            if len(failures) >= MAX_LOGIN_FAILURES:
                raise ApiError(429, "Too many sign-in attempts. Please wait 15 minutes and try again.")
        with db_connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            encoded = row["password_hash"] if row else hash_password("not-the-password")
            valid = verify_password(password, encoded)
            if not row or not valid:
                with LOGIN_FAILURES_LOCK:
                    LOGIN_FAILURES.setdefault(self.client_address[0], []).append(utc_now())
                raise ApiError(401, "Email or password is incorrect.")
            if row["role"] == "tenant" and row["subscription_status"] == "suspended":
                raise ApiError(403, "This seller account is paused. Contact AutoQuest for help.")
            with LOGIN_FAILURES_LOCK:
                LOGIN_FAILURES.pop(self.client_address[0], None)
            token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(), row["id"],
                    (utc_now() + timedelta(hours=SESSION_HOURS)).isoformat(),
                    utc_now().isoformat(),
                ),
            )
        cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS * 3600}"
        if COOKIE_SECURE:
            cookie += "; Secure"
        self._json(200, {"account": account_json(row)}, [("Set-Cookie", cookie)])

    def _create_session(self, user_id, recovery_code):
        token = secrets.token_urlsafe(32)
        with db_connect() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row:
                raise ApiError(500, "The account could not be created.")
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    user_id,
                    (utc_now() + timedelta(hours=SESSION_HOURS)).isoformat(),
                    utc_now().isoformat(),
                ),
            )
        cookie = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS * 3600}"
        if COOKIE_SECURE:
            cookie += "; Secure"
        return self._json(
            201,
            {
                "account": account_json(row),
                "recovery_code": recovery_code,
            },
            [("Set-Cookie", cookie)],
        )

    def _setup_admin(self):
        body = self._read_json()
        email = body.get("email")
        name = body.get("name")
        password = body.get("password")
        if not isinstance(password, str) or len(password) < 12:
            raise ApiError(400, "Admin passwords must be at least 12 characters.")
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone():
                raise ApiError(409, "An admin account already exists. Sign in instead.")
            account_id, recovery_code = create_account(
                db, email, name, password, role="admin"
            )
        return self._create_session(account_id, recovery_code)

    def _register_tenant(self):
        body = self._read_json()
        if not admin_account_exists():
            raise ApiError(403, "The AutoQuest administrator must set up the site first.")
        plan = body.get("plan")
        if not isinstance(plan, str) or plan not in PLANS:
            raise ApiError(400, "Select a valid subscription plan.")
        account_id, recovery_code = None, None
        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone():
                raise ApiError(403, "The AutoQuest administrator must set up the site first.")
            account_id, recovery_code = create_account(
                db,
                body.get("email"),
                body.get("name"),
                body.get("password"),
                role="tenant",
                plan=plan,
                subscription_status="pending",
                permissions={key: False for key in TENANT_PERMISSION_KEYS},
            )
        return self._create_session(account_id, recovery_code)

    def _logout(self):
        account = self._current_account(required=False)
        if account:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie[SESSION_COOKIE].value
            with db_connect() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))
        self._json(200, {"ok": True}, [("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + ("; Secure" if COOKIE_SECURE else ""))])

    def _change_password(self):
        account = self._current_account()
        body = self._read_json()
        current = body.get("currentPassword", "")
        new_password = body.get("newPassword", "")
        validate_password(new_password)
        if not isinstance(current, str) or len(current) > 256:
            raise ApiError(400, "Current password is invalid.")
        recovery_code = secrets.token_urlsafe(24)
        with db_connect() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id = ?", (account["id"],)).fetchone()
            if not isinstance(current, str) or not verify_password(current, row["password_hash"]):
                raise ApiError(403, "Current password is incorrect.")
            db.execute(
                "UPDATE users SET password_hash = ?, recovery_hash = ?, must_change_password = 0 WHERE id = ?",
                (hash_password(new_password), hash_recovery_code(recovery_code), account["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (
                account["id"],
                hashlib.sha256(SimpleCookie(self.headers.get("Cookie", ""))[SESSION_COOKIE].value.encode()).hexdigest(),
            ))
        self._json(200, {"ok": True, "recovery_code": recovery_code})

    def _recover_password(self):
        body = self._read_json()
        email = body.get("email", "")
        recovery_code = body.get("recoveryCode", "")
        new_password = body.get("newPassword", "")
        validate_password(new_password)
        if not isinstance(email, str) or len(email) > 254:
            raise ApiError(400, "Enter your account sign-in ID and recovery code.")
        if not isinstance(recovery_code, str) or not 16 <= len(recovery_code) <= 256:
            raise ApiError(400, "Enter the recovery code you saved when your account was created.")
        fresh_recovery_code = secrets.token_urlsafe(24)
        with db_connect() as db:
            row = db.execute(
                "SELECT id, recovery_hash FROM users WHERE email = ?",
                (email.strip().lower(),),
            ).fetchone()
            stored_hash = row["recovery_hash"] if row and row["recovery_hash"] else hash_recovery_code("unavailable-recovery-code")
            if not hmac.compare_digest(hash_recovery_code(recovery_code), stored_hash):
                raise ApiError(403, "Sign-in ID or recovery code is incorrect.")
            db.execute(
                """UPDATE users SET password_hash = ?, recovery_hash = ?, must_change_password = 0
                   WHERE id = ?""",
                (hash_password(new_password), hash_recovery_code(fresh_recovery_code), row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        self._json(200, {"ok": True, "recovery_code": fresh_recovery_code})

    def _validate_listing(self, body):
        def text(field, required=False, maximum=100):
            value = body.get(field, "")
            if not isinstance(value, str):
                raise ApiError(400, f"{field} must be text.")
            value = value.strip()
            if required and not value:
                raise ApiError(400, f"{field} is required.")
            if len(value) > maximum:
                raise ApiError(400, f"{field} must be {maximum} characters or fewer.")
            return value

        def number(field, minimum, maximum):
            value = body.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ApiError(400, f"{field} must be between {minimum} and {maximum}.")
            return value

        photos = body.get("images")
        if not isinstance(photos, list) or not 1 <= len(photos) <= MAX_IMAGES:
            raise ApiError(400, f"Add between 1 and {MAX_IMAGES} car photos.")
        image_data = []
        total_image_bytes = 0
        for photo in photos:
            if not isinstance(photo, str) or len(photo) > MAX_IMAGE_BYTES * 2:
                raise ApiError(400, "A compressed car photo is too large or invalid.")
            match = re.fullmatch(r"data:image/jpeg;base64,([A-Za-z0-9+/]+={0,2})", photo)
            if not match:
                raise ApiError(400, "Car photos must be JPEG images.")
            try:
                data = base64.b64decode(match.group(1), validate=True)
            except (ValueError, binascii.Error) as error:
                raise ApiError(400, "A car photo is not valid base64 data.") from error
            if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8\xff"):
                raise ApiError(400, "Each car photo must be a valid JPEG smaller than 5 MB.")
            total_image_bytes += len(data)
            if total_image_bytes > 24 * 1024 * 1024:
                raise ApiError(400, "The combined compressed photos must be smaller than 24 MB.")
            image_data.append(data)
        body_type = body.get("bodyType", "suv")
        transmission = body.get("transmission", "Automatic")
        fuel = body.get("fuel", "Petrol")
        if (
            not isinstance(body_type, str) or body_type not in BODY_TYPES
            or not isinstance(transmission, str) or transmission not in TRANSMISSIONS
            or not isinstance(fuel, str) or fuel not in FUELS
        ):
            raise ApiError(400, "Select valid vehicle details.")
        listing = {
            "title": text("title", True, 80),
            "make": text("make", True, 40),
            "model": text("model", True, 40),
            "year": number("year", 1950, utc_now().year + 1),
            "price": number("price", 1000, 10_000_000_000),
            "mileage": number("mileage", 0, 10_000_000),
            "location": text("location", True, 60),
            "body_type": body_type,
            "transmission": transmission,
            "fuel": fuel,
            "engine": text("engine", False, 30),
            "colour": text("colour", False, 30),
            "description": text("description", False, 500),
            "featured": False,
            "images": [],
        }
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for data in image_data:
            image_name = f"{uuid.uuid4().hex}.jpg"
            try:
                (UPLOAD_DIR / image_name).write_bytes(data)
            except OSError:
                self._remove_images(listing["images"])
                raise
            listing["images"].append("/uploads/" + image_name)
        return listing

    def _create_listing(self):
        account = self._require_role({"admin", "tenant"})
        if account["must_change_password"]:
            raise ApiError(403, "Change your temporary password before continuing.")
        if account["role"] == "tenant" and not account["permissions"]["can_create_listings"]:
            raise ApiError(403, "Your account does not have permission to create listings.")
        if not self._subscription_active(account):
            raise ApiError(403, "Your subscription is not active. Contact AutoQuest to activate it.")
        body = self._read_json()
        listing = self._validate_listing(body)
        listing_id = uuid.uuid4().hex
        try:
            with db_connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if account["role"] == "tenant":
                    limit = PLANS[account["plan"]]["listing_limit"]
                    count = db.execute(
                        "SELECT COUNT(*) FROM listings WHERE user_id = ? AND status = 'active'",
                        (account["id"],),
                    ).fetchone()[0]
                    if count >= limit:
                        raise ApiError(403, f"Your {PLANS[account['plan']]['label']} plan allows up to {limit} active listings.")
                db.execute(
                    """INSERT INTO listings
                       (id, user_id, title, make, model, year, price, mileage, location,
                        body_type, transmission, fuel, engine, colour, description,
                        featured, status, images_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                    (
                        listing_id, account["id"], listing["title"], listing["make"],
                        listing["model"], listing["year"], listing["price"], listing["mileage"],
                        listing["location"], listing["body_type"], listing["transmission"],
                        listing["fuel"], listing["engine"], listing["colour"],
                        listing["description"], int(account["role"] == "admin" and bool(body.get("featured"))),
                        json.dumps(listing["images"]), utc_now().isoformat(),
                    ),
                )
        except (ApiError, OSError, sqlite3.Error):
            self._remove_images(listing["images"])
            raise
        self._json(201, {"ok": True, "id": listing_id})

    def _update_listing(self, listing_id, body):
        if not re.fullmatch(r"[a-f0-9]{32}", listing_id):
            raise ApiError(404, "Listing not found.")
        account = self._require_role({"admin", "tenant"})
        if account["role"] == "tenant" and not account["permissions"]["can_manage_listings"]:
            raise ApiError(403, "Your account does not have permission to manage listings.")
        status = body.get("status")
        if not isinstance(status, str) or status not in {"active", "sold"}:
            raise ApiError(400, "Status must be active or sold.")
        with db_connect() as db:
            if account["role"] == "admin":
                result = db.execute(
                    "UPDATE listings SET status = ? WHERE id = ? AND user_id = ?",
                    (status, listing_id, account["id"]),
                )
            else:
                result = db.execute(
                    "UPDATE listings SET status = ? WHERE id = ? AND user_id = ?",
                    (status, listing_id, account["id"]),
                )
            if result.rowcount == 0:
                raise ApiError(404, "Your listing was not found.")
        self._json(200, {"ok": True})

    def _delete_listing(self, listing_id):
        if not re.fullmatch(r"[a-f0-9]{32}", listing_id):
            raise ApiError(404, "Listing not found.")
        account = self._require_role({"admin", "tenant"})
        if account["role"] == "tenant" and not account["permissions"]["can_manage_listings"]:
            raise ApiError(403, "Your account does not have permission to manage listings.")
        with db_connect() as db:
            row = db.execute(
                "SELECT images_json FROM listings WHERE id = ? AND user_id = ?",
                (listing_id, account["id"]),
            ).fetchone()
            if not row:
                raise ApiError(404, "Your listing was not found.")
            db.execute("DELETE FROM listings WHERE id = ? AND user_id = ?", (listing_id, account["id"]))
        self._remove_images(json.loads(row["images_json"]))
        self._json(200, {"ok": True})

    def _remove_images(self, images):
        for image in images:
            name = Path(urlsplit(image).path).name
            if re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
                (UPLOAD_DIR / name).unlink(missing_ok=True)

    def _create_tenant(self):
        self._require_role({"admin"})
        body = self._read_json()
        plan = body.get("plan")
        if not isinstance(plan, str) or plan not in PLANS:
            raise ApiError(400, "Select a valid subscription plan.")
        permissions = body.get("permissions", DEFAULT_TENANT_PERMISSIONS)
        validate_tenant_permissions(permissions)
        temporary_password = secrets.token_urlsafe(16)
        with db_connect() as db:
            tenant_id, recovery_code = create_account(
                db, body.get("email"), body.get("name"), temporary_password,
                role="tenant", plan=plan, subscription_status="pending",
                must_change_password=True, permissions=permissions,
            )
        self._json(201, {
            "tenant": {"id": tenant_id, "email": body["email"].strip().lower(), "plan": plan},
            "temporary_password": temporary_password,
            "recovery_code": recovery_code,
        })

    def _update_tenant(self, tenant_id, body):
        self._require_role({"admin"})
        if not re.fullmatch(r"[a-f0-9]{32}", tenant_id):
            raise ApiError(404, "Tenant not found.")
        allowed_fields = {"subscription_status", "permissions"}
        if not body or set(body) - allowed_fields:
            raise ApiError(400, "Update subscription status or seller permissions.")
        status = body.get("subscription_status")
        if status is not None and (
            not isinstance(status, str) or status not in {"active", "pending", "suspended"}
        ):
            raise ApiError(400, "Subscription status must be active, pending or suspended.")
        permissions = body.get("permissions")
        if permissions is not None:
            validate_tenant_permissions(permissions)
        if status is None and permissions is None:
            raise ApiError(400, "Update subscription status or seller permissions.")
        with db_connect() as db:
            updates = []
            values = []
            if status is not None:
                updates.extend(["subscription_status = ?", "subscription_expires = ?"])
                values.extend([
                    status,
                    (utc_now() + timedelta(days=30)).isoformat() if status == "active" else None,
                ])
            if permissions is not None:
                for key in sorted(TENANT_PERMISSION_KEYS):
                    updates.append(f"{key} = ?")
                    values.append(int(permissions[key]))
            values.append(tenant_id)
            result = db.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ? AND role = 'tenant'",
                values,
            )
            if result.rowcount == 0:
                raise ApiError(404, "Tenant not found.")
        self._json(200, {"ok": True})

    def _save_settings(self):
        account = self._require_role({"admin"})
        if account["must_change_password"]:
            raise ApiError(403, "Change your temporary password before continuing.")
        body = self._read_json()
        value = body.get("whatsapp", "")
        if not isinstance(value, str):
            raise ApiError(400, "WhatsApp number must be text.")
        digits = re.sub(r"\D", "", value)
        if value and not 10 <= len(digits) <= 15:
            raise ApiError(400, "Include the country code in a valid WhatsApp number.")
        with db_connect() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('whatsapp', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (value.strip(),),
            )
        self._json(200, {"ok": True})

    def _save_tenant_contact(self, body):
        account = self._require_role({"tenant"})
        if account["must_change_password"]:
            raise ApiError(403, "Change your temporary password before continuing.")
        if not account["permissions"]["can_manage_contact"]:
            raise ApiError(403, "Your account does not have permission to update seller contact details.")
        value = body.get("whatsapp", "")
        if not isinstance(value, str):
            raise ApiError(400, "WhatsApp number must be text.")
        digits = re.sub(r"\D", "", value)
        if value and not 10 <= len(digits) <= 15:
            raise ApiError(400, "Include the country code in a valid WhatsApp number.")
        with db_connect() as db:
            db.execute("UPDATE users SET whatsapp = ? WHERE id = ?", (value.strip(), account["id"]))
        self._json(200, {"ok": True})

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        if path.startswith("/uploads/"):
            name = path.removeprefix("/uploads/")
            if not re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
                self.send_error(404)
                return
            target = UPLOAD_DIR / name
            content_type = "image/jpeg"
        else:
            allowed = {
                "/index.html", "/admin.html", "/styles.css", "/app.js",
                "/admin.js", "/nav.js", "/assets/autoquest-logo.jpeg",
            }
            if path not in allowed:
                self.send_error(404)
                return
            target = ROOT / path.lstrip("/")
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            content = target.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=300" if path.startswith("/uploads/") else "no-store")
        if path.startswith("/uploads/"):
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="Run the AutoQuest marketplace server.")
    parser.add_argument("--host", default=os.environ.get("AUTOQUEST_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AUTOQUEST_PORT", "8000")))
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    initialize_database()
    if not admin_account_exists():
        print("No admin account exists. Create the first admin at /admin.html.")
    httpd = AutoQuestHTTPServer((args.host, args.port), AutoQuestHandler)
    print(f"AutoQuest serving at http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AutoQuest.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
