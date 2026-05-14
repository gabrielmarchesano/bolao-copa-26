"""
services/matches.py — Service de dados da Copa 2026.

Fonte: openfootball/worldcup.json (domínio público, sem API key).
URL:   https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json

Responsabilidades:
  - Fetch com cache TTL (1h)
  - Parse de datas/horários para BRT
  - Parse de placares (tempo normal, prorrogação, pênaltis)
  - Mapeamento país → código ISO (bandeiras via flagcdn.com)
  - Queries: upcoming / all / by_group / by_date

NOTA DE COMPATIBILIDADE:
  Sem `from __future__ import annotations` — mesma razão de models.py
  (conflito com SQLAlchemy 2.x + Python 3.13 em outros módulos; mantemos o
  padrão por consistência com o resto do projeto).
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SOURCE_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"
BRT = timezone(timedelta(hours=-3))
CACHE_TTL_SECONDS = 3600  # 1 hora


# ============================================================================
# Mapeamento país → código ISO para flagcdn.com
# ============================================================================
# URL base: https://flagcdn.com/w80/{iso}.png  (ex: /w80/br.png)
COUNTRY_FLAGS: Dict[str, str] = {
    # América do Sul
    "Brazil": "br", "Argentina": "ar", "Uruguay": "uy", "Paraguay": "py",
    "Ecuador": "ec", "Colombia": "co", "Venezuela": "ve", "Chile": "cl",
    "Peru": "pe", "Bolivia": "bo",
    # América do Norte / Central
    "USA": "us", "United States": "us", "Canada": "ca", "Mexico": "mx",
    "Haiti": "ht", "Panama": "pa", "Costa Rica": "cr", "Honduras": "hn",
    "Jamaica": "jm",
    # Europa
    "France": "fr", "Germany": "de", "Spain": "es", "Portugal": "pt",
    "Italy": "it", "England": "gb-eng", "Netherlands": "nl", "Belgium": "be",
    "Croatia": "hr", "Denmark": "dk", "Switzerland": "ch", "Austria": "at",
    "Poland": "pl", "Norway": "no", "Sweden": "se", "Czech Republic": "cz",
    "Czechia": "cz", "Serbia": "rs", "Scotland": "gb-sct", "Wales": "gb-wls",
    "Turkey": "tr", "Ukraine": "ua", "Ireland": "ie",
    # África
    "Morocco": "ma", "Senegal": "sn", "Tunisia": "tn", "Algeria": "dz",
    "Egypt": "eg", "Ivory Coast": "ci", "Côte d'Ivoire": "ci", "Ghana": "gh",
    "Nigeria": "ng", "Cameroon": "cm", "South Africa": "za", "Cape Verde": "cv",
    "Curaçao": "cw",
    # Ásia / Oceania
    "Japan": "jp", "South Korea": "kr", "Iran": "ir", "Saudi Arabia": "sa",
    "Qatar": "qa", "Australia": "au", "New Zealand": "nz", "Jordan": "jo",
    "Uzbekistan": "uz", "Iraq": "iq",
}


# ============================================================================
# Tradução EN → PT
# ============================================================================
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

_cache: dict = {"data": None, "fetched_at": None}


# ============================================================================
# Utils
# ============================================================================
def _parse_match_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Converte 'YYYY-MM-DD' + 'HH:MM UTC-X' → datetime em BRT (UTC-3).
    Retorna None se falhar o parse.
    """
    m = re.match(r"(\d{1,2}):(\d{2})\s+UTC([+-]\d+)", time_str.strip())
    if not m:
        return None
    hour, minute, offset = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        y, mo, d = map(int, date_str.split("-"))
        tz = timezone(timedelta(hours=offset))
        dt_local = datetime(y, mo, d, hour, minute, tzinfo=tz)
        return dt_local.astimezone(BRT)
    except Exception as e:
        logger.warning("Falha ao parsear data %s %s: %s", date_str, time_str, e)
        return None


