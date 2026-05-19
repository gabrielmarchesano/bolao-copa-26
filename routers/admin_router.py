"""
routers/admin_router.py — endpoints de admin.

ROTAS:
  POST /admin/matches/{match_id}/result → registra placar e recalcula pontos
  POST /admin/phases/{phase_key}/force-open → força abertura de fase
  POST /admin/phases/{phase_key}/force-close → força fechamento de fase
  DELETE /admin/phases/{phase_key}/force-open → remove força de abertura
  DELETE /admin/phases/{phase_key}/force-close → remove força de fechamento
  GET /admin/phases/control-status → mostra controle manual de fases

SEGURANÇA: header `X-Admin-Token: <ADMIN_TOKEN>` em todas as rotas.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from auth import require_admin
from database import get_db
from models import Guess, MatchResult, ExtraGuess, ChampionshipResult
from schemas import MatchResultCreate, ExtraValidationPayload
from scoring import calculate_match_points, calculate_tournament_points
from services import get_all_matches
from services.phase_control import (
    force_open_phase,
    force_close_phase,
    remove_force_open,
    remove_force_close,
    get_phase_control_status,
)
from services.phases import PHASE_ORDER

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)

@router.post("/championship-results")
def set_championship_results(
    payload: ExtraValidationPayload, 
    db: Session = Depends(get_db), 
):
    """
    Rota do Admin Geral da Plataforma. 
    Define o gabarito oficial da Copa e calcula os pontos bônus de TODOS os usuários do sistema.
    """
    # 1. Salva ou atualiza o gabarito único (ID 1) no banco de dados
    oficial = db.get(ChampionshipResult, 1)
    if not oficial:
        oficial = ChampionshipResult(id=1, campeao="", artilheiro="", melhor_jogador="")
        db.add(oficial)
        
    oficial.campeao = payload.campeao_oficial
    oficial.artilheiro = payload.artilheiro_oficial
    oficial.melhor_jogador = payload.melhor_jogador_oficial
    oficial.updated_at = datetime.utcnow()
    db.commit()

    # 2. Busca os palpites extras de ABSOLUTAMENTE TODO MUNDO na plataforma
    todos_palpites = db.exec(select(ExtraGuess)).all()

    # 3. Calcula os pontos usando a regra do scoring.py e atualiza as linhas
    for palpite in todos_palpites:
        pontos_ganhos = calculate_tournament_points(
            guess_champion=palpite.campeao,
            guess_scorer=palpite.artilheiro,
            guess_best_player=palpite.melhor_jogador,
            real_champion=oficial.campeao,
            real_scorer=oficial.artilheiro,
            real_best_player=oficial.melhor_jogador
        )
        palpite.points = pontos_ganhos
        db.add(palpite)

    db.commit()
    return {"status": "ok", "message": "Gabarito de torneio salvo e pontos extras distribuídos globalmente!"}

@router.delete("/championship-results")
def reset_championship_results(db: Session = Depends(get_db)):
    """
    Reseta o gabarito oficial e zera os pontos extras de todos os palpites de torneio.
    """
    oficial = db.get(ChampionshipResult, 1)
    if not oficial:
        raise HTTPException(404, "Gabarito oficial não encontrado.")
    
    oficial.campeao = None
    oficial.artilheiro = None
    oficial.melhor_jogador = None
    oficial.updated_at = datetime.utcnow()
    db.add(oficial)

    todos_palpites = db.exec(select(ExtraGuess)).all()
    for palpite in todos_palpites:
        palpite.points = 0
        db.add(palpite)

    db.commit()
    return {"status": "ok", "message": "Gabarito de torneio resetado e pontos extras zerados!"}


@router.post("/matches/{match_id}/result")
def set_match_result(
    match_id: int,
    payload: MatchResultCreate,
    db: Session = Depends(get_db),
):
    """
    Registra/atualiza resultado real e recalcula pontos de todos os palpites.

    Body:
        {"score1": 1, "score2": 1, "pen_winner": 1}  # 0 se não teve pênaltis

    Valida:
        - match_id existe no openfootball
        - score1/score2 >= 0
        - pen_winner em {0, 1, 2}
    """
    # Valida match_id
    all_matches = get_all_matches()
    match = next((m for m in all_matches if m["id"] == match_id), None)
    if not match:
        raise HTTPException(404, f"Jogo {match_id} não encontrado.")

    if payload.score1 < 0 or payload.score2 < 0:
        raise HTTPException(400, "Placares devem ser não-negativos.")
    if payload.pen_winner not in (0, 1, 2):
        raise HTTPException(400, "pen_winner deve ser 0, 1 ou 2.")

    match_round = match.get("round")

    # 1. Upsert do MatchResult
    result = db.get(MatchResult, match_id)
    if result:
        result.score1 = payload.score1
        result.score2 = payload.score2
        result.pen_winner = payload.pen_winner
        result.is_manual_override = True # ATIVANDO A TRAVA
        result.updated_at = datetime.utcnow()
    else:
        result = MatchResult(
            match_id=match_id,
            score1=payload.score1,
            score2=payload.score2,
            pen_winner=payload.pen_winner,
            is_manual_override=True,
        )
        db.add(result)

    # 2. Recalcula pontos de todos os palpites desse jogo
    guesses = db.exec(select(Guess).where(Guess.match_id == match_id)).all()
    for g in guesses:
        g.points = calculate_match_points(
            guess_s1=g.score1,
            guess_s2=g.score2,
            real_s1=payload.score1,
            real_s2=payload.score2,
            match_round=match_round,
            guess_pen_winner=g.pen_winner,
            real_pen_winner=payload.pen_winner,
        )
        g.locked = True

    db.commit()

    return {
        "status": "ok",
        "match_id": match_id,
        "match_round": match_round,
        "score1": payload.score1,
        "score2": payload.score2,
        "pen_winner": payload.pen_winner,
        "guesses_updated": len(guesses),
    }

# No arquivo admin_router.py

@router.delete("/matches/{match_id}/result")
def reset_match_result(
    match_id: int,
    db: Session = Depends(get_db),
):
    """
    Remove o resultado oficial e volta os palpites ao estado aberto (0 pontos).
    """
    # 1. Busca e remove o resultado oficial
    result = db.get(MatchResult, match_id)
    if not result:
        raise HTTPException(404, "Resultado não encontrado para este jogo.")

    db.delete(result)

    # 2. Busca todos os palpites desse jogo, destrava e zera os pontos
    guesses = db.exec(select(Guess).where(Guess.match_id == match_id)).all()
    for g in guesses:
        g.points = 0
        g.locked = False

    db.commit()
    return {"status": "ok", "message": "Resultado removido e palpites resetados com sucesso."}




# ============================================================================
# CONTROLE DE FASES
# ============================================================================
@router.post("/phases/{phase_key}/force-open")
def admin_force_open_phase(phase_key: str):
    """Força abertura de uma fase (admin override)."""
    if phase_key not in PHASE_ORDER:
        raise HTTPException(400, f"Fase '{phase_key}' inválida.")
    force_open_phase(phase_key)
    return {"status": "ok", "action": "force_open", "phase": phase_key}


@router.post("/phases/{phase_key}/force-close")
def admin_force_close_phase(phase_key: str):
    """Força fechamento de uma fase (admin override)."""
    if phase_key not in PHASE_ORDER:
        raise HTTPException(400, f"Fase '{phase_key}' inválida.")
    force_close_phase(phase_key)
    return {"status": "ok", "action": "force_close", "phase": phase_key}


@router.delete("/phases/{phase_key}/force-open")
def admin_remove_force_open(phase_key: str):
    """Remove força de abertura de uma fase."""
    if phase_key not in PHASE_ORDER:
        raise HTTPException(400, f"Fase '{phase_key}' inválida.")
    remove_force_open(phase_key)
    return {"status": "ok", "action": "remove_force_open", "phase": phase_key}


@router.delete("/phases/{phase_key}/force-close")
def admin_remove_force_close(phase_key: str):
    """Remove força de fechamento de uma fase."""
    if phase_key not in PHASE_ORDER:
        raise HTTPException(400, f"Fase '{phase_key}' inválida.")
    remove_force_close(phase_key)
    return {"status": "ok", "action": "remove_force_close", "phase": phase_key}


@router.get("/phases/control-status")
def admin_get_phase_control_status():
    """Retorna status do controle manual de fases."""
    return get_phase_control_status()


@router.post("/sync-external-results")
def sync_external_results(db: Session = Depends(get_db)):
    """
    Robô que olha a API externa e processa os jogos finalizados.
    Pode ser chamado a cada 5 minutos por um Cron Job.
    """
    all_matches = get_all_matches() # Puxa da sua fonte externa
    updated_count = 0

    for match in all_matches:
        # Verifica se o jogo já tem placar na API externa
        if match.get("score1") is not None and match.get("score2") is not None:
            match_id = match["id"]
            
            # Verifica se já temos esse resultado no banco local
            result = db.get(MatchResult, match_id)

            # Se o admin (você) já alterou isso na mão, o robô PULA e não mexe!
            if result and result.is_manual_override:
                continue

            # Se é um resultado novo ou o placar mudou na API externa (ex: VAR anulou gol)
            if not result or result.score1 != match["score1"] or result.score2 != match["score2"]:
                if not result:
                    result = MatchResult(
                        match_id=match_id, 
                        score1=match["score1"], 
                        score2=match["score2"],
                        is_manual_override=False # Resultado automático
                    )
                    db.add(result)
                else:
                    result.score1 = match["score1"]
                    result.score2 = match["score2"]
                    result.updated_at = datetime.utcnow()

                # Recalcula os pontos de todo mundo para esse jogo
                guesses = db.exec(select(Guess).where(Guess.match_id == match_id)).all()
                for g in guesses:
                    g.points = calculate_match_points(
                        guess_s1=g.score1,
                        guess_s2=g.score2,
                        real_s1=result.score1,
                        real_s2=result.score2,
                        match_round=match.get("round"),
                        guess_pen_winner=g.pen_winner,
                        real_pen_winner=0 # Assumindo que a API externa não manda penaltis fácil, vc preenche na mão se precisar
                    )
                    g.locked = True
                
                updated_count += 1

    db.commit()
    return {"status": "ok", "matches_synced": updated_count}