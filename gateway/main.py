import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

# Okta via Authlib (OIDC)
from authlib.integrations.starlette_client import OAuth

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret"))

# ----------------------------
# Config
# ----------------------------
STREAMLIT_INTERNAL_URL = os.environ.get("STREAMLIT_INTERNAL_URL", "http://localhost:8502")

OKTA_ISSUER = os.environ.get("OKTA_ISSUER", "")            # ex.: https://<org>.okta.com/oauth2/<authz_server_id>
OKTA_CLIENT_ID = os.environ.get("OKTA_CLIENT_ID", "")
OKTA_CLIENT_SECRET = os.environ.get("OKTA_CLIENT_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "")                  # ex.: https://roleta-gateway.onrender.com

# Fallback DEV (apenas se Okta não estiver habilitado)
DEV_FAKE_USER_ID = os.environ.get("DEV_FAKE_USER_ID", "")
DEV_FAKE_EMAIL = os.environ.get("DEV_FAKE_EMAIL", "")

# Okta OAuth client
oauth = OAuth()
OKTA_ENABLED = bool(OKTA_ISSUER and OKTA_CLIENT_ID and OKTA_CLIENT_SECRET)
if OKTA_ENABLED:
    oauth.register(
        name="okta",
        server_metadata_url=f"{OKTA_ISSUER}/.well-known/openid-configuration",
        client_id=OKTA_CLIENT_ID,
        client_secret=OKTA_CLIENT_SECRET,
        client_kwargs={"scope": "openid profile email"},
    )

# ----------------------------
# Auth helpers
# ----------------------------

def get_user(request: Request):
    return request.session.get("user")

def require_user(request: Request):
    u = get_user(request)
    if u:
        return u
    # Fallback DEV: somente se Okta não estiver habilitado
    if not OKTA_ENABLED and DEV_FAKE_USER_ID and DEV_FAKE_EMAIL:
        u = {"sub": DEV_FAKE_USER_ID, "email": DEV_FAKE_EMAIL}
        request.session["user"] = u
        return u
    raise HTTPException(status_code=401, detail="login required")

# ----------------------------
# DB wiring (SQLAlchemy async) com fallback para arquivo
# ----------------------------
USE_DB = bool(os.environ.get("DATABASE_URL"))
try:
    if USE_DB:
        from .db import SessionLocal, init_db
        from .models import User, Store
        from sqlalchemy import select, insert, update
except Exception:
    USE_DB = False

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data" / "stores"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _store_path(uid: str) -> Path:
    return DATA_DIR / f"{uid}.json"

# ----------------------------
# Startup
# ----------------------------
@app.on_event("startup")
async def _startup():
    if USE_DB:
        await init_db()
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------
# Auth routes (Okta)
# ----------------------------
@app.get("/login")
async def login(request: Request):
    if not OKTA_ENABLED:
        # DEV sem Okta → cria sessão fake
        if DEV_FAKE_USER_ID and DEV_FAKE_EMAIL:
            request.session["user"] = {"sub": DEV_FAKE_USER_ID, "email": DEV_FAKE_EMAIL}
            return RedirectResponse(url="/app")
        raise HTTPException(500, "Okta não configurado e DEV_FAKE_* ausentes.")
    if not BASE_URL:
        raise HTTPException(500, "BASE_URL não configurado.")
    redirect_uri = f"{BASE_URL}/callback"
    return await oauth.okta.authorize_redirect(request, redirect_uri)

@app.get("/callback")
async def auth_callback(request: Request):
    if not OKTA_ENABLED:
        return RedirectResponse(url="/app")
    token = await oauth.okta.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await oauth.okta.parse_id_token(request, token)
    request.session["user"] = {
        "sub": userinfo.get("sub"),
        "email": userinfo.get("email"),
        "name": userinfo.get("name"),
    }
    request.session["id_token"] = token.get("id_token")
    return RedirectResponse(url="/app")

@app.get("/logout")
async def logout(request: Request):
    id_token = request.session.pop("id_token", None)
    request.session.clear()
    if OKTA_ENABLED and id_token and BASE_URL:
        return RedirectResponse(
            url=f"{OKTA_ISSUER}/v1/logout?id_token_hint={id_token}&post_logout_redirect_uri={BASE_URL}/"
        )
    return RedirectResponse(url="/")

# ----------------------------
# Básico
# ----------------------------
@app.get("/health")
async def health():
    return {"ok": True, "storage": ("db" if USE_DB else "file"), "auth": ("okta" if OKTA_ENABLED else "dev")}

@app.get("/me")
async def me(request: Request):
    u = require_user(request)
    return {"user_id": u.get("sub"), "email": u.get("email")}

@app.get("/billing/status")
async def billing_status(request: Request):
    require_user(request)
    # Placeholder: voltaremos aqui quando integrar Mercado Pago
    return {"status": "active"}

# ----------------------------
# STORE (DB preferencial; fallback arquivo)
# ----------------------------
if USE_DB:
    @app.get("/store")
    async def get_store(request: Request):
        u = require_user(request)
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
            user = res.scalar_one_or_none()
            if not user:
                await s.execute(insert(User).values(okta_user_id=u["sub"], email=u["email"]))
                await s.commit()
                res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
                user = res.scalar_one()
            res = await s.execute(select(Store).where(Store.user_id == user.id))
            st_row = res.scalar_one_or_none()
            return {"data": st_row.data if st_row else {}}

    @app.put("/store")
    async def put_store(request: Request, payload: dict = Body(...)):
        u = require_user(request)
        data = payload.get("data", {})
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
            user = res.scalar_one_or_none()
            if not user:
                await s.execute(insert(User).values(okta_user_id=u["sub"], email=u["email"]))
                await s.commit()
                res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
                user = res.scalar_one()
            res2 = await s.execute(select(Store).where(Store.user_id == user.id))
            st_row = res2.scalar_one_or_none()
            if st_row:
                await s.execute(update(Store).where(Store.user_id == user.id).values(data=data))
            else:
                await s.execute(insert(Store).values(user_id=user.id, data=data))
            await s.commit()
        return {"ok": True}
else:
    @app.get("/store")
    async def get_store(request: Request):
        u = require_user(request)
        p = _store_path(u["sub"])
        if p.exists():
            try:
                return {"data": json.loads(p.read_text(encoding="utf-8"))}
            except Exception:
                return {"data": {}}
        return {"data": {}}

    @app.put("/store")
    async def put_store(request: Request, payload: dict = Body(...)):
        u = require_user(request)
        data = payload.get("data", {})
        p = _store_path(u["sub"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

# ----------------------------
# Redirect para o Streamlit (protegido)
# ----------------------------
@app.get("/app")
async def app_root_redirect(request: Request):
    if not get_user(request):
        return RedirectResponse(url="/login")
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app")

@app.api_route("/app/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def app_any_redirect(path: str, request: Request):
    if not get_user(request):
        return RedirectResponse(url="/login")
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app/{path}")

@app.get("/")
async def root():
    return RedirectResponse(url="/app")
