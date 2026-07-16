"""
routers/guesses_router.py — endpoints de palpites.

ROTAS:
  GET  /boloes/{id}/guesses              → meus palpites nesse bolão
  POST /boloes/{id}/guesses              → cria ou atualiza palpite (upsert)
  GET  /boloes/{id}/guessable            → todos os jogos agrupados + meus palpites
                                           (endpoint único pra tela de palpite)

REGRA DE LOCK (NOVA):
  Palpites fecham por FASE INTEIRA, no kickoff do 1º jogo da fase.
  Ex: palpites da fase de grupos fecham quando o 1º jogo da fase começa.
  Não é mais 15min antes de cada jogo individual (versão anterior).
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from auth import get_current_user, get_membership_or_403
from database import get_db
from models import Guess, User, MatchResult, Membership, ExtraGuess, ChampionshipResult
from schemas import GuessCreate, GuessRead
from scoring import tournament_points_breakdown
from services import (
    get_all_matches,
    get_matches_grouped_for_guessing,
    is_knockout,
    is_match_phase_locked,
    is_phase_guessable,
    get_phase_key
)

router = APIRouter(prefix="/boloes/{bolao_id}/guesses", tags=["guesses"])




# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _find_match(match_id: int) -> dict:
    """Busca match pelo id; 404 se não achar."""
    all_matches = get_all_matches()
    match = next((m for m in all_matches if m["id"] == match_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Jogo {match_id} não encontrado.")
    return match

# Quanto tempo antes do kickoff o palpite fecha.
# (É AQUI que se muda para 5 minutos quando você for fazer aquele item pendente.)
LOCK_OFFSET = timedelta(minutes=10)


def _match_kickoff(match: dict):
    """datetime (com tz) do início do jogo, ou None se não houver data."""
    dt_str = match.get("datetime_brt")
    if not dt_str:
        return None
    return datetime.fromisoformat(dt_str)


def is_match_locked_for_editing(match: dict) -> bool:
    """
    True quando o palpite deste jogo NÃO pode mais ser alterado:
    já passou de (kickoff - LOCK_OFFSET) — ou seja, o jogo está prestes a
    começar, em andamento, ou já terminou.

    É a MESMA regra usada para rejeitar um POST de palpite, por isso é segura
    para liberar a exibição dos palpites alheios (itens 2 e 3).
    """
    kickoff = _match_kickoff(match)
    if kickoff is None:
        return False
    now = datetime.now(kickoff.tzinfo)
    return now >= (kickoff - LOCK_OFFSET)


def _official_results_map(db: Session) -> dict:
    official = {}
    for m in get_all_matches():
        if m.get("real_s1") is not None and m.get("real_s2") is not None:
            official[m["id"]] = {
                "score1": m["real_s1"], "score2": m["real_s2"],
                "pen_s1": m.get("real_pen_s1"), "pen_s2": m.get("real_pen_s2"),
                "pen_winner": m.get("real_pen_winner", 0),
            }
    for r in db.exec(select(MatchResult)).all():
        base = official.get(r.match_id, {})
        official[r.match_id] = {
            "score1": r.score1, "score2": r.score2,
            "pen_s1": base.get("pen_s1"), "pen_s2": base.get("pen_s2"),
            "pen_winner": r.pen_winner,
        }
    return official


def build_extra_block(db: Session, membership_id: int) -> dict:
    """
    Monta o bloco de palpites bônus de um membro, com resultado oficial,
    o palpite dele e os pontos por campo.
 
    Retorno (contrato usado pelo frontend em ambos os pontos — box do usuário
    e topo da modal de ranking):
      {
        "has_official": bool,          # já existe gabarito oficial?
        "fields": {
          "campeao":        {"official": str|None, "guess": str|None, "points": int, "hit": bool},
          "artilheiro":     {...},
          "melhor_jogador": {...},
        },
        "total_points": int,           # soma dos pontos bônus
      }
    """
    extra = db.exec(
        select(ExtraGuess).where(ExtraGuess.membership_id == membership_id)
    ).first()
    oficial = db.get(ChampionshipResult, 1)
 
    g_campeao = extra.campeao if extra else None
    g_artilheiro = extra.artilheiro if extra else None
    g_melhor = extra.melhor_jogador if extra else None
 
    o_campeao = oficial.campeao if oficial else None
    o_artilheiro = oficial.artilheiro if oficial else None
    o_melhor = oficial.melhor_jogador if oficial else None
 
    breakdown = tournament_points_breakdown(
        guess_champion=g_campeao,
        guess_scorer=g_artilheiro,
        guess_best_player=g_melhor,
        real_champion=o_campeao,
        real_scorer=o_artilheiro,
        real_best_player=o_melhor,
    )
 
    has_official = any([o_campeao, o_artilheiro, o_melhor])
 
    fields = {
        "campeao": {
            "official": o_campeao, "guess": g_campeao,
            "points": breakdown["campeao"]["points"], "hit": breakdown["campeao"]["hit"],
        },
        "artilheiro": {
            "official": o_artilheiro, "guess": g_artilheiro,
            "points": breakdown["artilheiro"]["points"], "hit": breakdown["artilheiro"]["hit"],
        },
        "melhor_jogador": {
            "official": o_melhor, "guess": g_melhor,
            "points": breakdown["melhor_jogador"]["points"], "hit": breakdown["melhor_jogador"]["hit"],
        },
    }
 
    return {
        "has_official": has_official,
        "fields": fields,
        "total_points": sum(f["points"] for f in fields.values()),
    }
 
def _match_public(match: dict) -> dict:
    """Campos do jogo que o front precisa pra renderizar um card."""
    return {
        "match_id": match["id"],
        "team1": match.get("team1"),
        "team2": match.get("team2"),
        "datetime_brt": match.get("datetime_brt"),
        "group": match.get("group"),
        "round": match.get("round"),
    }

def _validate_guess_payload(payload: GuessCreate, match: dict) -> None:
    """Validações de regra de negócio. Raise HTTPException em caso de erro."""
    if payload.score1 < 0 or payload.score2 < 0:
        raise HTTPException(400, "Placares devem ser não-negativos.")

    # pen_winner só faz sentido em mata-mata + empate
    if payload.pen_winner != 0:
        if not is_knockout(match.get("round")):
            raise HTTPException(400, "Palpite de pênalti só é válido em jogos de mata-mata.")
        if payload.score1 != payload.score2:
            raise HTTPException(400, "Palpite de pênalti só é válido quando o palpite é de empate.")
        if payload.pen_winner not in (1, 2):
            raise HTTPException(400, "pen_winner deve ser 0, 1 ou 2.")

    # LOCK: usa a regra centralizada (mesma da liberação de palpites)
    if _match_kickoff(match) is None:
        raise HTTPException(400, "Jogo sem data definida — palpite indisponível.")
    if is_match_locked_for_editing(match):
        raise HTTPException(400, "Palpites para este jogo estão encerrados.")
      
# ----------------------------------------------------------------------------
# POST — criar ou atualizar palpite (upsert)
# ----------------------------------------------------------------------------
@router.post("", response_model=GuessRead, status_code=201)
def create_or_update_guess(
    bolao_id: int,
    payload: GuessCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upsert de palpite. Regras:
      - User precisa ser membro do bolão
      - Jogo precisa existir
      - Fase precisa estar aberta
      - pen_winner só em mata-mata + empate
    """
    membership = get_membership_or_403(bolao_id, current_user, db)
    match = _find_match(payload.match_id)

    # 🟢 A TRAVA VEM AQUI: Zera o pênalti fantasma direto no payload
    if payload.score1 != payload.score2:
        payload.pen_winner = 0

    # 🟢 AGORA SIM, VALIDA AS REGRAS (não vai mais estourar o Erro 400)
    _validate_guess_payload(payload, match)

    existing = db.exec(
        select(Guess).where(
            Guess.membership_id == membership.id,
            Guess.match_id == payload.match_id,
        )
    ).first()

    if existing:
        existing.score1 = payload.score1
        existing.score2 = payload.score2
        existing.pen_winner = payload.pen_winner # Atualizado para usar o payload direto
        existing.updated_at = datetime.utcnow()
        guess = existing
    else:
        guess = Guess(
            membership_id=membership.id,
            match_id=payload.match_id,
            score1=payload.score1,
            score2=payload.score2,
            pen_winner=payload.pen_winner, # Atualizado para usar o payload direto
        )
        db.add(guess)

    db.commit()
    db.refresh(guess)
    return guess


