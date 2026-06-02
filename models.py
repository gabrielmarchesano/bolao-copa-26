"""
models.py — tabelas do banco de dados.

NOTA DE COMPATIBILIDADE (Python 3.13 + SQLAlchemy 2.x):
  - NÃO usar `from __future__ import annotations` aqui — quebra o SQLAlchemy
    novo, que não consegue mais resolver tipos de Relationship() como strings.
  - Usar `List["X"]` e `Optional[X]` do `typing` em vez de `list["X"]` / `X | None`.
"""
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class User(SQLModel, table=True):
    """Usuário único da plataforma."""
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=120)
    password_hash: str = Field(max_length=200)
    full_name: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memberships: List["Membership"] = Relationship(back_populates="user")


class Bolao(SQLModel, table=True):
    """Bolão criado por um User (owner) e agrega Memberships."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=80)
    invite_code: str = Field(unique=True, index=True, max_length=16)
    owner_id: int = Field(foreign_key="user.id")
    visibility: str = Field(default="private", max_length=10)
    description: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    memberships: List["Membership"] = Relationship(
        back_populates="bolao",
        sa_relationship_kwargs={"cascade": "all, delete"}
    )


class Membership(SQLModel, table=True):
    """
    Liga User ↔ Bolão com codinome próprio por bolão.
    Permite que o mesmo user tenha codinomes diferentes em bolões diferentes.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    bolao_id: int = Field(foreign_key="bolao.id", index=True)
    codinome: str = Field(max_length=40)
    joined_at: datetime = Field(default_factory=datetime.utcnow)
    last_position: int = Field(default=0)
    
    user: "User" = Relationship(back_populates="memberships")
    bolao: "Bolao" = Relationship(back_populates="memberships")
    guesses: List["Guess"] = Relationship(
        back_populates="membership",
        sa_relationship_kwargs={"cascade": "all, delete"}
    )


class Guess(SQLModel, table=True):
    """
    Palpite de placar para um match, dentro de um bolão.

    `pen_winner` (NOVO): chute de quem vence nos pênaltis em jogo de mata-mata.
      0 = não palpitou (default, válido pra grupos ou user que não quis chutar)
      1 = palpita time 1
      2 = palpita time 2

    Só é relevante em jogos de mata-mata quando o palpite é um empate
    (score1 == score2). O frontend exibe condicionalmente.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    membership_id: int = Field(foreign_key="membership.id", index=True)
    match_id: int = Field(index=True)

    score1: int
    score2: int
    pen_winner: int = Field(default=0)

    points: int = Field(default=0)
    locked: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    membership: "Membership" = Relationship(back_populates="guesses")


class ChampionshipResult(SQLModel, table=True):
    """Resultados oficiais globais do torneio (Gabarito para validação do Admin)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    
    campeao: str = Field(max_length=100)
    artilheiro: str = Field(max_length=100)
    melhor_jogador: str = Field(max_length=100)
    
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MatchResult(SQLModel, table=True):
    """Resultado real de um jogo (inserido pelo admin)."""
    match_id: int = Field(primary_key=True)
    score1: int
    score2: int
    pen_winner: int = Field(default=0)  # NOVO: vencedor nos pênaltis (0 se não teve)

    is_manual_override: bool = Field(default=False)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# ============================================================================
# JOIN_REQUEST — solicitação de entrada em bolão privado
# ============================================================================
class JoinRequest(SQLModel, table=True):
    """
    Solicitação pendente pra entrar num bolão privado.

    Status:
      - "pending"  → aguardando resposta do owner
      - "accepted" → aprovada (vira Membership)
      - "rejected" → recusada

    Regra de unicidade: só existe UMA request ativa ('pending') por par
    (user, bolão). O endpoint POST dispara lógica de "reactivate" se existir
    uma rejected/accepted antiga — reaproveita a row.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    bolao_id: int = Field(foreign_key="bolao.id", index=True)

    codinome: str = Field(max_length=40)  # codinome pretendido (usa ao aceitar)
    message: str = Field(default="", max_length=200)  # msg opcional do solicitante
    status: str = Field(default="pending", max_length=10, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    responded_at: Optional[datetime] = Field(default=None)



class ExtraGuess(SQLModel, table=True):
    """Palpites extras (Campeão, Artilheiro, Melhor Jogador) que valem pontos bônus."""
    id: Optional[int] = Field(default=None, primary_key=True)
    membership_id: int = Field(foreign_key="membership.id", index=True, unique=True)
    
    campeao: Optional[str] = Field(default=None, max_length=100)
    artilheiro: Optional[str] = Field(default=None, max_length=100)
    melhor_jogador: Optional[str] = Field(default=None, max_length=100)

    points: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)



# ============================================================================
# NOTIFICATION — caixa de entrada de cada user
# ============================================================================
class Notification(SQLModel, table=True):
    """
    Notificação genérica, desenhada pra acomodar múltiplos tipos.

    Tipos atuais:
      - "join_request"   → owner recebe quando alguém solicita entrar
      - "request_accepted" → solicitante recebe quando aceito
      - "request_rejected" → solicitante recebe quando rejeitado
      - "phase_lock_soon" → todos os membros quando fase vai travar em 24h

    Para adicionar um novo tipo, não precisa mudar a tabela — só criar o
    constant novo no service e renderizar no frontend.

    `data` guarda JSON serializado com contexto específico do tipo
    (ex: join_request_id, bolao_id, bolao_name). Evita JOINs complexos
    e acopla os dados da notificação ao momento em que foi criada.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    type: str = Field(max_length=30, index=True)
    title: str = Field(max_length=120)
    body: str = Field(max_length=400)
    data: str = Field(default="{}")  # JSON serializado

    is_read: bool = Field(default=False, index=True)
    action_url: Optional[str] = Field(default=None, max_length=200)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    read_at: Optional[datetime] = Field(default=None)