import os
import secrets
import smtplib
import ssl
import textwrap
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Table, ForeignKey, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

router = APIRouter()


def _engine():
    url = os.getenv("POSTGRESQL_DATABASE_URL")
    if not url:
        raise RuntimeError("POSTGRESQL_DATABASE_URL is not set")
    return create_async_engine(url, pool_pre_ping=True)


_async_engine = None
_Session = None


def _get_session() -> async_sessionmaker[AsyncSession]:
    global _async_engine, _Session
    if _Session is None:
        _async_engine = _engine()
        _Session = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _Session


class _Base(DeclarativeBase):
    pass


_roles_users = Table(
    'roles_users', _Base.metadata,
    Column('user_id', Integer, ForeignKey('users.user_id')),
    Column('role_id', Integer, ForeignKey('roles.role_id')),
)


class User(_Base):
    __tablename__ = "users"
    user_id          = Column(Integer, autoincrement=True, primary_key=True)
    first_name       = Column(String(64))
    last_name        = Column(String(64))
    email            = Column(String(120), unique=True)
    password         = Column(String(128))
    date_created     = Column(DateTime)
    admin            = Column(Boolean)
    confirmed        = Column(Boolean)
    admin_approved   = Column(Boolean)
    deleted          = Column(Boolean)
    api_key          = Column(String(100), unique=True)
    api_key_created  = Column(DateTime)


async def verify_login(email: str, password: str) -> tuple[User | None, str]:
    """Return (user, error_detail). error_detail is empty string on success."""
    Session = _get_session()
    async with Session() as session:
        result = await session.execute(
            select(User).where(User.email == email, User.deleted == False)
        )
        user = result.scalars().first()
    if user is None:
        return None, "Invalid credentials"
    if not check_password_hash(user.password, password):
        return None, "Invalid credentials"
    if not user.confirmed:
        token = _make_confirm_token(user.email)
        _send_confirmation_email(user.email, token)
        return None, "Please confirm your email address before signing in. A new confirmation link has been sent."
    if not user.admin_approved:
        return None, "Your account is pending approval."
    return user, ""


def create_access_token(email: str) -> str:
    secret = os.getenv("JWT_SECRET", "")
    expire_minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    return jwt.encode({"sub": email, "exp": expire}, secret, algorithm="HS256")


def _make_confirm_token(email: str) -> str:
    secret = os.getenv("JWT_SECRET", "")
    expire = datetime.now(timezone.utc) + timedelta(hours=48)
    return jwt.encode({"sub": email, "purpose": "email_confirm", "exp": expire}, secret, algorithm="HS256")


