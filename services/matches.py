"""
services/matches.py — Service de dados da Copa 2026.

Fonte Nova: football-data.org
Responsabilidades:
  - Fetch com Cache Dinâmico Nível Profissional (30s se bola rolando, 1h se parado)
  - Padrão Adapter: Traduz a API nova para o formato que o frontend já conhece
  - Parse de placares e ganhador de pênaltis
  - Mapeamento país → código ISO (bandeiras via flagcdn.com)
  - Queries: upcoming / all / by_group / by_date
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURAÇÃO DA API FOOTBALL-DATA
# ============================================================================
SOURCE_URL = "https://api.football-data.org/v4/competitions/WC/matches"
API_TOKEN = "COLOQUE_SEU_TOKEN_AQUI"  # Substitua pelo token que você gerou no site!

BRT = timezone(timedelta(hours=-3))

# Variáveis globais para o Cache Dinâmico
_memory_cache: List[dict] = []
_cache_expires_at: float = 0


# ============================================================================
# Mapeamento país → código ISO para flagcdn.com
# ============================================================================
COUNTRY_FLAGS: Dict[str, str] = {
    "Brazil": "br", "Argentina": "ar", "Uruguay": "uy", "Paraguay": "py",
    "Ecuador": "ec", "Colombia": "co", "Venezuela": "ve", "Chile": "cl",
    "Peru": "pe", "Bolivia": "bo",
    "USA": "us", "United States": "us", "Canada": "ca", "Mexico": "mx",
    "Haiti": "ht", "Panama": "pa", "Costa Rica": "cr", "Honduras": "hn", "Jamaica": "jm",
    "France": "fr", "Germany": "de", "Spain": "es", "Portugal": "pt",
    "Italy": "it", "England": "gb-eng", "Netherlands": "nl", "Belgium": "be",
    "Croatia": "hr", "Denmark": "dk", "Switzerland": "ch", "Austria": "at",
    "Poland": "pl", "Norway": "no", "Sweden": "se", "Czech Republic": "cz",
    "Czechia": "cz", "Serbia": "rs", "Scotland": "gb-sct", "Wales": "gb-wls",
    "Turkey": "tr", "Ukraine": "ua", "Ireland": "ie",
    "Morocco": "ma", "Senegal": "sn", "Tunisia": "tn", "Algeria": "dz",
    "Egypt": "eg", "Ivory Coast": "ci", "Côte d'Ivoire": "ci", "Ghana": "gh",
    "Nigeria": "ng", "Cameroon": "cm", "South Africa": "za", "Cape Verde": "cv", "Curaçao": "cw",
    "Japan": "jp", "South Korea": "kr", "Iran": "ir", "Saudi Arabia": "sa",
    "Qatar": "qa", "Australia": "au", "New Zealand": "nz", "Jordan": "jo",
    "Uzbekistan": "uz", "Iraq": "iq",
}

COUNTRY_PT: Dict[str, str] = {
    "Brazil": "Brasil", "Argentina": "Argentina", "Mexico": "México",
    "USA": "Estados Unidos", "United States": "Estados Unidos",
    "South Africa": "África do Sul", "South Korea": "Coreia do Sul",
    "Germany": "Alemanha", "France": "França", "Spain": "Espanha",
    "England": "Inglaterra", "Netherlands": "Holanda", "Belgium": "Bélgica",
    "Switzerland": "Suíça", "Austria": "Áustria", "Croatia": "Croácia",
    "Denmark": "Dinamarca", "Norway": "Noruega", "Sweden": "Suécia",
    "Czech Republic": "República Tcheca", "Czechia": "República Tcheca",
    "Serbia": "Sérvia", "Scotland": "Escócia", "Turkey": "Turquia",
    "Morocco": "Marrocos", "Senegal": "Senegal", "Tunisia": "Tunísia",
    "Algeria": "Argélia", "Egypt": "Egito", "Ivory Coast": "Costa do Marfim",
    "Ghana": "Gana", "Nigeria": "Nigéria", "Cameroon": "Camarões",
    "Cape Verde": "Cabo Verde", "Curaçao": "Curaçao",
    "Japan": "Japão", "Iran": "Irã", "Saudi Arabia": "Arábia Saudita",
    "Qatar": "Catar", "Australia": "Austrália", "New Zealand": "Nova Zelândia",
    "Jordan": "Jordânia", "Portugal": "Portugal", "Italy": "Itália",
    "Canada": "Canadá", "Uruguay": "Uruguai", "Paraguay": "Paraguai",
    "Ecuador": "Equador", "Colombia": "Colômbia", "Haiti": "Haiti",
    "Wales": "País de Gales", "Ireland": "Irlanda",
    "Poland": "Polônia", "Ukraine": "Ucrânia",
    "Iraq": "Iraque", "Uzbekistan": "Uzbequistão",
}


def _pt(name: str) -> str:
    """Nome do país em PT-BR, ou o original se não mapeado."""
    return COUNTRY_PT.get(name, name)

def _flag_url(team_name: str) -> Optional[str]:
    """URL da bandeira no flagcdn.com, ou None pra placeholders."""
    code = COUNTRY_FLAGS.get(team_name)
    if not code:
        return None
    return f"https://flagcdn.com/w80/{code}.png"


# ============================================================================
# LÓGICA DE CACHE DINÂMICO (RATE LIMIT PROTECTOR)
# ============================================================================
def _calculate_dynamic_ttl(matches: list) -> int:
    """
    Inteligência do Cache:
    Se houver algum jogo acontecendo AGORA (em andamento ou pênaltis),
    o TTL cai para 30 segundos (atualizações em tempo real).
    Se não, o TTL vai para 3600 segundos (1 hora) para economizar a cota da API.
    """
    for m in matches:
        if m.get("status") in ["IN_PLAY", "PAUSED", "PENALTY_SHOOTOUT"]:
            logger.info("JOGO ROLANDO! Ativando cache de alta velocidade (30s).")
            return 30 
    
    return 3600 # 1 hora


# ============================================================================
# O ADAPTER: Traduz a API Nova para a Estrutura Antiga
# ============================================================================
def _enrich_match(raw: dict, idx: int) -> dict:
    # 1. Ajuste de fuso horário
    utc_dt = datetime.fromisoformat(raw["utcDate"].replace("Z", "+00:00"))
    dt_brt = utc_dt.astimezone(BRT)
    
    # 2. Extração dos times (Lida com TBD)
    home_team = raw.get("homeTeam", {}).get("name") or "TBD"
    away_team = raw.get("awayTeam", {}).get("name") or "TBD"
    
    # 3. Tratamento de placares e ganhador de pênaltis
    score_obj = raw.get("score", {})
    ft = score_obj.get("fullTime", {}) or {}
    pen = score_obj.get("penalties", {}) or {}
    
    # O fullTime da Football-Data já inclui a prorrogação
    s1 = ft.get("home")
    s2 = ft.get("away")
    
    pen_winner = 0
    pen_home = pen.get("home")
    pen_away = pen.get("away")
    if pen_home is not None and pen_away is not None:
        if pen_home > pen_away:
            pen_winner = 1
        elif pen_away > pen_home:
            pen_winner = 2

    # 4. Tradutor de Fases (Crucial para o phases.py não quebrar)
    stage_map = {
        "GROUP_STAGE": "Matchday 1",
        "LAST_32": "Round of 32",
        "LAST_16": "Round of 16",
        "QUARTER_FINALS": "Quarter-finals",
        "SEMI_FINALS": "Semi-finals",
        "THIRD_PLACE": "Play-off for third place",
        "FINAL": "Final"
    }
    raw_stage = raw.get("stage", "GROUP_STAGE")
    mapped_round = stage_map.get(raw_stage, "Matchday 1")
    
    # Tratamento de string do Grupo (Ex: "GROUP_A" -> "Group A")
    raw_group = raw.get("group")
    group_str = raw_group.replace("_", " ").title() if raw_group else ""

    # Retorna o dicionário exatamente como o Frontend (app.html) espera ler
    return {
        "id": raw.get("id", idx + 1),
        "round": mapped_round,
        "group": group_str,
        "ground": "A definir", # A nova API não envia o estádio facilmente no tier grátis
        "date_raw": dt_brt.strftime("%Y-%m-%d"),
        "time_raw": dt_brt.strftime("%H:%M"),
        "datetime_brt": dt_brt.isoformat(),
        "team1": {
            "name_en": home_team,
            "name_pt": _pt(home_team),
            "flag_url": _flag_url(home_team),
            "placeholder": "TBD" in home_team,
        },
        "team2": {
            "name_en": away_team,
            "name_pt": _pt(away_team),
            "flag_url": _flag_url(away_team),
            "placeholder": "TBD" in away_team,
        },
        "real_s1": s1,
        "real_s2": s2,
        "real_pen_winner": pen_winner,
    }


# ============================================================================
# FETCH & QUERIES
# ============================================================================
def refresh_cache() -> int:
    """Busca dados na API, calcula o TTL e atualiza a memória."""
    global _memory_cache, _cache_expires_at
    
    headers = {"X-Auth-Token": API_TOKEN}
    resp = requests.get(SOURCE_URL, headers=headers)
    resp.raise_for_status()
    
    data = resp.json()
    matches = data.get("matches", [])
    
    # Define quando o cache vai vencer (Inteligência Dinâmica)
    ttl = _calculate_dynamic_ttl(matches)
    _cache_expires_at = time.time() + ttl
    
    enriched = [_enrich_match(m, i) for i, m in enumerate(matches)]
    enriched.sort(key=lambda x: (x["datetime_brt"] or "9999-12-31T23:59:59"))
    
    _memory_cache = enriched
    return len(enriched)


def get_all_matches() -> List[dict]:
    """Entrega o cache. Se estiver vencido, busca na API."""
    global _memory_cache
    if not _memory_cache or time.time() > _cache_expires_at:
        refresh_cache()
    return _memory_cache


def get_upcoming_matches(limit: int = 3) -> List[dict]:
    """Próximos N jogos a partir de agora (BRT)."""
    now_brt = datetime.now(BRT)
    upcoming = []
    for m in get_all_matches():
        if not m["datetime_brt"]:
            continue
        dt = datetime.fromisoformat(m["datetime_brt"])
        if dt > now_brt:
            upcoming.append(m)
        if len(upcoming) >= limit:
            break
    return upcoming


def get_matches_by_group(group: str) -> List[dict]:
    """group: 'A'..'L' ou 'Group A'."""
    needle = group if group.lower().startswith("group") else f"Group {group}"
    return [m for m in get_all_matches() if (m["group"] or "") == needle]


def get_matches_by_date(date_str: str) -> List[dict]:
    """date_str: 'YYYY-MM-DD' em BRT."""
    return [
        m for m in get_all_matches()
        if m["datetime_brt"] and m["datetime_brt"].startswith(date_str)
    ]


def get_cache_info() -> dict:
    """Retorna o status atual do cache na memória para a rota de admin."""
    now = time.time()
    ttl_remaining = max(0, _cache_expires_at - now)
    
    return {
        "cached": len(_memory_cache) > 0,
        "matches_in_cache": len(_memory_cache),
        "expires_in_seconds": int(ttl_remaining),
        "is_expired": now > _cache_expires_at,
        "source": SOURCE_URL
    }