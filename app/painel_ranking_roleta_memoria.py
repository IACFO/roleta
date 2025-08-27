# painel_ranking_roleta_memoria.py
# RoletaSmart — Painel de Gerenciamento e Estratégias
# Versão com integração ao gateway via headers internos (INTERNAL_API_KEY)
# e tratamento de 401 (redireciono para login do gateway/Auth0).


import os
import io
import json
import requests
import pandas as pd
import streamlit as st
from pathlib import Path


# =========================
# Config da página
# =========================
st.set_page_config(page_title="Ranking Roleta com Memória", layout="wide")
st.title("📊 Roleta Smart - Painel de Gerenciamento e Estratégias")


# =========================
# Integração com Gateway (API)
# =========================
API_BASE = os.environ.get("API_BASE", "http://localhost:8001").rstrip("/")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "").strip()
LOGIN_URL = os.environ.get("LOGIN_URL", "https://roleta-gateway.onrender.com/app")


# pega u/e da URL (?u=...&e=...)
_qp = st.query_params if hasattr(st, "query_params") else st.experimental_get_query_params()
def _first(v): return v[0] if isinstance(v, list) else v
USER_SUB = _first(_qp.get("u"))
USER_EMAIL = _first(_qp.get("e"))


def _auth_headers():
    h = {}
    if INTERNAL_API_KEY:
        h["x-internal-key"] = INTERNAL_API_KEY
    if USER_SUB:
        h["x-user-sub"] = USER_SUB
    if USER_EMAIL:
        h["x-user-email"] = USER_EMAIL
    return h



def api_get(path: str):
    r = requests.get(f"{API_BASE}{path}", headers=_auth_headers(), timeout=15)
    return r


def api_put(path: str, json_data: dict):
    r = requests.put(f"{API_BASE}{path}", json=json_data, headers=_auth_headers(), timeout=20)
    return r


# =========================
# Checagem de sessão/assinatura (com tratamento de 401)
# =========================
try:
    r_me = api_get("/me")
    if r_me.status_code == 401:
        st.error("Você precisa entrar para usar o painel.")
        st.link_button("🔐 Entrar no painel", LOGIN_URL, use_container_width=True)
        st.stop()
    r_me.raise_for_status()

    r_billing = api_get("/billing/status")
    if r_billing.status_code == 401:
        st.error("Você precisa entrar para usar o painel.")
        st.link_button("🔐 Entrar no painel", LOGIN_URL, use_container_width=True)
        st.stop()
    r_billing.raise_for_status()
    billing = r_billing.json()

except requests.RequestException as e:
    st.error(f"❌ Não foi possível conectar ao gateway/API em {API_BASE}. Detalhe: {e}")
    st.stop()


if billing.get("status") != "active":
    st.warning("Sua licença ainda não está ativa.")
    if st.button("💳 Ativar Licença Anual (R$89,90)"):
        try:
            r = requests.post(
                f"{API_BASE}/billing/subscribe",
                params={"plan": "yearly"},
                headers=_auth_headers(),
                timeout=20,
            )
            r.raise_for_status()
            st.stop()
        except requests.RequestException as e:
            st.error(f"Erro ao tentar ativar licença: {e}")

# Continuação do painel original abaixo...
# [o restante do código permanece inalterado e continuará após este ponto de verificação de login/assinatura]


# =========================
# Perfil (1u = R$1)
# =========================
UNIDADE_REAIS = 1.0  # 1u = R$1

PERFIS = {
    "Conservador": {
        "soft_mult": 1.7, "forte_mult": 2.3, "extremo_pct": 0.80,
        "cap_expo": {
            "Cavalos": 6, "Setor": 12, "Dúzia": 2, "Coluna": 2, "Cor": 2, "Paridade": 2, "Metade": 2
        },
        "stake_por_numero": {"Cavalos": 1, "Setor": 1}
    },
    "Moderado": {
        "soft_mult": 1.5, "forte_mult": 2.0, "extremo_pct": 0.70,
        "cap_expo": {
            "Cavalos": 9, "Setor": 12, "Dúzia": 3, "Coluna": 3, "Cor": 3, "Paridade": 3, "Metade": 3
        },
        "stake_por_numero": {"Cavalos": 1, "Setor": 1}
    },
    "Agressivo": {
        "soft_mult": 1.3, "forte_mult": 1.7, "extremo_pct": 0.60,
        "cap_expo": {
            "Cavalos": 12, "Setor": 16, "Dúzia": 4, "Coluna": 4, "Cor": 4, "Paridade": 4, "Metade": 4
        },
        "stake_por_numero": {"Cavalos": 2, "Setor": 1}
    }
}

