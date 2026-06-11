"""
services/phases.py — agrupamento e lock de palpites por fase.

CONCEITO: extrair a lógica "que fase é este jogo?" e "quando esta fase trava?"
pra um módulo isolado. Router só consome, nada de regras de negócio misturadas.

FASES canônicas:
  - "groups"  → fase de grupos (qualquer "Matchday N")
  - "r32"     → Round of 32
  - "r16"     → Round of 16
  - "qf"      → Quarter-finals
  - "sf"      → Semi-finals
  - "third"   → Play-off for third place
  - "final"   → Final

REGRA DE LOCK:
  Palpites de uma fase fecham quando o primeiro jogo daquela fase começa.
  Ex: Grupos fecham no kickoff de "Mexico x South Africa" (11/jun/26, 13:00).

ABERTURA PROGRESSIVA:
  - "groups": sempre palpitáveis enquanto não travados
  - fases posteriores: só abrem quando predecessor tem TODOS os jogos com resultado
  - admin pode força abrir/fechar manualmente via `/admin/phases/{phase_key}/force-open`
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from services.matches import get_all_matches
from services.phase_control import is_phase_force_open, is_phase_force_closed

BRT = timezone(timedelta(hours=-3))


# ============================================================================
# Mapeamento round → fase canônica
# ============================================================================
_ROUND_TO_PHASE: Dict[str, str] = {
    "Round of 32": "r32",
    "Round of 16": "r16",
    "Quarter-finals": "qf",
    "Semi-finals": "sf",
    "Play-off for third place": "third",
    "Final": "final",
}

PHASE_LABELS: Dict[str, str] = {
    "groups": "Fase de Grupos",
    "r32": "16 avos de Final",
    "r16": "Oitavas de Final",
    "qf": "Quartas de Final",
    "sf": "Semifinais",
    "third": "Disputa do 3º Lugar",
    "final": "Final",
}

PHASE_ORDER: List[str] = ["groups", "r32", "r16", "qf", "sf", "third", "final"]

# Fases de mata-mata (usado no frontend pra decidir se mostra pen_winner)
KNOCKOUT_PHASES = {"r32", "r16", "qf", "sf", "third", "final"}


def get_phase_key(match_round: Optional[str]) -> str:
    """
    Mapeia o `round` do openfootball pra chave canônica de fase.

    >>> get_phase_key("Matchday 1")
    'groups'
    >>> get_phase_key("Matchday 17")
    'groups'
    >>> get_phase_key("Final")
    'final'
    >>> get_phase_key(None)
    'groups'
    """
    if not match_round or match_round.startswith("Matchday"):
        return "groups"
    return _ROUND_TO_PHASE.get(match_round, "groups")


def is_knockout(match_round: Optional[str]) -> bool:
    """True se o jogo é de mata-mata (relevante pra pen_winner)."""
    return get_phase_key(match_round) in KNOCKOUT_PHASES


# ============================================================================
# Cálculo de locks
# ============================================================================
def compute_phase_locks(now_brt: Optional[datetime] = None) -> Dict[str, dict]:
    """
    Calcula o timestamp de lock e o status de cada fase.

    Args:
        now_brt: datetime em BRT pra comparar. Default: agora.

    Returns:
        {
          "groups": {"lock_at": datetime|None, "is_locked": bool, "matches_count": int},
          "r32":    {...},
          ...
        }
    """
    if now_brt is None:
        now_brt = datetime.now(BRT)

    matches = get_all_matches()

    # Agrupa por fase e acha o kickoff mais cedo de cada uma
    by_phase: Dict[str, List[dict]] = {p: [] for p in PHASE_ORDER}
    for m in matches:
        phase = get_phase_key(m.get("round"))
        by_phase[phase].append(m)

    result: Dict[str, dict] = {}
    for phase, items in by_phase.items():
        dated = [m for m in items if m["datetime_brt"]]
        if not dated:
            result[phase] = {"lock_at": None, "is_locked": False, "matches_count": len(items)}
            continue
        first_dt = min(datetime.fromisoformat(m["datetime_brt"]) for m in dated)
        result[phase] = {
            "lock_at": first_dt,
            "is_locked": now_brt >= first_dt,
            "matches_count": len(items),
        }
    return result


def has_tournament_started() -> bool:
    """
    Verifica se a competição já começou.
    A Copa é considerada "iniciada" no exato momento em que os palpites 
    para o Jogo de Abertura (Jogo 1) são trancados.
    """
    matches = get_all_matches()
    
    # Prevenção de erro caso a API falhe e retorne vazio
    if not matches:
        return False
        
    # Como o matches.py já ordena cronologicamente, o matches[0] é sempre o Jogo de Abertura
    jogo_abertura = matches[0]
    dt_str = jogo_abertura.get("datetime_brt")
    if not dt_str:
        return False
    # Retorna o status do cadeado do Jogo 1 (que já tem a regra de 1h embutida)
    match_time = datetime.fromisoformat(dt_str)
    
    # Pega o relógio exato de agora, respeitando o mesmo fuso horário da partida
    now = datetime.now(match_time.tzinfo)
    
    # Retorna True apenas se o relógio de agora já passou do apito inicial
    return now >= match_time


def is_phase_locked(phase_key: str, now_brt: Optional[datetime] = None) -> bool:
    """Retorna True se a fase já está bloqueada (kickoff do 1º jogo passou)."""
    locks = compute_phase_locks(now_brt)
    return locks.get(phase_key, {}).get("is_locked", False)


def is_match_phase_locked(match_round: Optional[str], now_brt: Optional[datetime] = None) -> bool:
    """Retorna True se o palpite pra este jogo está bloqueado."""
    return is_phase_locked(get_phase_key(match_round), now_brt)


def is_phase_guessable(phase_key: str, now_brt: Optional[datetime] = None) -> bool:
    """
    Retorna True se a fase pode receber palpites NOVOS.
    
    Lógica:
      1. Se forçadamente fechada pelo admin → False
      2. Se forçadamente aberta pelo admin → True (exceto se já começou)
      3. Se já começou (kickoff passou) → False (mas permite visualizar)
      4. Grupos: sempre True (enquanto não começou)
      5. Fases posteriores: True se predecessor terminou (todos têm resultado)
    """
    if now_brt is None:
        now_brt = datetime.now(BRT)
    
    # 1. Admin força fechamento
    if is_phase_force_closed(phase_key):
        return False
    
    locks = compute_phase_locks(now_brt)
    lock_info = locks.get(phase_key, {})
    lock_at = lock_info.get("lock_at")
    is_locked = lock_info.get("is_locked", False)
    
    # Se sem data = fase não existe ainda
    if not lock_at:
        return False
    
    # 3. Se já começou = não palpita novo
    if is_locked:
        return False
    
    # 2. Admin força abertura
    if is_phase_force_open(phase_key):
        return True
    
    # 4. GRUPOS: sempre liberados enquanto não travados
    if phase_key == "groups":
        return True
    
    # 5. FASES POSTERIORES: só palpitam se predecessor terminou
    phase_idx = PHASE_ORDER.index(phase_key)
    predecessor_key = PHASE_ORDER[phase_idx - 1]
    
    # Verifica se predecessor tem TODOS os jogos com resultado
    matches = get_all_matches()
    pred_matches = [m for m in matches if get_phase_key(m.get("round")) == predecessor_key]
    
    # Todos os jogos do predecessor precisam ter resultado
    all_have_result = all(m.get("real_s1") is not None and m.get("real_s2") is not None for m in pred_matches)
    
    return all_have_result




# ============================================================================
# Agrupamento de jogos pro frontend de palpites
# ============================================================================
def get_matches_grouped_for_guessing() -> Dict[str, dict]:
    """
    Retorna jogos palpitáveis organizados pro UI de palpite.
    
    Filtra: Só retorna fases que são palpitáveis (is_phase_guessable).
    Rationale: Não adianta mostrar palpites pra oitavas se os grupos ainda estão em andamento.

    Grupos: agrupados por 'Group A' .. 'Group L', jogos em ordem cronológica.
    Mata-mata: agrupado por fase, jogos em ordem cronológica.

    Estrutura:
        {
          "groups": {
            "label": "Fase de Grupos",
            "phase_key": "groups",
            "lock_at": "2026-06-11T16:00:00-03:00",
            "is_locked": false,
            "can_guess": true,
            "groups": {
              "Group A": [match, match, ...],
              "Group B": [...],
              ...
            }
          },
          "r32": {
            "label": "Oitavas de 32",
            "phase_key": "r32",
            "lock_at": "...",
            "is_locked": false,
            "can_guess": false,
            "matches": [match, match, ...]
          },
          ...
        }
    """
    matches = get_all_matches()
    locks = compute_phase_locks()

    out: Dict[str, dict] = {}
    for phase_key in PHASE_ORDER:
        lock_info = locks[phase_key]
        
        # Verifica se a fase é palpitável
        can_guess = is_phase_guessable(phase_key)
        
        base = {
            "label": PHASE_LABELS[phase_key],
            "phase_key": phase_key,
            "lock_at": lock_info["lock_at"].isoformat() if lock_info["lock_at"] else None,
            "is_locked": lock_info["is_locked"],
            "can_guess": can_guess,
        }
        phase_matches = [m for m in matches if get_phase_key(m.get("round")) == phase_key]

        if phase_key == "groups":
            # Agrupa por "Group A", "Group B"... já ordenado por datetime_brt
            groups: Dict[str, List[dict]] = {}
            for m in phase_matches:
                g = m.get("group") or "Sem grupo"
                groups.setdefault(g, []).append(m)
            # Ordena as chaves (Group A, Group B, ...)
            base["groups"] = {k: groups[k] for k in sorted(groups.keys())}
        else:
            # Mata-mata: lista plana em ordem cronológica (já vem ordenado)
            base["matches"] = phase_matches

        out[phase_key] = base
    return out
