import os
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, quote

from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

# Okta via Authlib (OIDC)
from authlib.integrations.starlette_client import OAuth

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret"))

# ----------------------------
# Config
# ----------------------------
STREAMLIT_INTERNAL_URL = os.environ.get("STREAMLIT_INTERNAL_URL", "http://localhost:8502").rstrip("/")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")  # ex.: https://roleta-gateway.onrender.com

# Okta
OKTA_ISSUER = os.environ.get("OKTA_ISSUER", "")             # ex.: https://<org>.okta.com/oauth2/<authz_server_id>
OKTA_CLIENT_ID = os.environ.get("OKTA_CLIENT_ID", "")
OKTA_CLIENT_SECRET = os.environ.get("OKTA_CLIENT_SECRET", "")
OKTA_METADATA_URL = os.environ.get("OKTA_METADATA_URL", "")  # opcional; se setado, usamos ele

# Canal interno painel -> gateway
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")

# DEV (fallback se Okta desabilitado)
DEV_FAKE_USER_ID = os.environ.get("DEV_FAKE_USER_ID", "")
DEV_FAKE_EMAIL = os.environ.get("DEV_FAKE_EMAIL", "")

# Mercado Pago
MP_MODE = os.environ.get("MP_MODE", "").lower().strip()  # "sandbox" ou "prod" (opcional)
PLAN_MONTHLY_ID = os.environ.get("PLAN_MONTHLY_ID", "")
PLAN_YEARLY_ID  = os.environ.get("PLAN_YEARLY_ID", "")

def _mp_env() -> str:
    """
    Determina o ambiente do Mercado Pago:
    - Se MP_MODE=sandbox/test ⇒ sandbox
    - Senão, infere pelo token: TEST-... ⇒ sandbox, caso contrário prod
    """
    if MP_MODE in ("sandbox", "teste", "test"):
        return "sandbox"
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    return "sandbox" if token.startswith("TEST-") else "prod"

# Okta OAuth client
oauth = OAuth()
OKTA_ENABLED = bool(OKTA_ISSUER and OKTA_CLIENT_ID and OKTA_CLIENT_SECRET)
if OKTA_ENABLED:
    oauth.register(
        name="okta",
        server_metadata_url=OKTA_METADATA_URL or f"{OKTA_ISSUER}/.well-known/openid-configuration",
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

# aceita chamadas internas do Streamlit quando vierem com cabeçalhos válidos
def user_from_internal(request: Request) -> Optional[dict]:
    key = request.headers.get("x-internal-key")
    if not key or key != INTERNAL_API_KEY:
        return None
    sub = request.headers.get("x-user-sub")
    email = request.headers.get("x-user-email")
    if not sub or not email:
        raise HTTPException(400, "missing x-user-sub/x-user-email")
    return {"sub": sub, "email": email}

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
    return {
        "ok": True,
        "storage": ("db" if USE_DB else "file"),
        "auth": ("okta" if OKTA_ENABLED else "dev"),
    }

@app.get("/me")
async def me(request: Request):
    u = require_user(request)
    return {"user_id": u.get("sub"), "email": u.get("email")}

# Status de assinatura (placeholder até webhook)
@app.get("/billing/status")
async def billing_status(request: Request):
    # aceita header interno OU sessão Okta
    _ = user_from_internal(request) or require_user(request)
    # TODO: integrar com DB/MP para status real por usuário
    return {"status": "active"}

# ----------------------------
# Mercado Pago: subscribe + debug + thankyou
# ----------------------------
@app.post("/billing/subscribe")
async def billing_subscribe(request: Request):
    _ = user_from_internal(request) or require_user(request)
    params = dict(request.query_params)
    plan = (params.get("plan") or "").lower()

    if plan == "monthly":
        plan_id = PLAN_MONTHLY_ID
        missing = "PLAN_MONTHLY_ID"
    elif plan == "yearly":
        plan_id = PLAN_YEARLY_ID
        missing = "PLAN_YEARLY_ID"
    else:
        raise HTTPException(400, "plan must be 'monthly' or 'yearly'")

    if not plan_id:
        raise HTTPException(400, f"Variável {missing} não configurada no gateway")

    env = _mp_env()
    mp_base = "https://sandbox.mercadopago.com.br" if env == "sandbox" else "https://www.mercadopago.com.br"
    back_url = f"{BASE_URL}/billing/thankyou" if BASE_URL else "/billing/thankyou"

    checkout_url = (
        f"{mp_base}/subscriptions/checkout"
        f"?preapproval_plan_id={quote(plan_id)}"
        f"&back_url={quote(back_url)}"
        "&auto_return=approved"
    )
    return {"init_point": checkout_url, "env": env}

@app.get("/billing/debug")
async def billing_debug():
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    return {
        "env": _mp_env(),
        "PLAN_MONTHLY_ID": os.environ.get("PLAN_MONTHLY_ID"),
        "PLAN_YEARLY_ID": os.environ.get("PLAN_YEARLY_ID"),
        "token_prefix": (token[:5] + "..." if token else None),
    }

@app.get("/billing/thankyou")
async def billing_thankyou():
    html = """
    <html>
      <head><meta charset="utf-8"><title>Assinatura</title></head>
      <body style="font-family: sans-serif; padding: 24px;">
        <h2>✅ Obrigado! Se a assinatura foi aprovada, seu acesso será liberado.</h2>
        <p>Você já pode retornar ao painel.</p>
        <p><a href="/app">Voltar ao painel</a></p>
      </body>
    </html>
    """
    return HTMLResponse(html)

# alias caso você tenha criado plano com outro back_url histórico
@app.get("/billing/return")
async def billing_return():
    return await billing_thankyou()

# ----------------------------
# STORE (DB preferencial; fallback arquivo) — aceita header interno
# ----------------------------
if USE_DB:
    @app.get("/store")
    async def get_store(request: Request):
        u = user_from_internal(request) or require_user(request)
        async with SessionLocal() as s:
            res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
            user = res.scalar_one_or_none()
            if not user:
                await s.execute(insert(User).values(okta_user_id=u["sub"], email=u.get("email", "")))
                await s.commit()
                res = await s.execute(select(User).where(User.okta_user_id == u["sub"]))
                user = res.scalar_one()
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
            if not user:
                await s.execute(insert(User).values(okta_user_id=u["sub"], email=u.get("email", "")))
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

# ----------------------------
# Redirect para o Streamlit (protegido)
# ----------------------------
@app.get("/app")
async def app_root_redirect(request: Request):
    u = get_user(request)
    if not u:
        return RedirectResponse(url="/login")
    qs = urlencode({"u": u.get("sub", ""), "e": u.get("email", "")})
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app?{qs}")

@app.api_route("/app/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def app_any_redirect(path: str, request: Request):
    # para subcaminhos do Streamlit, apenas redirecionamos (sessão já criada na navegação inicial)
    if not get_user(request):
        return RedirectResponse(url="/login")
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app/{path}")

@app.get("/")
async def root():
    return RedirectResponse(url="/app")