perfil = st.sidebar.selectbox("Perfil de risco", list(PERFIS.keys()), index=1)
banca_total = st.sidebar.number_input("Banca (R$) – referência", min_value=0.0, value=1000.0, step=50.0)
st.sidebar.markdown(f"**Unidade (1u):** R$ {UNIDADE_REAIS:.2f}")

soft_mult   = PERFIS[perfil]["soft_mult"]
forte_mult  = PERFIS[perfil]["forte_mult"]
extremo_pct = PERFIS[perfil]["extremo_pct"]
cap_expo    = PERFIS[perfil]["cap_expo"]
stake_n_por = PERFIS[perfil]["stake_por_numero"]

with st.sidebar.expander("📘 Tutorial do Painel", expanded=False):
    st.markdown("""
### 👋 Bem-vindo ao Painel de Estratégias RoletaSmart

Este painel foi criado para ajudar você a tomar decisões mais estratégicas ao analisar os padrões de comportamento da roleta.

---

### 🔢 Inserção de Números

- Use o campo **"Insira números"** para digitar os resultados da roleta (ex: `23, 8, 17`).
- Os números são acumulados no histórico.

---

### 🧠 Como o painel analisa

- O sistema monitora os principais tipos de aposta (cor, dúzia, coluna, setor, etc.).
- Calcula sequências atuais e ausências, médias e máximos.
- Classifica oportunidades como **neutras**, **fortes**, **extremas**, com base na leitura comportamental de apostadores experientes.

---

### 🎯 Sugestões de Entrada

- O painel exibe duas sugestões:
  - **Principal (valor)**: baseada em padrões longos de ausência.
  - **Complementar (barata)**: baseada em sequência ou quebra de padrão.

---

### ⚠️ Importante

- Este painel **não é um sistema de apostas**.
- Ele **não garante lucro**. É uma **ferramenta de apoio à análise**.
- Use com responsabilidade.

---

### ❓Dúvidas?

🔗 [Entre em contato com o suporte ou acesse](https://chat.whatsapp.com/EhVB89mUzN5HGIabDtbfCC).

---
    """)

with st.sidebar.expander("💬 Comunidade / Suporte", expanded=False):
    st.markdown("""
Entre em nossa **comunidade oficial no WhatsApp** para:

- Tirar dúvidas sobre o uso do painel.
- Receber dicas e atualizações.
- Compartilhar experiências com outros usuários.

👉 **Atenção**: o grupo é exclusivo para assinantes e será usado também como canal de suporte direto.

🔗 [Clique aqui para entrar no grupo](https://chat.whatsapp.com/EhVB89mUzN5HGIabDtbfCC)
    """)

# =========================
# Tipos monitorados
# =========================
TIPOS = (
    ["Vermelho","Preto","Par","Ímpar","Metade 1-18","Metade 19-36","Dúzia 1","Dúzia 2","Dúzia 3"] +
    ["Coluna 1","Coluna 2","Coluna 3"] +
    ["Cavalos 1-4-7","Cavalos 2-5-8","Cavalos 3-6-9"] +
    ["Voisins","Tiers","Orphelins"]
)

# =========================
# Persistência via API (por usuário logado)
# =========================
def load_store():
    try:
        resp = api_get("/store")
        data = resp.get("data") or {}
    except Exception:
        data = {}

    # migração (compatível com estrutura antiga simples)
    if data and all(isinstance(v, int) for v in data.values()):
        old = data; data = {}
        for t in TIPOS:
            data[t] = {"seq_max": int(old.get(t,0)), "seq_media": 0.0, "seq_n": 0,
                       "aus_max": 0, "aus_media": 0.0, "aus_n": 0}
        try:
            api_put("/store", {"data": data})
        except Exception:
            pass
    else:
        new = {}
        for t in TIPOS:
            rec = data.get(t, {})
            rec.setdefault("seq_max",0); rec.setdefault("seq_media",0.0); rec.setdefault("seq_n",0)
            rec.setdefault("aus_max",0); rec.setdefault("aus_media",0.0); rec.setdefault("aus_n",0)
            new[t] = rec
        data = new
        try:
            api_put("/store", {"data": data})
        except Exception:
            pass
    return data