def _decode_confirm_token(token: str) -> str:
    """Return email or raise HTTPException."""
    secret = os.getenv("JWT_SECRET", "")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("purpose") != "email_confirm":
            raise ValueError
        email = payload.get("sub")
        if not email:
            raise ValueError
        return email
    except (JWTError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation link.")


def _send_confirmation_email(to_email: str, token: str):
    site_url = os.getenv("SITE_URL", "http://localhost:5050")
    confirm_url = f"{site_url}/api/auth/confirm-email?token={token}"
    mail_from = os.getenv("MAIL_FROM", "sea-noreply@bkslab.org")

    body = textwrap.dedent(f"""\
        Welcome to SEA!

        Please confirm your email address by clicking the link below:

        {confirm_url}

        This link expires in 48 hours.

        Your account can be used in TLDR and ZINC26.

        If you did not register for SEA, you can ignore this email.
    """)

    smtp_host = os.getenv("SMTP_SERVER", "").strip().strip('"').strip("'")
    smtp_port_raw = os.getenv("SMTP_PORT", "587").strip()
    smtp_user = os.getenv("SMTP_EMAIL_ADDRESS", "").strip().strip('"').strip("'")
    smtp_pass = os.getenv("SMTP_EMAIL_PASSWORD", "").strip().strip('"').strip("'")
    mail_from = smtp_user or mail_from

    print(f"[auth] SMTP config: host={smtp_host!r} port_raw={smtp_port_raw!r} user={smtp_user!r}")

    if not smtp_host:
        print(f"[auth] No SMTP_SERVER configured. Confirmation URL: {confirm_url}")
        return

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        print(f"[auth] Invalid SMTP_PORT value: {smtp_port_raw!r}")
        return

    msg = MIMEText(body)
    msg["Subject"] = "SEA - confirm your email address"
    msg["From"] = mail_from
    msg["To"] = to_email

    print(f"[auth] Sending confirmation email to {to_email} via {smtp_host}:{smtp_port}")
    try:
        if smtp_port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=10) as s:
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.sendmail(mail_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
                s.ehlo()
                s.starttls()
                if smtp_user:
                    s.login(smtp_user, smtp_pass)
                s.sendmail(mail_from, [to_email], msg.as_string())
        print(f"[auth] Confirmation email sent to {to_email}")
    except Exception as e:
        print(f"[auth] Failed to send confirmation email to {to_email}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to send confirmation email. Please try again later.")


def get_current_user(token: str) -> str:
    secret = os.getenv("JWT_SECRET", "")
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        email: str = payload.get("sub")
        if not email:
            raise ValueError
        return email
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


class _LoginRequest(BaseModel):
    email: str
    password: str


class _RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str


@router.post("/auth/login")
async def login(data: _LoginRequest):
    user, detail = await verify_login(data.email, data.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    token = create_access_token(user.email)
    return {"access_token": token, "first_name": user.first_name or "", "detail": "Login successfully!"}


@router.post("/auth/register")
async def register(data: _RegisterRequest):
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    Session = _get_session()
    async with Session() as session:
        result = await session.execute(
            select(User).where(User.email == data.email)
        )
        existing = result.scalars().first()
        if existing and not existing.deleted:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        hashed = generate_password_hash(data.password)
        user = User(
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            email=data.email,
            password=hashed,
            date_created=datetime.utcnow(),
            admin=False,
            confirmed=False,
            admin_approved=True,
            deleted=False,
            api_key=secrets.token_hex(16),
            api_key_created=datetime.utcnow(),
        )
        session.add(user)
        await session.commit()

    token = _make_confirm_token(data.email)
    _send_confirmation_email(data.email, token)
    return {"detail": "Registration successful. Please check your email to confirm your account."}


@router.get("/auth/confirm-email")
async def confirm_email(token: str):
    email = _decode_confirm_token(token)

    Session = _get_session()
    async with Session() as session:
        result = await session.execute(
            select(User).where(User.email == email, User.deleted == False)
        )
        user = result.scalars().first()
        if user is None:
            return HTMLResponse(_confirm_page("error", "Account not found."), status_code=400)
        if user.confirmed:
            return HTMLResponse(_confirm_page("already", email))
        user.confirmed = True
        await session.commit()

    return HTMLResponse(_confirm_page("success", email))


def _confirm_page(state: str, email: str = "") -> str:
    site_url = os.getenv("SITE_URL", "")
    if state == "success":
        title = "Email confirmed!"
        msg = f"Your email <strong>{email}</strong> has been confirmed. You can now sign in."
        color = "#14b8a6"
    elif state == "already":
        title = "Already confirmed"
        msg = f"<strong>{email}</strong> is already confirmed. You can sign in."
        color = "#64748b"
    else:
        title = "Confirmation failed"
        msg = "This confirmation link is invalid or has expired. Please register again."
        color = "#ef4444"

    link = f'<a href="{site_url}" style="color:#14b8a6">Go to SEA</a>' if site_url else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>{title}</title>
<style>body{{font-family:sans-serif;background:#0f172a;color:#f1f5f9;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#1e293b;border:1px solid rgba(255,255,255,.15);border-radius:12px;padding:40px;max-width:420px;text-align:center}}
h1{{color:{color};margin-bottom:16px}}p{{color:#94a3b8;line-height:1.6}}</style>
</head>
<body><div class="card"><h1>{title}</h1><p>{msg}</p><p style="margin-top:24px">{link}</p></div></body>
</html>"""