def _parse_match_score(raw: dict) -> dict:
    """
    Extrai placar e vencedor de pênaltis do formato openfootball.

    Formato esperado (quando o jogo já terminou):
        "score": {
            "ft": [2, 1],           # tempo normal (Full Time)
            "et": [2, 2],           # prorrogação (Extra Time, opcional)
            "p":  [4, 5]            # pênaltis (opcional)
        }

    A CONVENÇÃO DO SCORE:
        - 'ft' = placar ao fim dos 90' (se teve prorrogação, continua sendo 90')
        - 'et' = placar no fim da prorrogação (inclui os gols dos 90')
        - 'p'  = placar só dos pênaltis (ex: 4×5 = time 2 venceu nos pen)

    DECISÃO DE PRODUTO:
        Usamos `et` se existir, senão `ft`, como o "placar do jogo" — porque
        palpite é de placar até o fim da prorrogação. Pênaltis são tratados
        separadamente como pen_winner.

    Retorna:
        {"s1": int | None, "s2": int | None, "pen_winner": 0 | 1 | 2}
        pen_winner: 0 = sem pênaltis, 1 = time 1 venceu, 2 = time 2 venceu

    Validações defensivas (jogo ainda não rolou, JSON malformado, etc):
        retorna {"s1": None, "s2": None, "pen_winner": 0}
    """
    score_data = raw.get("score")
    if not score_data:
        return {"s1": None, "s2": None, "pen_winner": 0}

    # --- Placar base: prorrogação (et) se existir, senão tempo normal (ft) ---
    # `or []` protege contra None/lista vazia; len < 2 protege contra dados
    # incompletos (que o openfootball às vezes tem em fixtures futuras).
    et = score_data.get("et") or []
    ft = score_data.get("ft") or []

    if len(et) >= 2:
        s1, s2 = et[0], et[1]
    elif len(ft) >= 2:
        s1, s2 = ft[0], ft[1]
    else:
        # Sem placar válido → jogo não aconteceu ainda ou dado incompleto
        return {"s1": None, "s2": None, "pen_winner": 0}

    # --- Pênaltis ---
    pen_winner = 0
    p = score_data.get("p") or []
    if len(p) >= 2:
        if p[0] > p[1]:
            pen_winner = 1
        elif p[1] > p[0]:
            pen_winner = 2
        # se p[0] == p[1] (improvável mas defensivo): 0

    return {"s1": s1, "s2": s2, "pen_winner": pen_winner}


def _pt(name: str) -> str:
    """Nome do país em PT-BR, ou o original se não mapeado."""
    return COUNTRY_PT.get(name, name)


def _flag_url(team_name: str) -> Optional[str]:
    """URL da bandeira no flagcdn.com, ou None pra placeholders (UEFA Path etc)."""
    code = COUNTRY_FLAGS.get(team_name)
    if not code:
        return None
    return f"https://flagcdn.com/w80/{code}.png"


def _is_placeholder(team_name: str) -> bool:
    """True para times ainda não definidos (ex: 'UEFA Path D winner')."""
    return "winner" in team_name.lower() or "path" in team_name.lower()


def _enrich_match(raw: dict, idx: int) -> dict:
    """
    Transforma o match cru do openfootball num dict pronto pra API/UI.

    Acrescenta campos:
      - id, datetime_brt (ISO com tz)
      - team1/team2 com name_pt, flag_url, placeholder
      - real_s1, real_s2, real_pen_winner (quando o jogo já aconteceu)
    """
    dt_brt = _parse_match_datetime(raw["date"], raw["time"])
    t1, t2 = raw["team1"], raw["team2"]
    score_info = _parse_match_score(raw)

    return {
        "id": idx + 1,
        "round": raw["round"],
        "group": raw.get("group"),
        "ground": raw["ground"],
        "date_raw": raw["date"],
        "time_raw": raw["time"],
        "datetime_brt": dt_brt.isoformat() if dt_brt else None,
        "team1": {
            "name_en": t1,
            "name_pt": _pt(t1),
            "flag_url": _flag_url(t1),
            "placeholder": _is_placeholder(t1),
        },
        "team2": {
            "name_en": t2,
            "name_pt": _pt(t2),
            "flag_url": _flag_url(t2),
            "placeholder": _is_placeholder(t2),
        },
        # Placar real (None se jogo ainda não aconteceu)
        "real_s1": score_info["s1"],
        "real_s2": score_info["s2"],
        "real_pen_winner": score_info["pen_winner"],
    }


# ============================================================================
# Fetch + Cache
# ============================================================================
def _fetch_raw(force: bool = False) -> dict:
    """Baixa o JSON do openfootball, com cache TTL de 1h."""
    global _cache
    now = datetime.now(timezone.utc)
    if not force and _cache["data"] and _cache["fetched_at"]:
        age = (now - _cache["fetched_at"]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return _cache["data"]

    logger.info("Fetching openfootball fixtures...")
    resp = requests.get(SOURCE_URL, timeout=10)
    resp.raise_for_status()
    _cache["data"] = resp.json()
    _cache["fetched_at"] = now
    return _cache["data"]


# ============================================================================
# Public API
# ============================================================================
def get_all_matches() -> List[dict]:
    """Todos os jogos enriquecidos, ordenados por data."""
    raw = _fetch_raw()
    matches = [_enrich_match(m, i) for i, m in enumerate(raw.get("matches", []))]
    matches.sort(key=lambda x: (x["datetime_brt"] or "9999-12-31T23:59:59"))
    return matches


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


def refresh_cache() -> int:
    """Força refresh. Retorna total de partidas carregadas."""
    raw = _fetch_raw(force=True)
    return len(raw.get("matches", []))


def get_cache_info() -> dict:
    return {
        "cached": _cache["data"] is not None,
        "fetched_at": _cache["fetched_at"].isoformat() if _cache["fetched_at"] else None,
        "ttl_seconds": CACHE_TTL_SECONDS,
        "source": SOURCE_URL,
    }