def save_store(store: dict):
    try:
        api_put("/store", {"data": store})
    except Exception:
        pass

store = load_store()

# =========================
# Estado de sessão
# =========================
if "historico" not in st.session_state:
    st.session_state.historico = []
if "zerar_sequencias_view" not in st.session_state:
    st.session_state.zerar_sequencias_view = False

# =========================
# Entrada
# =========================
entrada = st.text_input("🔢 Insira números (0–36) separados por vírgula (acumula):")
colA, colB, colC, colD = st.columns(4)
with colA:
    if st.button("➕ Inserir"):
        if entrada.strip():
            try:
                novos = [int(x.strip()) for x in entrada.split(",") if x.strip().isdigit()]
                for v in novos:
                    if v < 0 or v > 36: raise ValueError
                st.session_state.historico.extend(novos)
            except Exception:
                st.warning("Entrada inválida. Use apenas números 0–36 separados por vírgula.")
with colB:
    if st.button("🔄 Resetar SEQUÊNCIAS"):
        st.session_state.zerar_sequencias_view = True
        st.success("Sequências zeradas na exibição (histórico/médias/máximos preservados).")
with colC:
    if st.button("🧹 Limpar MÁXIMOS/MÉDIAS (zera memória)"):
        for t in TIPOS:
            store[t] = {"seq_max":0,"seq_media":0.0,"seq_n":0,"aus_max":0,"aus_media":0.0,"aus_n":0}
        save_store(store)
        st.success("Memória zerada no servidor.")
with colD:
    st.write(f"📦 Itens monitorados: **{len(TIPOS)}**")

numeros = st.session_state.historico
if len(numeros) < 5:
    st.info("Insira ao menos 5 números para construir os rankings.")
    st.stop()

# =========================
# Classificações por número
# =========================
vermelho = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
preto    = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}
coluna_map = {i: f"Coluna {((i-1)%3)+1}" for i in range(1,37)}
setor_map = {
    **dict.fromkeys([22,18,29,7,28,12,35,3,26,0,32,15,19,4,21,2,25], "Voisins"),
    **dict.fromkeys([27,13,36,11,30,8,23,10,5,24,16,33], "Tiers"),
    **dict.fromkeys([1,20,14,31,9,17,34,6], "Orphelins")
}

def cavalo_do_numero(n:int):
    if n == 0: return None
    d = n % 10
    if d in (1,4,7): return "Cavalos 1-4-7"
    if d in (2,5,8): return "Cavalos 2-5-8"
    if d in (3,6,9): return "Cavalos 3-6-9"
    return None

def metade_do_numero(n:int):
    if n == 0: return None
    return "Metade 1-18" if 1 <= n <= 18 else "Metade 19-36"

def tipos_do_numero(n:int):
    out = []
    if n in vermelho: out.append("Vermelho")
    elif n in preto:  out.append("Preto")
    if n != 0: out.append("Par" if n % 2 == 0 else "Ímpar")
    metade = metade_do_numero(n)
    if metade: out.append(metade)
    if 1<=n<=12: out.append("Dúzia 1")
    elif 13<=n<=24: out.append("Dúzia 2")
    elif 25<=n<=36: out.append("Dúzia 3")
    if n != 0: out.append(coluna_map[n])
    if n in setor_map: out.append(setor_map[n])  # inclui 0 em Voisins
    cav = cavalo_do_numero(n)
    if cav: out.append(cav)
    return out

def grupo_de(tipo:str)->str:
    if tipo in ("Vermelho","Preto"): return "Cor"
    if tipo in ("Par","Ímpar"): return "Paridade"
    if tipo.startswith("Metade"): return "Metade"
    if tipo.startswith("Dúzia"): return "Dúzia"
    if tipo.startswith("Coluna"): return "Coluna"
    if tipo.startswith("Cavalos"): return "Cavalos"
    if tipo in ("Voisins","Tiers","Orphelins"): return "Setor"
    return "Outro"

# =========================
# Corridas + persistência
# =========================
cur_seq = {t:0 for t in TIPOS}
cur_gap = {t:0 for t in TIPOS}

