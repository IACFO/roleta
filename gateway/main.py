import os
import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

# Auth0 (OIDC)

from authlib.integrations.starlette\_client import OAuth

app = FastAPI()
app.add\_middleware(SessionMiddleware, secret\_key=os.environ.get("SECRET\_KEY", "dev-secret"))

# ----------------------------

# Configuração

# ----------------------------

STREAMLIT\_INTERNAL\_URL = os.environ.get("STREAMLIT\_INTERNAL\_URL", "[http://localhost:8502").rstrip("/](http://localhost:8502%22%29.rstrip%28%22/)")
BASE\_URL               = os.environ.get("BASE\_URL", "").rstrip("/")

# Auth0

AUTH0\_ISSUER        = os.environ.get("OKTA\_ISSUER", "")
AUTH0\_CLIENT\_ID     = os.environ.get("OKTA\_CLIENT\_ID", "")
AUTH0\_CLIENT\_SECRET = os.environ.get("OKTA\_CLIENT\_SECRET", "")
AUTH0\_METADATA\_URL  = os.environ.get("OKTA\_METADATA\_URL", "")

# Chave interna (Streamlit -> Gateway)

INTERNAL\_API\_KEY = os.environ.get("INTERNAL\_API\_KEY", "").strip()

# DEV fallback (sem Auth0)

DEV\_FAKE\_USER\_ID = os.environ.get("DEV\_FAKE\_USER\_ID", "")
DEV\_FAKE\_EMAIL   = os.environ.get("DEV\_FAKE\_EMAIL", "")

# Mercado Pago (planos)

MP\_MONTHLY\_PLAN\_ID = os.environ.get("MP\_MONTHLY\_PLAN\_ID", "")
MP\_YEARLY\_PLAN\_ID  = os.environ.get("MP\_YEARLY\_PLAN\_ID", "")

# Banco (opcional)

USE\_DB = bool(os.environ.get("DATABASE\_URL"))
if USE\_DB:
try:
from .db import SessionLocal, init\_db
from .models import User, Store
from sqlalchemy import select, insert, update
except Exception:
USE\_DB = False

# Arquivo (fallback para store)

DATA\_DIR = Path(os.environ.get("DATA\_DIR", Path(**file**).parent / "data" / "stores"))
DATA\_DIR.mkdir(parents=True, exist\_ok=True)

def \_store\_path(uid: str) -> Path:
return DATA\_DIR / f"{uid}.json"

# Auth0 OAuth

oauth = OAuth()
AUTH0\_ENABLED = bool(AUTH0\_ISSUER and AUTH0\_CLIENT\_ID and AUTH0\_CLIENT\_SECRET)
if AUTH0\_ENABLED:
oauth.register(
name="auth0",
server\_metadata\_url=AUTH0\_METADATA\_URL or f"{AUTH0\_ISSUER}/.well-known/openid-configuration",
client\_id=AUTH0\_CLIENT\_ID,
client\_secret=AUTH0\_CLIENT\_SECRET,
client\_kwargs={"scope": "openid profile email"},
)

# ----------------------------

# Helpers de auth

# ----------------------------

def get\_user(request: Request) -> Optional\[dict]:
return request.session.get("user")

def require\_user(request: Request) -> dict:
u = get\_user(request)
if u:
return u
if not AUTH0\_ENABLED and DEV\_FAKE\_USER\_ID and DEV\_FAKE\_EMAIL:
u = {"sub": DEV\_FAKE\_USER\_ID, "email": DEV\_FAKE\_EMAIL}
request.session\["user"] = u
return u
raise HTTPException(status\_code=401, detail="login required")

def user\_from\_internal(request: Request) -> Optional\[dict]:
key = request.headers.get("x-internal-key")
if not key or key != INTERNAL\_API\_KEY:
return None
sub = request.headers.get("x-user-sub")
email = request.headers.get("x-user-email")
if not sub or not email:
raise HTTPException(status\_code=400, detail="missing x-user-sub/x-user-email")
return {"sub": sub, "email": email}

@app.on\_event("startup")
async def \_startup():
if USE\_DB:
await init\_db()
else:
DATA\_DIR.mkdir(parents=True, exist\_ok=True)

@app.get("/login")
async def login(request: Request):
if not AUTH0\_ENABLED:
if DEV\_FAKE\_USER\_ID and DEV\_FAKE\_EMAIL:
request.session\["user"] = {"sub": DEV\_FAKE\_USER\_ID, "email": DEV\_FAKE\_EMAIL}
return RedirectResponse(url="/app")
raise HTTPException(500, "Auth0 não configurado e DEV\_FAKE\_\* ausentes.")
if not BASE\_URL:
raise HTTPException(500, "BASE\_URL não configurado.")
redirect\_uri = f"{BASE\_URL}/callback"
return await oauth.auth0.authorize\_redirect(request, redirect\_uri)

