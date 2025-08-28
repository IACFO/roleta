import os
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret"))

STREAMLIT_INTERNAL_URL = os.environ.get("STREAMLIT_INTERNAL_URL", "http://localhost:8502").rstrip("/")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")
AUTH0_METADATA_URL = f"https://{AUTH0_DOMAIN}/.well-known/openid-configuration"

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "").strip()
DEV_FAKE_USER_ID = os.environ.get("DEV_FAKE_USER_ID", "")
DEV_FAKE_EMAIL = os.environ.get("DEV_FAKE_EMAIL", "")

MP_MONTHLY_PLAN_ID = os.environ.get("MP_MONTHLY_PLAN_ID", "")
MP_YEARLY_PLAN_ID = os.environ.get("MP_YEARLY_PLAN_ID", "")

USE_DB = bool(os.environ.get("DATABASE_URL"))
if USE_DB:
    try:
        from .db import SessionLocal, init_db
        from .models import User, Store
        from sqlalchemy import select, insert, update
    except Exception:
        USE_DB = False

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data" / "stores"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _store_path(uid: str) -> Path:
    return DATA_DIR / f"{uid}.json"

oauth = OAuth()
AUTH0_ENABLED = bool(AUTH0_DOMAIN and AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET)
if AUTH0_ENABLED:
    oauth.register(
        name="auth0",
        server_metadata_url=AUTH0_METADATA_URL,
        client_id=AUTH0_CLIENT_ID,
        client_secret=AUTH0_CLIENT_SECRET,
        client_kwargs={"scope": "openid profile email"},
    )

def get_user(request: Request) -> Optional[dict]:
    return request.session.get("user")

def require_user(request: Request) -> dict:
    u = get_user(request)
    if u:
        return u
    if not AUTH0_ENABLED and DEV_FAKE_USER_ID and DEV_FAKE_EMAIL:
        u = {"sub": DEV_FAKE_USER_ID, "email": DEV_FAKE_EMAIL}
        request.session["user"] = u
        return u
    raise HTTPException(status_code=401, detail="login required")

def user_from_internal(request: Request) -> Optional[dict]:
    key = request.headers.get("x-internal-key")
    if not key or key != INTERNAL_API_KEY:
        return None
    sub = request.headers.get("x-user-sub")
    email = request.headers.get("x-user-email")
    if not sub or not email:
        raise HTTPException(status_code=400, detail="missing x-user-sub/x-user-email")
    return {"sub": sub, "email": email}

@app.on_event("startup")
async def _startup():
    if USE_DB:
        await init_db()
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/login")
async def login(request: Request):
    if not AUTH0_ENABLED:
        if DEV_FAKE_USER_ID and DEV_FAKE_EMAIL:
            request.session["user"] = {"sub": DEV_FAKE_USER_ID, "email": DEV_FAKE_EMAIL}
            return RedirectResponse(url="/app")
        raise HTTPException(500, "Auth0 não configurado e DEV_FAKE_* ausentes.")
    if not BASE_URL:
        raise HTTPException(500, "BASE_URL não configurado.")
    redirect_uri = f"{BASE_URL}/callback"
    return await oauth.auth0.authorize_redirect(request, redirect_uri)

@app.get("/callback")
async def auth_callback(request: Request):
    if not AUTH0_ENABLED:
        return RedirectResponse(url="/app")
    token = await oauth.auth0.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.auth0.parse_id_token(request, token)
    user_data = {
        "sub": userinfo.get("sub"),
        "email": userinfo.get("email"),
        "name": userinfo.get("name"),
    }
    request.session["user"] = user_data
    request.session["id_token"] = token.get("id_token")

    if USE_DB:
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == user_data["sub"]))
            user = res.scalar_one_or_none()
            if not user:
                await s.execute(insert(User).values(
                    okta_user_id=user_data["sub"],
                    email=user_data["email"]
                ))
                await s.commit()
    return RedirectResponse(url="/app")

@app.get("/logout")
async def logout(request: Request):
    id_token = request.session.pop("id_token", None)
    request.session.clear()
    if AUTH0_ENABLED and id_token and BASE_URL:
        return RedirectResponse(
            url=f"https://{AUTH0_DOMAIN}/v2/logout?id_token_hint={id_token}&post_logout_redirect_uri={quote(BASE_URL + '/')}"
        )
    return RedirectResponse(url="/")

@app.get("/health")
async def health():
    return {
        "ok": True,
        "storage": ("db" if USE_DB else "file"),
        "auth": ("auth0" if AUTH0_ENABLED else "dev"),
    }

@app.get("/me")
async def me(request: Request):
    u = user_from_internal(request) or require_user(request)
    return {"user_id": u.get("sub"), "email": u.get("email")}