def update_mean(old_mean, old_n, val):
    return (old_mean*old_n + val)/(old_n+1), (old_n+1)

for n in numeros:
    ativos = set(tipos_do_numero(n))
    for t in TIPOS:
        if t in ativos:
            if cur_gap[t] > 0:
                rec = store[t]
                if cur_gap[t] > rec["aus_max"]: rec["aus_max"] = cur_gap[t]
                rec["aus_media"], rec["aus_n"] = update_mean(rec["aus_media"], rec["aus_n"], cur_gap[t])
                store[t] = rec; cur_gap[t] = 0
            cur_seq[t] += 1
        else:
            if cur_seq[t] > 0:
                rec = store[t]
                if cur_seq[t] > rec["seq_max"]: rec["seq_max"] = cur_seq[t]
                rec["seq_media"], rec["seq_n"] = update_mean(rec["seq_media"], rec["seq_n"], cur_seq[t])
                store[t] = rec; cur_seq[t] = 0
            cur_gap[t] += 1

# Salva memória consolidada do usuário
save_store(store)

# =========================
# DataFrames
# =========================
df_cont = pd.DataFrame({
    "Tipo": TIPOS,
    "Rodadas seguidas": [cur_seq[t] for t in TIPOS],
    "Média quebra (seq)": [round(store[t]["seq_media"],2) for t in TIPOS],
    "Máxima sequência": [store[t]["seq_max"] for t in TIPOS],
})
df_aus = pd.DataFrame({
    "Tipo": TIPOS,
    "Rodadas ausente": [cur_gap[t] for t in TIPOS],
    "Média ausência": [round(store[t]["aus_media"],2) for t in TIPOS],
    "Máxima ausência": [store[t]["aus_max"] for t in TIPOS],
})

# Reset visual
if st.session_state.zerar_sequencias_view:
    df_cont["Rodadas seguidas"] = 0
    df_aus["Rodadas ausente"] = 0
    st.session_state.zerar_sequencias_view = False

# =========================
# Regras de leitura
# =========================
SEQ_RULES = {
    "Setor":     {"neutro_max": 2, "med": (3,5), "forte": (6,7), "ext": (8, 10**6)},
    "Cor":       {"neutro_max": 2, "med": (3,5), "forte": (6,9), "ext": (10, 10**6)},
    "Paridade":  {"neutro_max": 2, "med": (3,5), "forte": (6,9), "ext": (10, 10**6)},
    "Metade":    {"neutro_max": 2, "med": (3,5), "forte": (6,9), "ext": (10, 10**6)},
    "Coluna":    {"neutro_max": 2, "med": (3,4), "forte": (5,6), "ext": (7, 10**6)},
    "Dúzia":     {"neutro_max": 2, "med": (3,4), "forte": (5,6), "ext": (7, 10**6)},
    "Cavalos":   {"neutro_max": 1, "med": (2,3), "forte": (4,4), "ext": (5, 10**6)},
}

ABS_RULES = {
    "Setor":   {"neutro": 4, "oposto_ini": 5,  "oposto_fim": 22, "retorno_min": 23},
    "Coluna":  {"neutro": 4, "oposto_ini": 5,  "oposto_fim": 15, "retorno_min": 16},
    "Dúzia":   {"neutro": 4, "oposto_ini": 5,  "oposto_fim": 15, "retorno_min": 16},
    "Cavalos": {"neutro": 4, "oposto_ini": 5,  "oposto_fim": 10, "retorno_min": 11},
    # Cor / Paridade / Metade: usam a mesma gradação da regra de sequência
}

def classify_abs(tipo, aus):
    g = grupo_de(tipo)
    if g in ("Cor","Paridade","Metade"):
        r = SEQ_RULES[g]
        if aus <= r["neutro_max"]: return "neutro", "Ausência neutra"
        if r["med"][0] <= aus <= r["med"][1]: return "retorno_médio", "Retorno (ausência ~média 3–5)"
        if r["forte"][0] <= aus <= r["forte"][1]: return "retorno_forte", "Retorno forte (6–9)"
        if aus >= r["ext"][0]: return "retorno_extremo", "Retorno extremo (10+)"
        return "neutro",""
    if g in ABS_RULES:
        r = ABS_RULES[g]
        if aus <= r["neutro"]: return "neutro", f"Neutro até {r['neutro']}"
        if r["oposto_ini"] <= aus <= r["oposto_fim"]:
            return "oposto", f"Apostar **OPOSTO** ({r['oposto_ini']}–{r['oposto_fim']})"
        if aus >= r["retorno_min"]:
            return "retorno", f"Quebrar ausência (≥ {r['retorno_min']})"
    return "neutro",""

