from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession
from app.models import User
from app.ratelimit import limit_ip
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.security import create_access_token, hash_password, verify_password
from app.services import demo_account, jobs

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_ip("register")],
)
def register(body: RegisterRequest, db: DbSession) -> User:
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two requests registered the same email at the same moment; the unique index caught it.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered") from None
    return user


@router.post("/login", response_model=TokenResponse, dependencies=[limit_ip("login")])
def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Same message for "no such user" and "wrong password", so emails can't be probed.
    # Demo accounts have no usable password, and are refused here on principle too.
    if user is None or user.is_demo or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.post(
    "/demo",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_ip("demo_account")],
)
def try_demo(db: DbSession, background_tasks: BackgroundTasks) -> TokenResponse:
    """ "Try the demo": a throwaway account with sample invoices, deleted after 24 hours."""
    user, job_ids = demo_account.create_demo_account(db)
    for job_id in job_ids:  # the demo invoices to review are read in the background
        background_tasks.add_task(jobs.run_now, job_id)
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> User:
    return user