@app.post("/webhook")
async def mercado_pago_webhook(request: Request):
    payload = await request.json()
    topic = request.query_params.get("topic")
    if topic == "preapproval":
        payer_email = payload.get("payer_email")
        if not payer_email:
            return JSONResponse(status_code=400, content={"error": "payer_email ausente"})
        if USE_DB:
            async with SessionLocal() as s:
                res = await s.execute(select(User).where(User.email == payer_email))
                user = res.scalar_one_or_none()
                if user:
                    await s.execute(update(User).where(User.id == user.id).values(
                        access_expires_at=datetime.utcnow() + timedelta(days=365)
                    ))
                    await s.commit()
        return {"ok": True}
    return {"ignored": True}

@app.get("/billing/status")
async def billing_status(request: Request):
    u = user_from_internal(request) or require_user(request)
    if USE_DB:
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
            user = res.scalar_one_or_none()
            if not user or not user.access_expires_at or datetime.utcnow() > user.access_expires_at:
                return {"status": "expired"}
    return {"status": "active"}

@app.post("/billing/subscribe")
async def billing_subscribe(request: Request, plan: str = "yearly"):
    u = user_from_internal(request) or require_user(request)

    # ❌ REMOVA esta ativação prematura
    # async with SessionLocal() as s:
    #     res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
    #     user = res.scalar_one_or_none()
    #     if user:
    #         await s.execute(update(User).where(User.id == user.id).values(access_expires_at=datetime.utcnow() + timedelta(days=365)))
    #         await s.commit()

    plan_id = MP_YEARLY_PLAN_ID
    if not plan_id:
        raise HTTPException(400, detail="plan 'yearly' sem PLAN_ID configurado")
    if not BASE_URL:
        raise HTTPException(500, detail="BASE_URL não configurado")
    back_url = f"{BASE_URL}/billing/thankyou"
    init_point = (
        "https://www.mercadopago.com.br/subscriptions/checkout"
        f"?preapproval_plan_id={quote(plan_id)}"
        f"&back_url={quote(back_url)}"
        f"&auto_return=approved"
    )
    return {"init_point": init_point, "plan": plan, "user": u}

@app.get("/billing/thankyou")
async def billing_thankyou():
    return RedirectResponse(url="/app")

# Store endpoints
if USE_DB:
    @app.get("/store")
    async def get_store(request: Request):
        u = user_from_internal(request) or require_user(request)
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
            user = res.scalar_one_or_none()
            if not user or not user.access_expires_at or datetime.utcnow() > user.access_expires_at:
                return JSONResponse(status_code=403, content={"error": "Acesso negado. Assinatura necessária."})
            res = await s.execute(select(Store).where(Store.user_id == user.id))
            st_row = res.scalar_one_or_none()
            return {"data": st_row.data if st_row else {}}

    @app.put("/store")
    async def put_store(request: Request, payload: dict = Body(...)):
        u = user_from_internal(request) or require_user(request)
        data = payload.get("data", {})
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
            user = res.scalar_one_or_none()
            if not user or not user.access_expires_at or datetime.utcnow() > user.access_expires_at:
                return JSONResponse(status_code=403, content={"error": "Acesso negado. Assinatura necessária."})
            res2 = await s.execute(select(Store).where(Store.user_id == user.id))
            st_row = res2.scalar_one_or_none()
            if st_row:
                await s.execute(update(Store).where(Store.user_id == user.id).values(data=data))
            else:
                await s.execute(insert(Store).values(user_id=user.id, data=data))
            await s.commit()
        return {"ok": True}
else:
    # Fallback to local file storage
    @app.get("/store")
    async def get_store(request: Request):
        u = user_from_internal(request) or require_user(request)
        p = _store_path(u["sub"])
        if p.exists():
            try:
                return {"data": json.loads(p.read_text(encoding="utf-8"))}
            except Exception:
                return {"data": {}}
        return {"data": {}}

    @app.put("/store")
    async def put_store(request: Request, payload: dict = Body(...)):
        u = user_from_internal(request) or require_user(request)
        data = payload.get("data", {})
        p = _store_path(u["sub"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

@app.get("/app")
async def app_root_redirect(request: Request):
    u = get_user(request)
    if not u:
        return RedirectResponse(url="/login")
    qs = urlencode({"u": u.get("sub", ""), "e": u.get("email", "")})
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app?{qs}")

@app.api_route("/app/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def app_any_redirect(path: str, request: Request):
    if not get_user(request):
        return RedirectResponse(url="/login")
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app/{path}")

@app.get("/")
async def root():
    return RedirectResponse(url="/app")