def classify_seq(tipo, seq):
    g = grupo_de(tipo)
    r = SEQ_RULES.get(g)
    if not r: return "neutro",""
    if seq <= r["neutro_max"]: return "neutro", f"Neutro até {r['neutro_max']}"
    if r["med"][0] <= seq <= r["med"][1]: return "quebrar_médio", "Quebrar sequência (médio)"
    if r["forte"][0] <= seq <= r["forte"][1]: return "quebrar_forte", "Quebra forte"
    if seq >= r["ext"][0]: return "quebrar_extremo", "Quebra extrema"
    return "neutro",""

df_aus[["Sinal_aus","Motivo_aus"]] = df_aus.apply(lambda r: pd.Series(classify_abs(r["Tipo"], int(r["Rodadas ausente"]))), axis=1)
df_cont[["Sinal_cont","Motivo_cont"]] = df_cont.apply(lambda r: pd.Series(classify_seq(r["Tipo"], int(r["Rodadas seguidas"]))), axis=1)

# Ordenação amigável
df_aus  = df_aus.sort_values(["Sinal_aus","Rodadas ausente","Máxima ausência"], ascending=[True,False,False]).reset_index(drop=True)
df_cont = df_cont.sort_values(["Sinal_cont","Rodadas seguidas","Máxima sequência"], ascending=[True,False,False]).reset_index(drop=True)

# =========================
# Tabelas com destaque
# =========================
def style_abs(row):
    s = row["Sinal_aus"]
    if s == "oposto": return "background-color: #fff3cd"
    if s == "retorno": return "background-color: #ffd27f"
    if s == "retorno_médio": return "background-color: #fff8d6"
    if s == "retorno_forte": return "background-color: #ffefb3"
    if s == "retorno_extremo": return "background-color: #ffc266"
    return ""

def style_seq(row):
    s = row["Sinal_cont"]
    if s == "quebrar_médio": return "background-color: #ffe7e7"
    if s == "quebrar_forte": return "background-color: #ffcccc"
    if s == "quebrar_extremo": return "background-color: #ffb3b3"
    return ""

c1, c2 = st.columns(2)
with c1:
    st.subheader("🔴 Ranking de Ausência")
    st.dataframe(df_aus.style.apply(lambda r: [style_abs(r)]*len(r), axis=1), use_container_width=True)
with c2:
    st.subheader("🟢 Ranking de Continuidade")
    st.dataframe(df_cont.style.apply(lambda r: [style_seq(r)]*len(r), axis=1), use_container_width=True)

# =========================
# Exportar XLSX
# =========================
def build_excel_bytes(df_aus, df_cont):
    buffer = io.BytesIO()
    engine = None
    try:
        import xlsxwriter  # noqa
        engine = "xlsxwriter"
    except Exception:
        try:
            import openpyxl  # noqa
            engine = "openpyxl"
        except Exception:
            return None, "Instale 'xlsxwriter' ou 'openpyxl' para exportar XLSX."
    with pd.ExcelWriter(buffer, engine=engine) as writer:
        df_aus.to_excel(writer, sheet_name="Ausência", index=False)
        df_cont.to_excel(writer, sheet_name="Continuidade", index=False)
    return buffer.getvalue(), None

xlsx_bytes, err = build_excel_bytes(df_aus, df_cont)
if err:
    st.warning(f"📄 Exportação desabilitada: {err}")
