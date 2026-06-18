
from math import floor
from typing import Optional
import unicodedata
import difflib

# ============================================================================
# PESOS POR FASE
# ============================================================================
# Dicionário de exceções (fases de mata-mata). Qualquer round que NÃO começar
# com "Matchday" vai ser buscado aqui; não achando, assume peso 1 (default).
_PHASE_WEIGHTS: dict[str, int] = {
    "Round of 32": 2,
    "Round of 16": 2,
    "Quarter-finals": 3,
    "Semi-finals": 3,
    "Play-off for third place": 3,
    "Final": 4,
}


def _phase_weight(match_round: Optional[str]) -> int:
    """
    Retorna o peso (multiplicador) da fase do jogo.

    Rodadas da fase de grupos no openfootball são numeradas como "Matchday 1"
    até "Matchday 17" (a Copa 2026 tem 48 times = mais matchdays que antes).
    Tratamos todas como peso 1 via prefix check — mais robusto que enumerar.

    Qualquer round desconhecido cai em peso 1 (default seguro).
    """
    if not match_round:
        return 1
    if match_round.startswith("Matchday"):
        return 1
    return _PHASE_WEIGHTS.get(match_round, 1)


# ============================================================================
# PONTUAÇÃO DE UMA PARTIDA
# ============================================================================
def calculate_match_points(
    guess_s1: int,
    guess_s2: int,
    real_s1: int,
    real_s2: int,
    match_round: Optional[str] = None,
    guess_pen_winner: int = 0,
    real_pen_winner: int = 0,
) -> int:
    """
    Pontuação de um palpite de placar, com peso da fase aplicado.

    Args:
        guess_s1, guess_s2: placar palpitado (tempo normal + prorrogação)
        real_s1, real_s2:   placar real (tempo normal + prorrogação)
        match_round:        string da fase (ex: "Matchday 3", "Final"). Default: peso 1.
        guess_pen_winner:   se o jogo foi pra pênaltis, quem o user palpitou que venceu?
                            0 = não palpitou / sem pênaltis, 1 = time 1, 2 = time 2
        real_pen_winner:    vencedor real nos pênaltis (mesma convenção).
                            0 = jogo não foi pra pênaltis.

    Returns:
        Pontuação do palpite (int >= 0).

 
    """

    if guess_s1 is None or guess_s2 is None: 
       return 0


    weight = _phase_weight(match_round)

    def _winner(s1: int, s2: int) -> int:
        if s1 > s2:
            return 1
        if s2 > s1:
            return 2
        return 0

    g_winner = _winner(guess_s1, guess_s2)
    r_winner = _winner(real_s1, real_s2)

    # ─── 1. Placar exato? ───
    if guess_s1 == real_s1 and guess_s2 == real_s2:
        base_points = 5
        # Jogo foi pra pênaltis E user palpitou quem vence nos pênaltis?
        # Só penaliza se o user de fato chutou um pen_winner (>0); se não
        # chutou (=0), tratamos como "não sabia que era mata-mata" e damos
        # pontos cheios — decisão de produto para não punir user desavisado.
        if real_pen_winner != 0 and guess_pen_winner != 0:
            if guess_pen_winner == real_pen_winner:
                return (base_points * weight) + weight  # Acertou placar exato + acertou pen_winner → pontuação cheia
            
            return (max(1,weight)* base_points) - weight  # Acertou placar exato + errou pen_winner → penaliza peso (mínimo 1×)
        
        return base_points * weight


        
    
    if g_winner == 0 and r_winner == 0:
        base_points = 3
        if real_pen_winner != 0 and guess_pen_winner != 0:
            if guess_pen_winner == real_pen_winner:
                return (base_points * weight) + weight  # Errou placar exato do empate + acertou pen_winner 
            return (max(1, weight)*base_points) - weight  # Errou placar exato do empate  e errou pen_winner 
        return base_points* weight



    if g_winner == r_winner and g_winner != 0:
        base_points = 2

        if guess_s1 == real_s1 or guess_s2 == real_s2:
            base_points += 1  # Acertou vencedor e um dos placares (ex: palpite 2×0, real 2×1)
        
        elif abs((guess_s1 - guess_s2) ==  abs(real_s1 - real_s2)):
            base_points += 1  # Acertou vencedor e placares próximos (ex: palpite 2×0, real 3×1)
        
        return base_points * weight

    if guess_s1 == real_s1 or guess_s2 == real_s2:
        base_points = 1  # Acertou só vencedor/empate (placar ≠) ou um dos placares certos
        return base_points * weight
        
    
    return 0


# ============================================================================
# PONTUAÇÃO DE TORNEIO (outrights, palpites pré-Copa)
# ============================================================================
def _checar_match_flexivel(palpite: Optional[str], oficial: Optional[str], threshold: float = 0.7) -> bool:
    """Valida acertos considerando minúsculas, sem acentos, substrings ou similaridade textual."""
    if not palpite or not oficial:
        return False
        
    def limpar(texto):
        t = str(texto).strip().lower()
        return ''.join(c for c in unicodedata.normalize('NFD', t) if unicodedata.category(c) != 'Mn')

    p_limpo = limpar(palpite)
    o_limpo = limpar(oficial)

    if p_limpo == o_limpo:
        return True
    if p_limpo in o_limpo or o_limpo in p_limpo:
        return True
        
    similaridade = difflib.SequenceMatcher(None, p_limpo, o_limpo).ratio()
    return similaridade >= threshold


def calculate_tournament_points(
    guess_champion: Optional[str],
    guess_scorer: Optional[str],
    guess_best_player: Optional[str],
    real_champion: Optional[str],
    real_scorer: Optional[str],
    real_best_player: Optional[str],
) -> int:
    """
    Pontos por palpites gerais do torneio.
    Soma 20 pontos por acerto usando a verificação flexível (fuzzy match).
    """
    points = 0
    if real_champion and _checar_match_flexivel(guess_champion, real_champion):
        points += 20
    if real_scorer and _checar_match_flexivel(guess_scorer, real_scorer):
        points += 20    
    if real_best_player and _checar_match_flexivel(guess_best_player, real_best_player):
        points += 20
    return points