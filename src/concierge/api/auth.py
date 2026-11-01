import asyncio

from fastapi import APIRouter, HTTPException, Response

from concierge.auth.dependencies import CurrentUser, DatabaseSession
from concierge.auth.security import DUMMY_PASSWORD_HASH, create_token, hash_password, verify_password
from concierge.config import get_settings
from concierge.db.relationaldb import crud
from .schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def token_response(user, response: Response) -> TokenResponse:
    lifetime = get_settings().access_token_seconds
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=create_token(user.user_id, "access", lifetime),
        expires_in=lifetime, user=UserResponse.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, session: DatabaseSession, response: Response):
    password_hash = await asyncio.to_thread(hash_password, body.password)
    try:
        user = await crud.register_user(session, body.name, body.email, password_hash)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return token_response(user, response)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: DatabaseSession, response: Response):
    record = await crud.get_user_credentials(session, body.email)
    encoded = record[1].password_hash if record is not None else DUMMY_PASSWORD_HASH
    valid = await asyncio.to_thread(verify_password, body.password, encoded)
    if not valid or record is None:
        raise HTTPException(
            status_code=401, detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_response(record[0], response)


@router.get("/me", response_model=UserResponse)
async def current_user(user: CurrentUser, response: Response):
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to view your account.", headers={"WWW-Authenticate": "Bearer"})
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)