# ----------------------------------------------------------------------------
# GET — meus palpites nesse bolão
# ----------------------------------------------------------------------------
@router.get("", response_model=list[GuessRead])
def list_my_guesses(
    bolao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lista todos os palpites do user logado nesse bolão."""
    membership = get_membership_or_403(bolao_id, current_user, db)
    stmt = (
        select(Guess)
        .where(Guess.membership_id == membership.id)
        .order_by(Guess.updated_at.desc())
    )
    return db.exec(stmt).all()


# ----------------------------------------------------------------------------
# GET — tudo que o frontend precisa pra tela de palpite (1 request)
# ----------------------------------------------------------------------------
@router.get("/guessable")
def guessable(
    bolao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retorna jogos agrupados por fase + meus palpites + status de lock de cada fase.

    Um request só monta toda a tela de palpite (não precisa fazer N requests).

    Response:
      {
        "my_guesses": {match_id: {score1, score2, pen_winner, points, locked}, ...},
        "phases": {
          "groups": {"label": "...", "lock_at": "...", "is_locked": false, "groups": {...}},
          "r32": {..., "matches": [...]},
          ...
        }
      }
    """
    membership = get_membership_or_403(bolao_id, current_user, db)

    # Palpites do user indexados por match_id
    rows = db.exec(
        select(Guess).where(Guess.membership_id == membership.id)
    ).all()
    my_guesses = {
        g.match_id: {
            "score1": g.score1,
            "score2": g.score2,
            "pen_winner": g.pen_winner,
            "points": g.points,
            "locked": g.locked,
        }
        for g in rows
    }

    phases = get_matches_grouped_for_guessing()

    results_db = db.exec(select(MatchResult)).all()
    all_matches = get_all_matches()
    official_results = {}
    for m in all_matches:
        # Base: o que a API trouxe (real_s1/real_s2 do _enrich_match)
        if m.get("real_s1") is not None and m.get("real_s2") is not None:
            official_results[m["id"]] = {
                "score1": m["real_s1"],
                "score2": m["real_s2"],
                "pen_s1": m.get("real_pen_s1"),
                "pen_s2": m.get("real_pen_s2"),
                "pen_winner": m.get("real_pen_winner", 0),
            }

    # Override: tudo que está no MatchResult (injeção manual do admin) vence
    results_db = db.exec(select(MatchResult)).all()
    for r in results_db:
        base = official_results.get(r.match_id, {})
        official_results[r.match_id] = {
            "score1": r.score1,
            "score2": r.score2,
            "pen_s1": base.get("pen_s1"),
            "pen_s2": base.get("pen_s2"),
            "pen_winner": r.pen_winner,
        }


    return {
        "my_guesses": my_guesses, 
        "phases": phases, 
        "official_results": official_results
    }

# ----------------------------------------------------------------------------
# GET — palpites de UM participante (só jogos travados) — clique no ranking
# ----------------------------------------------------------------------------
@router.get("/member/{membership_id}")
def member_guesses(
    bolao_id: int,
    membership_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. Requester precisa ser membro do bolão
    get_membership_or_403(bolao_id, current_user, db)

    # 2. O alvo precisa pertencer a ESTE bolão (não vaza de outro bolão)
    target = db.get(Membership, membership_id)
    if not target or target.bolao_id != bolao_id:
        raise HTTPException(404, "Participante não encontrado neste bolão.")

    # 3. Conjunto de jogos travados (não editáveis)
    matches_by_id = {m["id"]: m for m in get_all_matches()}
    locked_ids = {mid for mid, m in matches_by_id.items() if is_match_locked_for_editing(m)}

    # 4. Palpites do alvo SÓ dos jogos travados
    guesses = {
        g.match_id: g
        for g in db.exec(select(Guess).where(Guess.membership_id == membership_id)).all()
        if g.match_id in locked_ids
    }

    official = _official_results_map(db)

    # 5. Lista cronológica de todos os jogos travados (marca quem não palpitou)
    locked_matches = sorted(
        (m for mid, m in matches_by_id.items() if mid in locked_ids),
        key=lambda m: m.get("datetime_brt") or "",
    )
    items = []
    for m in locked_matches:
        g = guesses.get(m["id"])
        item = _match_public(m)
        item["guess"] = (
            {"score1": g.score1, "score2": g.score2, "pen_winner": g.pen_winner, "points": g.points}
            if g else None
        )
        item["official"] = official.get(m["id"])
        items.append(item)

    return {"membership_id": membership_id, "codinome": target.codinome, "items": items}


# ----------------------------------------------------------------------------
# GET — palpites de TODOS os participantes (só jogos travados)
# ----------------------------------------------------------------------------
@router.get("/all")
def all_guesses(
    bolao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_membership_or_403(bolao_id, current_user, db)

    # Membros do bolão (id → codinome)
    members = db.exec(select(Membership).where(Membership.bolao_id == bolao_id)).all()
    codinome_by_mid = {m.id: m.codinome for m in members}
    member_ids = list(codinome_by_mid.keys())
    if not member_ids:
        return {"matches": []}

    matches_by_id = {m["id"]: m for m in get_all_matches()}
    locked_ids = {mid for mid, m in matches_by_id.items() if is_match_locked_for_editing(m)}

    # Palpites de todo mundo, só dos jogos travados, agrupados por jogo
    all_g = db.exec(select(Guess).where(Guess.membership_id.in_(member_ids))).all()
    by_match = {}
    for g in all_g:
        if g.match_id not in locked_ids:
            continue
        by_match.setdefault(g.match_id, []).append({
            "membership_id": g.membership_id,
            "codinome": codinome_by_mid.get(g.membership_id, "—"),
            "score1": g.score1, "score2": g.score2,
            "pen_winner": g.pen_winner, "points": g.points,
        })

    official = _official_results_map(db)

    locked_matches = sorted(
        (matches_by_id[mid] for mid in by_match.keys()),
        key=lambda m: m.get("datetime_brt") or "",
    )
    out = []
    for m in locked_matches:
        entry = _match_public(m)
        entry["guesses"] = sorted(by_match[m["id"]], key=lambda x: -x["points"])
        entry["official"] = official.get(m["id"])
        out.append(entry)

    return {"matches": out}
