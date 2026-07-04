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
from curses import raw
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
API_TOKEN = "ec7fdc85b0164a999885259b3c72a0c6"  # Substitua pelo token que você gerou no site!

BRT = timezone(timedelta(hours=-3))

# Variáveis globais para o Cache Dinâmico
_memory_cache: List[dict] = []
_cache_expires_at: float = 0


# ============================================================================
# Mapeamento país → código ISO para flagcdn.com
# ============================================================================
COUNTRY_FLAGS: Dict[str, str] = {
    # Américas
    "Brazil": "br", "Argentina": "ar", "Uruguay": "uy", "Paraguay": "py",
    "Ecuador": "ec", "Colombia": "co", "Venezuela": "ve", "Chile": "cl",
    "Peru": "pe", "Bolivia": "bo",
    "USA": "us", "United States": "us", "Canada": "ca", "Mexico": "mx",
    "Haiti": "ht", "Panama": "pa", "Costa Rica": "cr", "Honduras": "hn", "Jamaica": "jm",
    "El Salvador": "sv", "Trinidad and Tobago": "tt",
    
    # Europa
    "France": "fr", "Germany": "de", "Spain": "es", "Portugal": "pt",
    "Italy": "it", "England": "gb-eng", "Netherlands": "nl", "Belgium": "be",
    "Croatia": "hr", "Denmark": "dk", "Switzerland": "ch", "Austria": "at",
    "Poland": "pl", "Norway": "no", "Sweden": "se", "Czech Republic": "cz",
    "Czechia": "cz", "Serbia": "rs", "Scotland": "gb-sct", "Wales": "gb-wls",
    "Turkey": "tr", "Ukraine": "ua", "Ireland": "ie", "Northern Ireland": "gb-nir",
    "Bosnia-Herzegovina": "ba", "Bosnia": "ba", "Slovakia": "sk", "Slovenia": "si",
    "Greece": "gr", "Romania": "ro", "Hungary": "hu", "Iceland": "is", "Finland": "fi",
    
    # África
    "Morocco": "ma", "Senegal": "sn", "Tunisia": "tn", "Algeria": "dz",
    "Egypt": "eg", "Ivory Coast": "ci", "Côte d'Ivoire": "ci", "Ghana": "gh",
    "Nigeria": "ng", "Cameroon": "cm", "South Africa": "za", "Cape Verde Islands": "cv", 
    "Cabo Verde": "cv", "DR Congo": "cd", "Congo DR": "cd", "Congo": "cg", 
    "Mali": "ml", "Zambia": "zm", "Burkina Faso": "bf", "Guinea": "gn",
    
    # Ásia e Oceania
    "Japan": "jp", "South Korea": "kr", "Iran": "ir", "Saudi Arabia": "sa",
    "Qatar": "qa", "Australia": "au", "New Zealand": "nz", "Jordan": "jo",
    "Uzbekistan": "uz", "Iraq": "iq", "United Arab Emirates": "ae", "UAE": "ae",
    "Oman": "om", "Syria": "sy", "Bahrain": "bh", "China PR": "cn", "China": "cn",
    
    # Outros (Placeholder)
    "Curaçao": "cw",
}