@app.get("/callback")
async def auth\_callback(request: Request):
if not AUTH0\_ENABLED:
return RedirectResponse(url="/app")
token = await oauth.auth0.authorize\_access\_token(request)
userinfo = token.get("userinfo")
if not userinfo:
userinfo = await oauth.auth0.parse\_id\_token(request, token)
request.session\["user"] = {
"sub": userinfo.get("sub"),
"email": userinfo.get("email"),
"name": userinfo.get("name"),
}
request.session\["id\_token"] = token.get("id\_token")
return RedirectResponse(url="/app")

@app.get("/logout")
async def logout(request: Request):
id\_token = request.session.pop("id\_token", None)
request.session.clear()
if AUTH0\_ENABLED and id\_token and BASE\_URL:
return RedirectResponse(
url=f"{AUTH0\_ISSUER}/v1/logout?id\_token\_hint={id\_token}\&post\_logout\_redirect\_uri={quote(BASE\_URL + '/')}"
)
return RedirectResponse(url="/")

@app.get("/health")
async def health():
return {
"ok": True,
"storage": ("db" if USE\_DB else "file"),
"auth": ("auth0" if AUTH0\_ENABLED else "dev"),
}

@app.get("/me")
async def me(request: Request):
u = user\_from\_internal(request) or require\_user(request)
return {"user\_id": u.get("sub"), "email": u.get("email")}

@app.get("/billing/status")
async def billing\_status(request: Request):
u = user\_from\_internal(request) or require\_user(request)
if USE\_DB:
async with SessionLocal() as s:
res = await s.execute(select(User).where(User.okta\_user\_id == u\["sub"]))
user = res.scalar\_one\_or\_none()
if user and user.access\_expires\_at:
if datetime.utcnow() > user.access\_expires\_at:
return {"status": "expired"}
return {"status": "active"}

@app.post("/billing/subscribe")
async def billing\_subscribe(request: Request, plan: str = "monthly"):
u = user\_from\_internal(request) or require\_user(request)
if USE\_DB:
async with SessionLocal() as s:
res = await s.execute(select(User).where(User.okta\_user\_id == u\["sub"]))
user = res.scalar\_one\_or\_none()
if user:
await s.execute(update(User).where(User.id == user.id).values(access\_expires\_at=datetime.utcnow() + timedelta(days=365)))
await s.commit()
plan\_id = MP\_YEARLY\_PLAN\_ID
if not plan\_id:
raise HTTPException(400, detail="plan 'yearly' sem PLAN\_ID configurado")
if not BASE\_URL:
raise HTTPException(500, detail="BASE\_URL não configurado")
back\_url = f"{BASE\_URL}/billing/thankyou"
init\_point = (
"[https://www.mercadopago.com.br/subscriptions/checkout](https://www.mercadopago.com.br/subscriptions/checkout)"
f"?preapproval\_plan\_id={quote(plan\_id)}"
f"\&back\_url={quote(back\_url)}"
f"\&auto\_return=approved"
)
return {"init\_point": init\_point, "plan": plan, "user": u}

@app.get("/billing/thankyou")
async def billing\_thankyou():
return RedirectResponse(url="/app")

# ----------------------------

# STORE

# ----------------------------

if USE\_DB:
@app.get("/store")
async def get\_store(request: Request):
u = user\_from\_internal(request) or require\_user(request)
async with SessionLocal() as s:
res = await s.execute(select(User).where(User.okta\_user\_id == u\["sub"]))
user = res.scalar\_one\_or\_none()
if not user:
await s.execute(insert(User).values(okta\_user\_id=u\["sub"], email=u.get("email", "")))
await s.commit()
res = await s.execute(select(User).where(User.okta\_user\_id == u\["sub"]))
user = res.scalar\_one()
res = await s.execute(select(Store).where(Store.user\_id == user.id))
st\_row = res.scalar\_one\_or\_none()
return {"data": st\_row\.data if st\_row else {}}

```
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
```

else:
@app.get("/store")
async def get\_store(request: Request):
u = user\_from\_internal(request) or require\_user(request)
p = \_store\_path(u\["sub"])
if p.exists():
try:
return {"data": json.loads(p.read\_text(encoding="utf-8"))}
except Exception:
return {"data": {}}
return {"data": {}}

```
@app.put("/store")
async def put_store(request: Request, payload: dict = Body(...)):
    u = user_from_internal(request) or require_user(request)
    data = payload.get("data", {})
    p = _store_path(u["sub"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}
```

# ----------------------------

# Redirecionamento p/ Streamlit

# ----------------------------

@app.get("/app")
async def app\_root\_redirect(request: Request):
u = get\_user(request)
if not u:
return RedirectResponse(url="/login")
qs = urlencode({"u": u.get("sub", ""), "e": u.get("email", "")})
return RedirectResponse(url=f"{STREAMLIT\_INTERNAL\_URL}/app?{qs}")

@app.api\_route("/app/{path\:path}", methods=\["GET", "POST", "PUT", "PATCH", "DELETE"])
async def app\_any\_redirect(path: str, request: Request):
if not get\_user(request):
return RedirectResponse(url="/login")
return RedirectResponse(url=f"{STREAMLIT\_INTERNAL\_URL}/app/{path}")

@app.get("/")
async def root():
return RedirectResponse(url="/app")
