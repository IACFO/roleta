import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()

# -----------------------------------------
# Sessão (mantém o desenho de auth p/ produção)
# -----------------------------------------
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret"))

# DEV: sessão fake local (sem Okta)
FAKE_UID = os.environ.get("DEV_FAKE_USER_ID", "dev-user-123")
FAKE_EMAIL = os.environ.get("DEV_FAKE_EMAIL", "dev@local.test")

# Para onde redirecionar o app (seu Streamlit)
# Rode o Streamlit com: --server.baseUrlPath=/app --server.port=8502
STREAMLIT_INTERNAL_URL = os.environ.get("STREAMLIT_INTERNAL_URL", "http://localhost:8502")

# -----------------------------------------
# DB opcional (SQLite/Postgres via SQLAlchemy async)
# Se DATABASE_URL não estiver setado OU import falhar, cai no fallback em arquivo.
# -----------------------------------------
USE_DB = bool(os.environ.get("DATABASE_URL"))
try:
    if USE_DB:
        from .db import SessionLocal, init_db
        from .models import User, Store
        from sqlalchemy import select, insert, update
except Exception as e:
    USE_DB = False

# -----------------------------------------
# Helpers
# -----------------------------------------

def require_user(request: Request):
    # Gera/garante usuário de sessão em DEV
    request.session["user"] = {"sub": FAKE_UID, "email": FAKE_EMAIL}
    u = request.session.get("user")
    if not u:
        raise HTTPException(status_code=401, detail="login required")
    return u

# ===== Persistência DEV em disco (fallback) =====
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data" / "stores"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _store_path(uid: str) -> Path:
    return DATA_DIR / f"{uid}.json"

# -----------------------------------------
# Startup
# -----------------------------------------
@app.on_event("startup")
async def _startup():
    if USE_DB:
        await init_db()
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------
# Rotas básicas
# -----------------------------------------
@app.get("/health")
async def health():
    return {"ok": True, "storage": ("db" if USE_DB else "file")}

@app.get("/me")
async def me(request: Request):
    u = require_user(request)
    return {"user_id": u["sub"], "email": u["email"], "plan": "single"}

@app.get("/billing/status")
async def billing_status(request: Request):
    require_user(request)
    # Em produção, este status virá do seu billing (Mercado Pago).
    return {"status": "active"}

# -----------------------------------------
# STORE: DB (quando disponível) ou arquivo (fallback)
# -----------------------------------------
if USE_DB:
    @app.get("/store")
    async def get_store(request: Request):
        u = require_user(request)
        async with SessionLocal() as s:
            # upsert de User mínimo (usa FAKE_UID no DEV)
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
    async def put_store(payload: dict = Body(...), request: Request = None):
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
    async def put_store(payload: dict = Body(...), request: Request = None):
        u = require_user(request)
        data = payload.get("data", {})
        p = _store_path(u["sub"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"ok": True}

# -----------------------------------------
# Modo REDIRECT para o Streamlit (DEV local)
# -----------------------------------------
@app.get("/app")
async def app_root_redirect(request: Request):
    require_user(request)
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app")

@app.api_route("/app/{path:path}", methods=["GET","POST","PUT","PATCH","DELETE"])
async def app_any_redirect(path: str, request: Request):
    require_user(request)
    return RedirectResponse(url=f"{STREAMLIT_INTERNAL_URL}/app/{path}")

@app.get("/")
async def root():
    return RedirectResponse(url="/app")
