"""
services/ranking.py — utilitários de classificação.

snapshot_ranking_positions: grava em Membership.last_position a posição
ATUAL de cada membro no seu bolão. Deve ser chamado SEMPRE antes de aplicar
um novo resultado (no admin), pra que a variação (setinha) do ranking passe
a refletir o movimento causado pelo último resultado — e não a posição lá do
início da Copa.

A ordenação aqui é idêntica à do endpoint GET /boloes/{id}/ranking:
pontos (palpites + bônus) desc, e codinome asc como desempate.
"""
from sqlmodel import Session, func, select

from models import Membership, Guess, ExtraGuess


def snapshot_ranking_positions(db: Session) -> None:
    stmt = (
        select(
            Membership.id,
            Membership.bolao_id,
            Membership.codinome,
            (func.coalesce(func.sum(Guess.points), 0) + func.coalesce(ExtraGuess.points, 0)).label("total_points"),
        )
        .join(Guess, Guess.membership_id == Membership.id, isouter=True)
        .join(ExtraGuess, ExtraGuess.membership_id == Membership.id, isouter=True)
        .group_by(Membership.id, Membership.bolao_id, Membership.codinome, ExtraGuess.points)
    )
    rows = db.exec(stmt).all()

    # Agrupa por bolão
    by_bolao = {}
    for mid, bolao_id, codinome, total in rows:
        by_bolao.setdefault(bolao_id, []).append((mid, codinome or "", int(total or 0)))

    # Ordena cada bolão igual ao ranking e grava a posição como baseline
    for members in by_bolao.values():
        members.sort(key=lambda x: (-x[2], x[1]))  # pontos desc, codinome asc
        for pos, (mid, _codinome, _total) in enumerate(members, start=1):
            m = db.get(Membership, mid)
            if m and m.last_position != pos:
                m.last_position = pos
                db.add(m)
    # Sem commit aqui de propósito: o caller (admin) faz o commit junto com a
    # aplicação do resultado, mantendo tudo numa transação só.