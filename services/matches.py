"""
services/matches.py — Service de dados com API Football-Data e Cache Dinâmico.
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# --- CONFIGURAÇÃO DA NOVA API ---
SOURCE_URL = "https://api.football-data.org/v4/competitions/WC/matches"
API_TOKEN = "COLOQUE_SEU_TOKEN_AQUI" # Cadastre-se lá e cole seu token!

BRT = timezone(timedelta(hours=-3))

# Variáveis globais para o Cache Dinâmico
_memory_cache: List[dict] = []
_cache_expires_at: float = 0


def _calculate_dynamic_ttl(matches: list) -> int:
    """
    Inteligência do Cache:
    Varre todos os jogos. Se houver algum acontecendo AGORA, o TTL cai para 30 segundos.
    Caso contrário, relaxa e atualiza a cada 1 hora.
    """
    for m in matches:
        # Status oficiais da Football-Data para bola rolando
        if m.get("status") in ["IN_PLAY", "PAUSED", "PENALTY_SHOOTOUT"]:
            logger.info("JOGO ROLANDO! Ativando cache de alta velocidade (30s).")
            return 30 
    
    return 3600 # 1 hora


def _enrich_match(raw: dict, idx: int) -> dict:
    """
    O 'Adapter': Pega o formato da Football-Data e traduz para o formato
    que o nosso app.html e o phases.py já estão acostumados a ler.
    """
    # 1. Ajuste de fuso horário (Vem em UTC puro, passamos pra BRT)
    utc_dt = datetime.fromisoformat(raw["utcDate"].replace("Z", "+00:00"))
    dt_brt = utc_dt.astimezone(BRT)
    
    # 2. Tratamento de times (se for TBD, a API pode mandar None)
    home = raw.get("homeTeam", {}).get("name") if raw.get("homeTeam") else "TBD"
    away = raw.get("awayTeam", {}).get("name") if raw.get("awayTeam") else "TBD"
    
    # 3. Tratamento de placares
    score_obj = raw.get("score", {})
    ft = score_obj.get("fullTime", {}) or {}
    pen = score_obj.get("penalties", {}) or {}
    
    # 4. Tradutor de Fases (Engana o phases.py para ele não quebrar)
    stage_map = {
        "GROUP_STAGE": "Matchday 1", # Tudo de grupo cai aqui
        "LAST_32": "Round of 32",
        "LAST_16": "Round of 16",
        "QUARTER_FINALS": "Quarter-finals",
        "SEMI_FINALS": "Semi-finals",
        "THIRD_PLACE": "Play-off for third place",
        "FINAL": "Final"
    }
    raw_stage = raw.get("stage", "GROUP_STAGE")
    mapped_round = stage_map.get(raw_stage, "Matchday 1")

    # Retorna o dicionário com as mesmíssimas chaves de antes!
    return {
        "id": raw.get("id", idx + 1),
        "num": idx + 1,
        "date": dt_brt.strftime("%Y-%m-%d"),
        "time": dt_brt.strftime("%H:%M"),
        "datetime_brt": dt_brt.isoformat(),
        "team1": home,
        "team2": away,
        "score1": ft.get("home"),
        "score2": ft.get("away"),
        "score1et": None, # Football-data embute a prorrogação no fullTime
        "score2et": None,
        "score1p": pen.get("home"),
        "score2p": pen.get("away"),
        "round": mapped_round,
        "group": raw.get("group", ""),
        "status": raw.get("status") # SCHEDULED, TIMED, IN_PLAY, FINISHED
    }


def refresh_cache() -> int:
    """Busca dados na API, calcula o TTL e atualiza a memória."""
    global _memory_cache, _cache_expires_at
    
    headers = {"X-Auth-Token": API_TOKEN}
    resp = requests.get(SOURCE_URL, headers=headers)
    resp.raise_for_status()
    
    data = resp.json()
    matches = data.get("matches", [])
    
    # Define quando o cache vai vencer
    ttl = _calculate_dynamic_ttl(matches)
    _cache_expires_at = time.time() + ttl
    
    enriched = [_enrich_match(m, i) for i, m in enumerate(matches)]
    enriched.sort(key=lambda x: x["datetime_brt"])
    
    _memory_cache = enriched
    return len(enriched)


def get_all_matches() -> List[dict]:
    """Entrega o cache. Se estiver vencido, busca na API."""
    global _memory_cache
    if not _memory_cache or time.time() > _cache_expires_at:
        refresh_cache()
    return _memory_cache


def get_upcoming_matches(limit: int = 3) -> List[dict]:
    now_brt = datetime.now(BRT)
    upcoming = []
    for m in get_all_matches():
        dt = datetime.fromisoformat(m["datetime_brt"])
        if dt > now_brt:
            upcoming.append(m)
        if len(upcoming) >= limit:
            break
    return upcoming


def get_matches_by_group(group: str) -> List[dict]:
    needle = group.upper() if group.lower().startswith("group") else f"GROUP {group.upper()}"
    return [m for m in get_all_matches() if (m["group"] or "").upper() == needle]


def get_matches_by_date(date_str: str) -> List[dict]:
    return [
        m for m in get_all_matches()
        if m["datetime_brt"].startswith(date_str)
    ]