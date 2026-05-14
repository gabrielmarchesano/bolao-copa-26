"""
scoring.py — regras de pontuação do bolão.

CONCEITO: funções puras (sem side effects, sem I/O, sem banco).
Fácil de testar, fácil de trocar a regra sem quebrar nada.

═══════════════════════════════════════════════════════════════
REGRA BASE (fase de grupos, peso 1)
═══════════════════════════════════════════════════════════════
- Placar exato                     → 3 pts
- Só vencedor/empate (placar ≠)    → 1 pt
- Errou tudo                       → 0 pts

═══════════════════════════════════════════════════════════════
PESOS POR FASE (multiplicadores)
═══════════════════════════════════════════════════════════════
- Fase de grupos (Matchday N)      → peso 1
- Oitavas de 32 (Round of 32)      → peso 2
- Oitavas de 16 (Round of 16)      → peso 2
- Quartas (Quarter-finals)         → peso 3
- Semi (Semi-finals)               → peso 4
- 3º lugar (Play-off 3rd place)    → peso 2
- Final                            → peso 5

═══════════════════════════════════════════════════════════════
REGRA DE PÊNALTIS (mata-mata que termina empatado e vai pra pênaltis)
═══════════════════════════════════════════════════════════════
Só aplica quando:
  (a) o jogo teve decisão por pênaltis (real_pen_winner != 0)
  (b) o palpite foi um empate exato (guess_s1 == guess_s2 e bateu com
      o placar real do tempo normal/prorrogação)
  (c) o palpite também inclui um chute de quem vence nos pênaltis

Cenários:
  - Acertou placar exato + acertou pen_winner  → 3 × peso (pontuação cheia)
  - Acertou placar exato + errou pen_winner    → floor(2/3 × 3 × peso)
  - Acertou só vencedor/empate                 → 1 × peso (regra normal, pen ignorado)

═══════════════════════════════════════════════════════════════
REGRA DE TORNEIO (outrights, palpites pré-Copa)
═══════════════════════════════════════════════════════════════
- Campeão exato          → 50 pts
- Artilheiro exato       → 30 pts
- Melhor jogador exato   → 30 pts
(valores FIXOS — não multiplicam por peso)
"""
from math import floor
from typing import Optional


# ============================================================================
# PESOS POR FASE
# ============================================================================
# Dicionário de exceções (fases de mata-mata). Qualquer round que NÃO começar
# com "Matchday" vai ser buscado aqui; não achando, assume peso 1 (default).
_PHASE_WEIGHTS: dict[str, int] = {
    "Round of 32": 2,
    "Round of 16": 2,
    "Quarter-finals": 3,
    "Semi-finals": 4,
    "Play-off for third place": 2,
    "Final": 5,
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

    Exemplos:
        >>> calculate_match_points(2, 1, 2, 1)             # placar exato, grupos
        3
        >>> calculate_match_points(2, 1, 3, 0)             # só vencedor, grupos
        1
        >>> calculate_match_points(1, 1, 1, 1, "Final")    # placar exato, final
        15
        >>> calculate_match_points(1, 1, 1, 1, "Semi-finals",
        ...                        guess_pen_winner=1, real_pen_winner=1)
        12  # placar exato + acertou pen → 3 × 4 = 12
        >>> calculate_match_points(1, 1, 1, 1, "Semi-finals",
        ...                        guess_pen_winner=1, real_pen_winner=2)
        8   # placar exato + errou pen → floor(2/3 × 3 × 4) = floor(8.0) = 8
    """
    weight = _phase_weight(match_round)

    # ─── 1. Placar exato? ───
    if guess_s1 == real_s1 and guess_s2 == real_s2:
        full_points = 3 * weight

        # Jogo foi pra pênaltis E user palpitou quem vence nos pênaltis?
        # Só penaliza se o user de fato chutou um pen_winner (>0); se não
        # chutou (=0), tratamos como "não sabia que era mata-mata" e damos
        # pontos cheios — decisão de produto para não punir user desavisado.
        if real_pen_winner != 0 and guess_pen_winner != 0:
            if guess_pen_winner == real_pen_winner:
                return full_points
            # Errou quem ganhou nos pênaltis → 2/3 da pontuação, floor
            return floor(full_points * 2 / 3)

        return full_points

    # ─── 2. Ambos empates, placares diferentes (ex: 1×1 palpite, 2×2 real) ───
    if guess_s1 == guess_s2 and real_s1 == real_s2:
        return 1 * weight

    # ─── 3. Acertou só quem venceu (fora empate) ───
    def _winner(s1: int, s2: int) -> int:
        if s1 > s2:
            return 1
        if s2 > s1:
            return 2
        return 0

    if _winner(guess_s1, guess_s2) == _winner(real_s1, real_s2) != 0:
        return 1 * weight

    # ─── 4. Errou tudo ───
    return 0


# ============================================================================
# PONTUAÇÃO DE TORNEIO (outrights, palpites pré-Copa)
# ============================================================================
def calculate_tournament_points(
    guess_champion: Optional[str],
    guess_scorer: Optional[str],
    guess_best_player: Optional[str],
    real_champion: Optional[str],
    real_scorer: Optional[str],
    real_best_player: Optional[str],
) -> int:
    """
    Pontos por palpites gerais do torneio (feitos antes do início).

    Comparação case-insensitive e ignorando espaços extras. Não pontua se
    a realidade ainda não foi definida (real_X == None / "").

    Returns:
        Pontuação total (int).

    Exemplos:
        >>> calculate_tournament_points("Brasil", "Vinicius Jr", "Rodrygo",
        ...                             "Brasil", "Vinicius Jr", "Mbappé")
        80  # 50 + 30 + 0
        >>> calculate_tournament_points("Brasil", None, None, None, None, None)
        0   # realidade ainda não definida
    """
    def _normalize(val: Optional[str]) -> str:
        return str(val).strip().lower() if val else ""

    points = 0
    if real_champion and _normalize(guess_champion) == _normalize(real_champion):
        points += 50
    if real_scorer and _normalize(guess_scorer) == _normalize(real_scorer):
        points += 30
    if real_best_player and _normalize(guess_best_player) == _normalize(real_best_player):
        points += 30
    return points
