"""
schemas.py — schemas de input/output da API.

Separação MODELS (persistência) vs SCHEMAS (API contract).
Evita vazar campos sensíveis (ex: password_hash) na resposta.
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from sqlmodel import Field, SQLModel


# ============================================================================
# USER / AUTH
# ============================================================================
class UserCreate(SQLModel):
    email: str
    password: str
    full_name: str


class UserRead(SQLModel):
    id: int
    email: str
    full_name: str
    created_at: datetime


class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# ============================================================================
# BOLÃO
# ============================================================================
class BolaoCreate(SQLModel):
    name: str
    visibility: str = "private"
    description: str = ""
    codinome: str = ""


class BolaoRead(SQLModel):
    id: int
    name: str
    invite_code: str
    visibility: str
    description: str
    owner_id: int
    created_at: datetime
    members_count: int = 0


class BolaoJoin(SQLModel):
    invite_code: str
    codinome: str

class BolaoUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

# ============================================================================
# GUESS
# ============================================================================
class GuessCreate(SQLModel):
    """
    Body do POST /boloes/{id}/guesses.

    `pen_winner` é opcional — só relevante em palpites de empate em mata-mata.
      0 = não palpitou (ignore)
      1 = time 1 vence nos pen
      2 = time 2 vence nos pen
    """
    match_id: int
    score1: int
    score2: int
    pen_winner: int = 0


class GuessRead(SQLModel):
    id: int
    match_id: int
    score1: int
    score2: int
    pen_winner: int
    points: int
    locked: bool
    updated_at: datetime


# ============================================================================
# RANKING
# ============================================================================
class RankingRow(SQLModel):
    membership_id: int
    real_name: Optional[str] = None
    codinome: str
    total_points: int
    guesses_count: int
    position: int


# ============================================================================
# ADMIN
# ============================================================================
class MatchResultCreate(SQLModel):
    """Body do POST /admin/matches/{match_id}/result."""
    score1: int
    score2: int
    pen_winner: int = 0  # 0 se o jogo não teve pênaltis


# ============================================================================
# FASES (lock info)
# ============================================================================
class PhaseLockInfo(SQLModel):
    """
    Informação sobre uma fase do torneio: nome canônico, jogos que pertencem
    a ela, quando trava (kickoff do 1º jogo), e se já travou.

    Usado pelo frontend pra montar as telas de palpite agrupadas por fase.
    """
    phase: str                # "Fase de Grupos", "Oitavas de 32", "Final", etc
    phase_key: str            # "groups", "r32", "r16", "qf", "sf", "third", "final"
    lock_at: Optional[str]    # ISO BRT do kickoff do 1º jogo da fase
    is_locked: bool           # True se o lock já passou (ou jogo começou)
    matches_count: int
# ============================================================================
# JOIN REQUEST
# ============================================================================
class JoinRequestCreate(SQLModel):
    """Body do POST /boloes/{id}/join-requests."""
    codinome: str
    message: str = ""


class JoinRequestRead(SQLModel):
    id: int
    bolao_id: int
    bolao_name: str
    user_id: int
    user_name: str          # full_name do solicitante (mostrar ao owner)
    codinome: str
    message: str
    status: str
    created_at: datetime
    responded_at: Optional[datetime]


class JoinRequestRespond(SQLModel):
    """Body do POST /join-requests/{id}/respond."""
    action: str  # "accept" | "reject"


# ============================================================================
# NOTIFICATION
# ============================================================================
class NotificationRead(SQLModel):
    id: int
    type: str
    title: str
    body: str
    data: dict = Field(default_factory=dict)  # parsed de JSON string pro cliente
    is_read: bool
    action_url: Optional[str]
    created_at: datetime


class NotificationsEnvelope(SQLModel):
    """Resposta do GET /notifications — inclui contador pra badge."""
    unread_count: int
    items: List[NotificationRead]


# ============================================================================
# BUSCA DE BOLÕES
# ============================================================================
class BolaoSearchResult(SQLModel):
    """Card de bolão na busca. Sem invite_code (não vaza pra não-membros)."""
    id: int
    name: str
    visibility: str
    members_count: int
    has_pending_request: bool   # True se já solicitou e está pending
    is_member: bool             # sempre False aqui (filtramos), mas mantém pra clareza


# ============================================================================
# PREVIEW DE BOLÃO POR INVITE CODE
# ============================================================================
class BolaoPreview(SQLModel):
    """
    Preview minimalista de um bolão a partir do invite_code, usado no
    fluxo de aceitar convite via link compartilhado.

    Retorna apenas o necessário pra o user logado decidir se quer entrar:
    nome, visibilidade, contagem de membros. Não vaza owner_id ou estrutura
    interna. is_member sinaliza se o user já participa (evita duplicar).
    """
    id: int
    name: str
    visibility: str
    members_count: int
    is_member: bool