else:
    st.download_button(
        "📥 Baixar ranking (.xlsx)",
        data=xlsx_bytes,
        file_name="ranking_roleta.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# =========================
# Sugestões (2 por rodada)
# =========================
st.subheader("🎯 Sugestões da rodada")

SETOR_SIZE = {"Voisins": 17, "Tiers": 12, "Orphelins": 8}
CAVALOS_SIZE = {"Cavalos 1-4-7": 12, "Cavalos 2-5-8": 12, "Cavalos 3-6-9": 12}

def sugestao_principal(df_aus):
    # prioridade: Cavalos > Setor > Dúzia > Coluna > Metade > Paridade > Cor
    ordem = ["Cavalos","Setor","Dúzia","Coluna","Metade","Paridade","Cor"]
    cand = df_aus[df_aus["Sinal_aus"].isin(["oposto","retorno","retorno_médio","retorno_forte","retorno_extremo"])].copy()
    if cand.empty: return None
    for grp in ordem:
        sub = cand[[grupo_de(t)==grp for t in cand["Tipo"]]]
        if sub.empty: continue
        r = sub.iloc[0]
        tipo, g, sinal = r["Tipo"], grp, r["Sinal_aus"]

        if g == "Cavalos":
            n_nums = 12; stake = stake_n_por.get("Cavalos",1)
            expo = min(cap_expo["Cavalos"], n_nums * stake)
            acao = "Apostar **CAVALOS** no grupo"
        elif g == "Setor":
            base_n = SETOR_SIZE.get(tipo, 12)
            n_nums = min(base_n, cap_expo["Setor"]); stake = stake_n_por.get("Setor",1)
            expo = n_nums * stake; acao = "Apostar **SETOR**"
        elif g in ("Dúzia","Coluna","Metade","Paridade","Cor"):
            expo = min(1, cap_expo[g]); n_nums = None; acao = "Apostar"
        else:
            expo = 1; n_nums = None; acao = "Apostar"

        if sinal == "oposto":
            racional = f"Ausência média superada → **apostar OPOSTO** ({r['Motivo_aus']})."
        elif sinal.startswith("retorno"):
            racional = f"Ausência alongada → **retorno do AUSENTE** ({r['Motivo_aus']})."
        else:
            racional = r["Motivo_aus"]

        if n_nums:
            txt = f"{acao} **{tipo}** — cobrir ~{n_nums} nº × {stake}u (exposição **{expo}u / R${expo*UNIDADE_REAIS:.2f}**)"
        else:
            txt = f"{acao} **{tipo}** — {expo}u (R${expo*UNIDADE_REAIS:.2f})"
        detalhe = f" | Aus: {int(r['Rodadas ausente'])} • Média: {r['Média ausência']} • Máx: {r['Máxima ausência']}"
        return txt, (racional + detalhe)
    return None

def sugestao_complementar(df_cont):
    # barato: Cor/Paridade/Coluna/Dúzia/Metade — prioriza quebrar forte/extremo
    sub = df_cont[[grupo_de(t) in ("Cor","Paridade","Coluna","Dúzia","Metade") for t in df_cont["Tipo"]]].copy()
    if sub.empty: return None
    pref = pd.CategoricalDtype(["quebrar_extremo","quebrar_forte","quebrar_médio","neutro"])
    sub["rk"] = sub["Sinal_cont"].astype(pref)
    sub = sub.sort_values(["rk","Rodadas seguidas"], ascending=[True,False])
    r = sub.iloc[0]
    tipo, sinal = r["Tipo"], r["Sinal_cont"]
    expo = 1
    if sinal.startswith("quebrar"):
        razao = f"**Quebrar {tipo}** ({int(r['Rodadas seguidas'])} seguidas; média {r['Média quebra (seq)']})"
    else:
        razao = f"**Seguir {tipo}** ({int(r['Rodadas seguidas'])}≤média {r['Média quebra (seq)']})"
    txt = f"{tipo} — {expo}u (R${expo*UNIDADE_REAIS:.2f})"
    detalhe = f" | Seq: {int(r['Rodadas seguidas'])} • Máx: {r['Máxima sequência']}"
    return txt, (razao + detalhe)

col1, col2 = st.columns(2)
with col1:
    st.markdown("### ✅ Sugestão principal (valor)")
    s1 = sugestao_principal(df_aus)
    if s1: st.markdown(s1[0]); st.caption(s1[1])
    else:  st.info("Nenhum gatilho de ausência acionado agora.")
with col2:
    st.markdown("### ➕ Sugestão complementar (barata)")
    s2 = sugestao_complementar(df_cont)
    if s2: st.markdown(s2[0]); st.caption(s2[1])
    else:  st.info("Nada claro nas apostas baratas agora.")
