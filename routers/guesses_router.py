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
from models import Guess, User, MatchResult
from schemas import GuessCreate, GuessRead
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

    # LOCK INDIVIDUAL: palpite fecha 1h antes do kickoff DESTE jogo.
    # Usa o datetime_brt do próprio match (fonte: API). Recalculado a cada
    # request com datetime.now, então o cache dinâmico não interfere.
    dt_str = match.get("datetime_brt")
    if not dt_str:
        raise HTTPException(400, "Jogo sem data definida — palpite indisponível.")

    dt_brt = datetime.fromisoformat(dt_str)
    now = datetime.now(dt_brt.tzinfo)
    cutoff = dt_brt - timedelta(minutes=30)
    if now >= cutoff:
        raise HTTPException(
            status_code=400,
            detail="Palpites para este jogo estão encerrados (fecha 1h antes do início).",
        )
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
    _validate_guess_payload(payload, match)

    # Se palpite NÃO for empate, zera pen_winner (user mudou de ideia)
    pen = payload.pen_winner if payload.score1 == payload.score2 else 0

    existing = db.exec(
        select(Guess).where(
            Guess.membership_id == membership.id,
            Guess.match_id == payload.match_id,
        )
    ).first()

    if existing:
        existing.score1 = payload.score1
        existing.score2 = payload.score2
        existing.pen_winner = pen
        existing.updated_at = datetime.utcnow()
        guess = existing
    else:
        guess = Guess(
            membership_id=membership.id,
            match_id=payload.match_id,
            score1=payload.score1,
            score2=payload.score2,
            pen_winner=pen,
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
                "pen_winner": m.get("real_pen_winner", 0),
            }

    # Override: tudo que está no MatchResult (injeção manual do admin) vence
    results_db = db.exec(select(MatchResult)).all()
    for r in results_db:
        official_results[r.match_id] = {
            "score1": r.score1,
            "score2": r.score2,
            "pen_winner": r.pen_winner,
        }


    return {
        "my_guesses": my_guesses, 
        "phases": phases, 
        "official_results": official_results
    }

