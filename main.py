"""
main.py — FastAPI app + rotas de páginas + rotas da API de matches.

NOVIDADE nesta Parte 1:
  - `lifespan`: hook que roda código no startup/shutdown do servidor.
    Usamos pra criar as tabelas automaticamente no primeiro boot.

Nas próximas partes, vamos importar routers (auth, bolões, palpites) aqui.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from database import init_db
from routers import admin_router, auth_router, boloes_router, guesses_router, notifications_router
from services import (
    get_all_matches,
    get_cache_info,
    get_matches_by_date,
    get_matches_by_group,
    get_upcoming_matches,
    refresh_cache,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("bolao")

BASE_DIR = Path(__file__).resolve().parent


# ----------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Executado uma vez no startup e uma vez no shutdown.

    Aqui a gente cria as tabelas se ainda não existirem. Isso é idempotente:
    rodar 100 vezes não afeta nada.

    Em produção com migrações (Alembic), você removeria init_db() daqui
    e rodaria `alembic upgrade head` antes de subir o servidor. Pro MVP
    isso é suficiente.
    """
    logger.info("Startup: inicializando banco de dados...")
    init_db()
    logger.info("Banco pronto. Subindo servidor.")
    yield
    logger.info("Shutdown.")


app = FastAPI(
    title="Bolão da Copa 2026 API",
    description="API do bolão da Copa 2026. Fonte de dados: openfootball/worldcup.json.",
    version="0.2.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ============================================================================
# Routers — cada um agrupa um domínio da API (auth, bolões, palpites, admin)
# ============================================================================
app.include_router(auth_router.router)
app.include_router(boloes_router.router)
app.include_router(guesses_router.router)
app.include_router(admin_router.router)
app.include_router(notifications_router.router)


# ============================================================================
# PÁGINAS (HTML)
# ============================================================================
@app.get("/", response_class=HTMLResponse, tags=["pages"])
def page_login(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/app", response_class=HTMLResponse, tags=["pages"])
def page_dashboard(request: Request):
    return templates.TemplateResponse(request, "app.html")


# ============================================================================
# API - Matches (já existente, sem mudanças nesta parte)
# ============================================================================
@app.get("/api/matches/upcoming", tags=["matches"])
def api_upcoming(limit: int = Query(3, ge=1, le=50)):
    try:
        return {"count": limit, "matches": get_upcoming_matches(limit)}
    except Exception as e:
        logger.exception("Erro em /api/matches/upcoming")
        raise HTTPException(status_code=502, detail=f"Fonte indisponível: {e}")


@app.get("/api/matches/all", tags=["matches"])
def api_all():
    try:
        matches = get_all_matches()
        return {"count": len(matches), "matches": matches}
    except Exception as e:
        logger.exception("Erro em /api/matches/all")
        raise HTTPException(status_code=502, detail=f"Fonte indisponível: {e}")


@app.get("/api/matches/group/{group_id}", tags=["matches"])
def api_by_group(group_id: str):
    try:
        matches = get_matches_by_group(group_id)
        if not matches:
            raise HTTPException(status_code=404, detail=f"Grupo '{group_id}' não encontrado.")
        return {"group": group_id.upper(), "count": len(matches), "matches": matches}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro em /api/matches/group")
        raise HTTPException(status_code=502, detail=f"Fonte indisponível: {e}")


@app.get("/api/matches/date/{date_str}", tags=["matches"])
def api_by_date(date_str: str):
    try:
        matches = get_matches_by_date(date_str)
        return {"date": date_str, "count": len(matches), "matches": matches}
    except Exception as e:
        logger.exception("Erro em /api/matches/date")
        raise HTTPException(status_code=502, detail=f"Fonte indisponível: {e}")


# ============================================================================
# API - Cache
# ============================================================================
@app.post("/api/cache/refresh", tags=["cache"])
def api_refresh():
    try:
        total = refresh_cache()
        return {"status": "ok", "total_matches": total}
    except Exception as e:
        logger.exception("Erro em /api/cache/refresh")
        raise HTTPException(status_code=502, detail=f"Falha no refresh: {e}")


@app.get("/api/cache/info", tags=["cache"])
def api_cache_info():
    return get_cache_info()


# ============================================================================
# Health
# ============================================================================
@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}