COUNTRY_PT: Dict[str, str] = {
    # Américas
    "Brazil": "Brasil", "Argentina": "Argentina", "Mexico": "México",
    "USA": "Estados Unidos", "United States": "Estados Unidos",
    "Canada": "Canadá", "Uruguay": "Uruguai", "Paraguay": "Paraguai",
    "Ecuador": "Equador", "Colombia": "Colômbia", "Haiti": "Haiti",
    "El Salvador": "El Salvador", "Trinidad and Tobago": "Trinidad e Tobago",
    
    # Europa
    "Germany": "Alemanha", "France": "França", "Spain": "Espanha",
    "England": "Inglaterra", "Netherlands": "Holanda", "Belgium": "Bélgica",
    "Switzerland": "Suíça", "Austria": "Áustria", "Croatia": "Croácia",
    "Denmark": "Dinamarca", "Norway": "Noruega", "Sweden": "Suécia",
    "Czech Republic": "República Tcheca", "Czechia": "República Tcheca",
    "Serbia": "Sérvia", "Scotland": "Escócia", "Turkey": "Turquia",
    "Portugal": "Portugal", "Italy": "Itália", "Wales": "País de Gales", 
    "Ireland": "Irlanda", "Northern Ireland": "Irlanda do Norte",
    "Poland": "Polônia", "Ukraine": "Ucrânia", 
    "Bosnia-Herzegovina": "Bósnia e Herzegovina", "Bosnia": "Bósnia",
    "Slovakia": "Eslováquia", "Slovenia": "Eslovênia", "Greece": "Grécia",
    "Romania": "Romênia", "Hungary": "Hungria", "Iceland": "Islândia", "Finland": "Finlândia",
    
    # África
    "South Africa": "África do Sul", "Morocco": "Marrocos", "Senegal": "Senegal", 
    "Tunisia": "Tunísia", "Algeria": "Argélia", "Egypt": "Egito", 
    "Ivory Coast": "Costa do Marfim", "Côte d'Ivoire": "Costa do Marfim",
    "Ghana": "Gana", "Nigeria": "Nigéria", "Cameroon": "Camarões",
    "Cape Verde Islands": "Cabo Verde", "Cabo Verde": "Cabo Verde",
    "DR Congo": "RD Congo", "Congo DR": "RD Congo", "Congo": "Congo",
    "Mali": "Mali", "Zambia": "Zâmbia", "Burkina Faso": "Burkina Faso", "Guinea": "Guiné",
    
    # Ásia e Oceania
    "South Korea": "Coreia do Sul", "Japan": "Japão", "Iran": "Irã", 
    "Saudi Arabia": "Arábia Saudita", "Qatar": "Catar", "Australia": "Austrália", 
    "New Zealand": "Nova Zelândia", "Jordan": "Jordânia",
    "Iraq": "Iraque", "Uzbekistan": "Uzbequistão", "United Arab Emirates": "Emirados Árabes",
    "UAE": "Emirados Árabes", "Oman": "Omã", "Syria": "Síria", "Bahrain": "Bahrein",
    "China PR": "China", "China": "China",
    
    # Outros
    "Curaçao": "Curaçao",
}
MATCH_VENUES: Dict[int, str] = {
    # ---------------- FASE DE GRUPOS (jogos 1–72) ----------------
    # Qui, 11/jun
    1:  "Estádio Azteca (Cidade do México)",
    2:  "Estádio Akron (Guadalajara)",
    # Sex, 12/jun
    3:  "BMO Field (Toronto)",
    4:  "SoFi Stadium (Los Angeles)",
    # Sáb, 13/jun
    5:  "Levi's Stadium (San Francisco)",
    6:  "MetLife Stadium (Nova Jersey)",
    7:  "Gillette Stadium (Boston)",
    8:  "BC Place (Vancouver)",
    # Dom, 14/jun
    9:  "NRG Stadium (Houston)",
    10: "AT&T Stadium (Dallas)",
    11: "Lincoln Financial Field (Filadélfia)",
    12: "Estádio BBVA (Monterrey)",
    # Seg, 15/jun
    13: "Mercedes-Benz Stadium (Atlanta)",
    14: "BC Place (Vancouver)",
    15: "Hard Rock Stadium (Miami)",
    16: "SoFi Stadium (Los Angeles)",
    # Ter, 16/jun
    17: "MetLife Stadium (Nova Jersey)",
    18: "Gillette Stadium (Boston)",
    19: "Arrowhead Stadium (Kansas City)",
    20: "Levi's Stadium (San Francisco)",
    # Qua, 17/jun
    21: "NRG Stadium (Houston)",
    22: "AT&T Stadium (Dallas)",
    23: "BMO Field (Toronto)",
    24: "Estádio Azteca (Cidade do México)",
    # Qui, 18/jun
    25: "Mercedes-Benz Stadium (Atlanta)",
    26: "SoFi Stadium (Los Angeles)",
    27: "BC Place (Vancouver)",
    28: "Estádio Akron (Guadalajara)",
    # Sex, 19/jun
    29: "Lumen Field (Seattle)",
    30: "Gillette Stadium (Boston)",
    31: "Lincoln Financial Field (Filadélfia)",
    32: "Levi's Stadium (San Francisco)",
    # Sáb, 20/jun
    33: "NRG Stadium (Houston)",
    34: "BMO Field (Toronto)",
    35: "Arrowhead Stadium (Kansas City)",
    36: "Estádio BBVA (Monterrey)",
    # Dom, 21/jun
    37: "Mercedes-Benz Stadium (Atlanta)",
    38: "SoFi Stadium (Los Angeles)",
    39: "Hard Rock Stadium (Miami)",
    40: "BC Place (Vancouver)",
    # Seg, 22/jun
    41: "AT&T Stadium (Dallas)",
    42: "Lincoln Financial Field (Filadélfia)",
    43: "MetLife Stadium (Nova Jersey)",
    44: "Levi's Stadium (San Francisco)",
    # Ter, 23/jun
    45: "NRG Stadium (Houston)",
    46: "Gillette Stadium (Boston)",
    47: "BMO Field (Toronto)",
    48: "Estádio Akron (Guadalajara)",
    # Qua, 24/jun
    49: "BC Place (Vancouver)",
    50: "Lumen Field (Seattle)",
    51: "Hard Rock Stadium (Miami)",
    52: "Mercedes-Benz Stadium (Atlanta)",
    53: "Estádio Azteca (Cidade do México)",
    54: "Estádio BBVA (Monterrey)",
    # Qui, 25/jun
    55: "MetLife Stadium (Nova Jersey)",
    56: "Lincoln Financial Field (Filadélfia)",
    57: "AT&T Stadium (Dallas)",
    58: "Arrowhead Stadium (Kansas City)",
    59: "SoFi Stadium (Los Angeles)",
    60: "Levi's Stadium (San Francisco)",
    # Sex, 26/jun
    61: "Gillette Stadium (Boston)",
    62: "BMO Field (Toronto)",
    63: "NRG Stadium (Houston)",
    64: "Estádio Akron (Guadalajara)",
    65: "Lumen Field (Seattle)",
    66: "BC Place (Vancouver)",
    # Sáb, 27/jun
    67: "MetLife Stadium (Nova Jersey)",
    68: "Lincoln Financial Field (Filadélfia)",
    69: "Hard Rock Stadium (Miami)",
    70: "Mercedes-Benz Stadium (Atlanta)",
    71: "Arrowhead Stadium (Kansas City)",
    72: "AT&T Stadium (Dallas)",
 
    # ---------------- ROUND OF 32 (jogos 73–88) ----------------
    # Dom, 28/jun
    73: "SoFi Stadium (Los Angeles)",
    # Seg, 29/jun
    74: "NRG Stadium (Houston)",
    75: "Gillette Stadium (Boston)",
    76: "Estádio BBVA (Monterrey)",
    # Ter, 30/jun
    77: "AT&T Stadium (Dallas)",
    78: "MetLife Stadium (Nova Jersey)",
    79: "Estádio Azteca (Cidade do México)",
    # Qua, 01/jul
    80: "Mercedes-Benz Stadium (Atlanta)",
    81: "Lumen Field (Seattle)",
    82: "Levi's Stadium (San Francisco)",
    # Qui, 02/jul
    83: "SoFi Stadium (Los Angeles)",
    84: "BMO Field (Toronto)",
    85: "BC Place (Vancouver)",
    # Sex, 03/jul
    86: "AT&T Stadium (Dallas)",
    87: "Hard Rock Stadium (Miami)",
    88: "Arrowhead Stadium (Kansas City)",
 
    # ---------------- ROUND OF 16 (jogos 89–96) ----------------
    # Sáb, 04/jul
    89: "NRG Stadium (Houston)",
    90: "Lincoln Financial Field (Filadélfia)",
    # Dom, 05/jul
    91: "MetLife Stadium (Nova Jersey)",
    92: "Estádio Azteca (Cidade do México)",
    # Seg, 06/jul
    93: "AT&T Stadium (Dallas)",
    94: "Lumen Field (Seattle)",
    # Ter, 07/jul
    95: "Mercedes-Benz Stadium (Atlanta)",
    96: "BC Place (Vancouver)",
 
    # ---------------- QUARTAS DE FINAL (jogos 97–100) ----------------
    97:  "Gillette Stadium (Boston)",       # Qui, 09/jul
    98:  "SoFi Stadium (Los Angeles)",      # Sex, 10/jul
    99:  "Hard Rock Stadium (Miami)",       # Sáb, 11/jul
    100: "Arrowhead Stadium (Kansas City)", # Sáb, 11/jul
 
    # ---------------- SEMIFINAIS (jogos 101–102) ----------------
    101: "AT&T Stadium (Dallas)",           # Ter, 14/jul
    102: "Mercedes-Benz Stadium (Atlanta)", # Qua, 15/jul
 
    # ---------------- DISPUTA DE 3º LUGAR (jogo 103) ----------------
    103: "Hard Rock Stadium (Miami)",       # Sáb, 18/jul
 
    # ---------------- FINAL (jogo 104) ----------------
    104: "MetLife Stadium (Nova Jersey)",   # Dom, 19/jul
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

    now_brt = datetime.now(BRT)
    cutoff_time = dt_brt - timedelta(minutes=10)
    is_locked = now_brt >= cutoff_time
    
    # 2. Extração dos times (Lida com TBD)
    home_team = raw.get("homeTeam", {}).get("name") or "TBD"
    away_team = raw.get("awayTeam", {}).get("name") or "TBD"
    
    # 3. Tratamento de placares e ganhador de pênaltis
    score_obj = raw.get("score", {})
    
    pen = score_obj.get("penalties", {}) or {}
    pen_home = pen.get("home")
    pen_away = pen.get("away")
    
    pen_winner = 0
    if pen_home is not None and pen_away is not None:
        if pen_home > pen_away:
            pen_winner = 1
        elif pen_away > pen_home:
            pen_winner = 2

    # Correção: Ignorar o vazamento de pênaltis no fullTime usando regularTime + extraTime
    reg = score_obj.get("regularTime", {}) or {}
    ext = score_obj.get("extraTime", {}) or {}
    ft = score_obj.get("fullTime", {}) or {}

    if reg.get("home") is not None and reg.get("away") is not None:
        s1 = reg.get("home") + (ext.get("home", 0) if ext.get("home") is not None else 0)
        s2 = reg.get("away") + (ext.get("away", 0) if ext.get("away") is not None else 0)
    else:
        s1 = ft.get("home")
        s2 = ft.get("away")

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
        "id": idx + 1,
        "round": mapped_round,
        "group": group_str,
        "ground": MATCH_VENUES.get(idx+1, "A definir"), # A nova API não envia o estádio facilmente no tier grátis
        "date_raw": dt_brt.strftime("%Y-%m-%d"),
        "time_raw": dt_brt.strftime("%H:%M"),
        "datetime_brt": dt_brt.isoformat(),
        "is_locked": is_locked,
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
        "real_pen_s1": pen_home, 
        "real_pen_s2": pen_away,
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
    matches_raw = data.get("matches", [])
    
    # 1. ORDENA CRONOLOGICAMENTE ANTES DE DAR O ID
    # Isso garante que a ordem 1 a 104 vai ser idêntica à antiga API
    matches_raw.sort(key=lambda x: x.get("utcDate", "9999-12-31T23:59:59Z"))
    
    # Define quando o cache vai vencer
    ttl = _calculate_dynamic_ttl(matches_raw)
    _cache_expires_at = time.time() + ttl
    
    # 2. Enriquece os jogos (agora sim, o enumerate vai dar ids 1, 2, 3...)
    enriched = [_enrich_match(m, i) for i, m in enumerate(matches_raw)]
    
    _memory_cache = enriched
    return len(enriched)


def get_all_matches() -> List[dict]:
    """Entrega o cache. Se estiver vencido, busca na API."""
    global _memory_cache
    if not _memory_cache or time.time() > _cache_expires_at:
        refresh_cache()
    now_brt = datetime.now(BRT)
    for m in _memory_cache:
        if m.get("datetime_brt"):
            dt_brt = datetime.fromisoformat(m["datetime_brt"])
            cutoff_time = dt_brt - timedelta(minutes=10)
            # Sobrescreve o status antigo do cache com a realidade do segundo atual
            m["is_locked"] = now_brt >= cutoff_time

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