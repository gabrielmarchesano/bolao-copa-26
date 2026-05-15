"""
routers/boloes_router.py — endpoints de bolões.

ROTAS:
  POST /boloes           → cria bolão (user vira owner e primeiro membro)
  GET  /boloes/me        → lista bolões que o user participa
  GET  /boloes/{id}      → detalhes (só membros)
  POST /boloes/join      → entra em bolão via invite_code + codinome
  GET  /boloes/{id}/ranking → tabela de classificação ordenada por pontos

CONCEITO NOVO: queries agregadas (func.sum, func.count, GROUP BY) direto
no SQLAlchemy. Evita loops em Python pra calcular ranking — deixa o banco
fazer o trabalho pesado com índices.
"""
import secrets
import string
import models
import schemas
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, func, select

from auth import get_current_user, get_membership_or_403
from database import get_db
from models import Bolao, Guess, Membership, User
from schemas import BolaoCreate, BolaoJoin, BolaoPreview, BolaoRead, RankingRow
from models import JoinRequest, Notification
from schemas import BolaoSearchResult, JoinRequestCreate, JoinRequestRead, JoinRequestRespond, CodinomeUpdate, BolaoUpdate
from services import notifications as notif_svc
from services.phases import has_tournament_started



router = APIRouter(prefix="/boloes", tags=["boloes"])


# ----------------------------------------------------------------------------
# Helper: gerar invite_code aleatório e único
# ----------------------------------------------------------------------------
def _generate_invite_code(length: int = 6) -> str:
    """
    Gera código tipo 'COPA-A2X9K7'. Usa `secrets` (não `random`) porque é seguro
    criptograficamente — impossível alguém chutar códigos de outros bolões.

    Remove caracteres ambíguos (0/O, 1/I) pra evitar erro humano ao digitar.
    """
    alphabet = string.ascii_uppercase + string.digits
    for c in "0O1I":
        alphabet = alphabet.replace(c, "")
    body = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"COPA-{body}"


def _bolao_to_read(bolao: Bolao, members_count: int) -> BolaoRead:
    """Converte model → schema de resposta. Pattern repetido, fica isolado aqui."""
    return BolaoRead(
        id=bolao.id,
        name=bolao.name,
        invite_code=bolao.invite_code,
        visibility=bolao.visibility,
        description=bolao.description,
        owner_id=bolao.owner_id,
        created_at=bolao.created_at,
        members_count=members_count,
    )


# ----------------------------------------------------------------------------
# POST /boloes — criar
# ----------------------------------------------------------------------------
@router.post("", response_model=BolaoRead, status_code=201)
def create_bolao(
    payload: BolaoCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cria um bolão. O owner entra automaticamente como primeiro membro,
    com codinome fornecido pelo user ou primeiro nome se vazio.
    """
    # Tenta gerar código único (10 tentativas de segurança)
    for _ in range(10):
        code = _generate_invite_code()
        if not db.exec(select(Bolao).where(Bolao.invite_code == code)).first():
            break
    else:
        raise HTTPException(500, "Falha ao gerar código único — tente novamente.")

    bolao = Bolao(
        name=payload.name,
        invite_code=code,
        owner_id=current_user.id,
        visibility=payload.visibility,
        description=payload.description.strip(),
    )
    db.add(bolao)
    db.commit()
    db.refresh(bolao)

    # Owner vira primeiro membro automaticamente
    if payload.codinome and payload.codinome.strip():
        codinome = payload.codinome.strip()
    else:
        codinome = current_user.full_name.split()[0] if current_user.full_name else "User"
    
    # Validação do codinome
    if len(codinome) > 40:
        codinome = codinome[:40]
    
    membership = Membership(
        user_id=current_user.id,
        bolao_id=bolao.id,
        codinome=codinome,
    )
    db.add(membership)
    db.commit()

    return _bolao_to_read(bolao, members_count=1)




# ----------------------------------------------------------------------------
# GET /boloes/me — meus bolões
# ----------------------------------------------------------------------------
@router.get("/me", response_model=list[BolaoRead])
def list_my_boloes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lista bolões que o user participa, com contagem de membros.

    Query: JOIN Bolao ↔ Membership, filtrando pelos bolões onde user_id = logado,
    agrupando pra contar membros.
    """
    stmt = (
        select(Bolao, func.count(Membership.id).label("member_count"))
        .join(Membership, Membership.bolao_id == Bolao.id)
        .where(
            Bolao.id.in_(
                select(Membership.bolao_id).where(Membership.user_id == current_user.id)
            )
        )
        .group_by(Bolao.id)
        .order_by(Bolao.created_at.desc())
    )
    rows = db.exec(stmt).all()
    return [_bolao_to_read(b, count) for b, count in rows]


# ============================================================================
# GET /boloes/search — buscar bolões pra entrar
# ============================================================================
@router.get("/search", response_model=list[BolaoSearchResult])
def search_boloes(
    q: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Busca bolões por nome (case-insensitive, partial match).
    - Retorna públicos E privados (decisão de produto)
    - Exclui bolões que o user JÁ participa
    - Marca `has_pending_request` pra UI mostrar "Solicitação enviada"
    - Q vazia retorna lista vazia (evita dump do banco inteiro)
    """
    term = q.strip()
    if not term or len(term) < 2:
        return []

    # IDs dos bolões que o user JÁ é membro → excluir
    member_bolao_ids = db.exec(
        select(Membership.bolao_id).where(Membership.user_id == current_user.id)
    ).all()

    # Filtra por nome (LIKE case-insensitive funciona em SQLite e Postgres)
    stmt = (
        select(Bolao, func.count(Membership.id).label("count"))
        .join(Membership, Membership.bolao_id == Bolao.id, isouter=True)
        .where(func.lower(Bolao.name).contains(term.lower()))
        .group_by(Bolao.id)
        .order_by(func.count(Membership.id).desc())
        .limit(30)
    )
    rows = db.exec(stmt).all()

    # IDs de requests pendentes do user
    pending_ids = set(db.exec(
        select(JoinRequest.bolao_id).where(
            JoinRequest.user_id == current_user.id,
            JoinRequest.status == "pending",
        )
    ).all())

    results = []
    for bolao, count in rows:
        if bolao.id in member_bolao_ids:
            continue  # já participa
        results.append(BolaoSearchResult(
            id=bolao.id,
            name=bolao.name,
            visibility=bolao.visibility,
            members_count=count,
            has_pending_request=(bolao.id in pending_ids),
            is_member=False,
        ))
    return results


# ----------------------------------------------------------------------------
# GET /boloes/preview/{invite_code} — preview pra fluxo de convite por link
#
# IMPORTANTE: declarado ANTES de /{bolao_id} pra evitar colisão de path.
# FastAPI resolve rotas em ordem; se /{bolao_id:int} viesse primeiro, o path
# /preview/COPA-X tentaria validar "preview" como int e cairia em 422.
# ----------------------------------------------------------------------------
@router.get("/preview/{invite_code}", response_model=BolaoPreview)
def preview_bolao_by_invite(
    invite_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retorna preview minimalista de um bolão a partir do invite_code.
    Usado pelo frontend quando o user clica num link de convite (?invite=COPA-XXX),
    pra mostrar "Você foi convidado para entrar em <nome>" antes de pedir codinome.

    Decisão de design:
      - Auth obrigatório → quem nem se cadastrou ainda passa pelo /login antes.
      - Não vaza owner_id ou outros dados sensíveis.
      - 404 se código inválido (não confirma existência de outros bolões).
    """
    code = invite_code.strip().upper()
    bolao = db.exec(select(Bolao).where(Bolao.invite_code == code)).first()
    if not bolao:
        raise HTTPException(404, "Convite inválido ou expirado.")

    members_count = db.exec(
        select(func.count(Membership.id)).where(Membership.bolao_id == bolao.id)
    ).one()

    is_member = db.exec(
        select(Membership).where(
            Membership.user_id == current_user.id,
            Membership.bolao_id == bolao.id,
        )
    ).first() is not None

    return BolaoPreview(
        id=bolao.id,
        name=bolao.name,
        visibility=bolao.visibility,
        members_count=members_count,
        is_member=is_member,
    )


# ----------------------------------------------------------------------------
# GET /boloes/{id} — detalhes
# ----------------------------------------------------------------------------
@router.get("/{bolao_id}", response_model=BolaoRead)
def get_bolao_details(
    bolao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retorna o bolão se o user for membro. 403 caso contrário."""
    # valida membership (raise 403)
    get_membership_or_403(bolao_id, current_user, db)

    bolao = db.get(Bolao, bolao_id)
    if not bolao:
        raise HTTPException(404, "Bolão não encontrado.")

    count = db.exec(
        select(func.count(Membership.id)).where(Membership.bolao_id == bolao_id)
    ).one()
    return _bolao_to_read(bolao, count)


@router.patch("/{bolao_id}/codinome")
def update_my_codinome(
    bolao_id: int,
    payload: CodinomeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Muda o codinome do usuário num bolão específico, desde que a Copa não tenha começado."""
    if has_tournament_started():
        raise HTTPException(400, "A competição já começou. Não é mais possível alterar o codinome.")
        
    codinome = payload.new_codinome.strip()
    if not codinome or len(codinome) > 40:
        raise HTTPException(400, "Codinome inválido.")

    membership = db.exec(
        select(Membership).where(
            Membership.user_id == current_user.id,
            Membership.bolao_id == bolao_id
        )
    ).first()

    if not membership:
        raise HTTPException(404, "Você não participa deste bolão.")

    membership.codinome = codinome
    db.commit()
    return {"status": "ok", "new_codinome": codinome}

# ----------------------------------------------------------------------------
# DELETE /boloes/{id} — deletar bolão
# 
# Restrição: só o owner pode deletar, e apenas se a competição não começou.
# Regra: Se qualquer fase está locked (is_locked=True), impossível deletar.
# ----------------------------------------------------------------------------
@router.delete("/{bolao_id}", status_code=204)
def delete_bolao(
    bolao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Deleta um bolão.
    
    Restrições:
      - User deve ser o OWNER
      - Nenhuma fase pode ter começado (is_locked=False pra todas)
    """
    bolao = db.get(Bolao, bolao_id)
    if not bolao:
        raise HTTPException(404, "Bolão não encontrado.")

    # Validação 1: User é o owner?
    if bolao.owner_id != current_user.id:
        raise HTTPException(403, "Apenas o criador pode deletar o bolão.")

    # Validação 2: Competição já começou?
    if has_tournament_started():
        raise HTTPException(400, "Não é possível deletar um bolão após o início da competição.")

    # Delete em cascata:
    # 1. JoinRequests (sem relacionamento explícito, deletar manualmente)
    db.exec(select(JoinRequest).where(JoinRequest.bolao_id == bolao_id))
    for req in db.exec(select(JoinRequest).where(JoinRequest.bolao_id == bolao_id)).all():
        db.delete(req)
    
    # 2. Memberships → Guesses (cascata automática via sa_relationship_kwargs)
    # 3. Bolao (cascata automática via sa_relationship_kwargs)
    db.delete(bolao)
    db.commit()


# ----------------------------------------------------------------------------
# POST /boloes/join — entrar
# ----------------------------------------------------------------------------
@router.post("/join", response_model=BolaoRead)
def join_bolao(
    payload: BolaoJoin,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Entra em bolão via invite_code. Aqui o usuário define o codinome que vai
    aparecer no ranking desse bolão específico (pode ser diferente do nome real
    ou do codinome usado em outros bolões).
    """
    code = payload.invite_code.strip().upper()
    bolao = db.exec(select(Bolao).where(Bolao.invite_code == code)).first()
    if not bolao:
        raise HTTPException(404, "Código de convite inválido.")

    # Já é membro?
    existing = db.exec(
        select(Membership).where(
            Membership.user_id == current_user.id,
            Membership.bolao_id == bolao.id,
        )
    ).first()
    if existing:
        raise HTTPException(400, "Você já é membro desse bolão.")

    # Validação do codinome
    codinome = payload.codinome.strip()
    if not codinome or len(codinome) > 40:
        raise HTTPException(400, "Codinome deve ter entre 1 e 40 caracteres.")

    membership = Membership(
        user_id=current_user.id,
        bolao_id=bolao.id,
        codinome=codinome,
    )
    db.add(membership)
    db.commit()

    count = db.exec(
        select(func.count(Membership.id)).where(Membership.bolao_id == bolao.id)
    ).one()
    return _bolao_to_read(bolao, count)





# ----------------------------------------------------------------------------
# GET /boloes/{id}/ranking — tabela de classificação
# ----------------------------------------------------------------------------
@router.get("/{bolao_id}/ranking", response_model=list[RankingRow])
def ranking(
    bolao_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ranking do bolão. Revela o nome real apenas para o dono."""
    get_membership_or_403(bolao_id, current_user, db)

    # 1. Descobre se quem está acessando é o dono do bolão
    bolao = db.get(Bolao, bolao_id)
    is_owner = (bolao.owner_id == current_user.id)

    # 2. Fazemos o JOIN com a tabela User para buscar o nome real (full_name)
    stmt = (
        select(
            Membership.id,
            Membership.codinome,
            User.full_name,  # <--- Pegando o nome real no banco
            func.coalesce(func.sum(Guess.points), 0).label("total_points"),
            func.count(Guess.id).label("guesses_count"),
        )
        .join(User, User.id == Membership.user_id) # Ligação Membership -> User
        .join(Guess, Guess.membership_id == Membership.id, isouter=True)
        .where(Membership.bolao_id == bolao_id)
        .group_by(Membership.id, Membership.codinome, User.full_name)
        .order_by(func.coalesce(func.sum(Guess.points), 0).desc())
    )
    rows = db.exec(stmt).all()

    # 3. Monta a resposta. A regra de privacidade entra no "real_name"
    return [
        RankingRow(
            membership_id=row[0],
            codinome=row[1],
            real_name=row[2] if is_owner else None, # Revela se for dono, esconde (None) se for membro
            total_points=int(row[3] or 0),
            guesses_count=int(row[4] or 0),
            position=i + 1,
        )
        for i, row in enumerate(rows)
    ]


# ============================================================================
# POST /boloes/{id}/join-requests — solicitar entrada
# ============================================================================
@router.post("/{bolao_id}/join-requests", response_model=JoinRequestRead, status_code=201)
def create_join_request(
    bolao_id: int,
    payload: JoinRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cria solicitação de entrada.

    Regras:
      - Bolão precisa existir
      - User não pode já ser membro
      - Se já tiver request 'pending' → 400
      - Se for bolão PÚBLICO → aceita automaticamente (vira Membership na hora)
      - Se for PRIVADO → cria request pending + notifica owner
    """
    bolao = db.get(Bolao, bolao_id)
    if not bolao:
        raise HTTPException(404, "Bolão não encontrado.")

    # Já é membro?
    existing_m = db.exec(
        select(Membership).where(
            Membership.user_id == current_user.id,
            Membership.bolao_id == bolao_id,
        )
    ).first()
    if existing_m:
        raise HTTPException(400, "Você já é membro desse bolão.")

    # Validação codinome
    codinome = payload.codinome.strip()
    if not codinome or len(codinome) > 40:
        raise HTTPException(400, "Codinome deve ter entre 1 e 40 caracteres.")

    # ─── BOLÃO PÚBLICO: fast-path, vira membership direto ───
    if bolao.visibility == "public":
        membership = Membership(
            user_id=current_user.id,
            bolao_id=bolao_id,
            codinome=codinome,
        )
        db.add(membership)
        # Cria também uma JoinRequest "accepted" pra rastreamento
        req = JoinRequest(
            user_id=current_user.id, bolao_id=bolao_id,
            codinome=codinome, message=payload.message,
            status="accepted", responded_at=datetime.utcnow(),
        )
        db.add(req)
        db.commit()
        db.refresh(req)
        return JoinRequestRead(
            id=req.id, bolao_id=bolao_id, bolao_name=bolao.name,
            user_id=current_user.id, user_name=current_user.full_name,
            codinome=codinome, message=payload.message, status="accepted",
            created_at=req.created_at, responded_at=req.responded_at,
        )

    # ─── BOLÃO PRIVADO: pending + notifica owner ───
    pending = db.exec(
        select(JoinRequest).where(
            JoinRequest.user_id == current_user.id,
            JoinRequest.bolao_id == bolao_id,
            JoinRequest.status == "pending",
        )
    ).first()
    if pending:
        raise HTTPException(400, "Você já tem uma solicitação pendente para este bolão.")

    req = JoinRequest(
        user_id=current_user.id,
        bolao_id=bolao_id,
        codinome=codinome,
        message=payload.message[:200],
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    # Notificar o owner
    notif_svc.notify_join_request(
        db,
        owner_id=bolao.owner_id,
        requester_name=current_user.full_name,
        bolao_id=bolao_id,
        bolao_name=bolao.name,
        join_request_id=req.id,
    )
    db.commit()

    return JoinRequestRead(
        id=req.id, bolao_id=bolao_id, bolao_name=bolao.name,
        user_id=current_user.id, user_name=current_user.full_name,
        codinome=codinome, message=payload.message, status="pending",
        created_at=req.created_at, responded_at=None,
    )


# ============================================================================
# GET /boloes/join-requests/incoming — pending que CHEGAM pra mim (owner)
# ============================================================================
@router.get("/join-requests/incoming", response_model=list[JoinRequestRead])
def list_incoming_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Todas as solicitações pending dirigidas a bolões que sou owner."""
    stmt = (
        select(JoinRequest, Bolao, User)
        .join(Bolao, Bolao.id == JoinRequest.bolao_id)
        .join(User, User.id == JoinRequest.user_id)
        .where(
            Bolao.owner_id == current_user.id,
            JoinRequest.status == "pending",
        )
        .order_by(JoinRequest.created_at.desc())
    )
    rows = db.exec(stmt).all()
    return [
        JoinRequestRead(
            id=req.id, bolao_id=bolao.id, bolao_name=bolao.name,
            user_id=user.id, user_name=user.full_name,
            codinome=req.codinome, message=req.message,
            status=req.status, created_at=req.created_at,
            responded_at=req.responded_at,
        )
        for req, bolao, user in rows
    ]


# ============================================================================
# POST /boloes/join-requests/{id}/respond — owner aceita ou rejeita
# ============================================================================
@router.post("/join-requests/{req_id}/respond")
def respond_join_request(
    req_id: int,
    payload: JoinRequestRespond,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Owner aceita ou rejeita uma solicitação.
    Se aceito: cria Membership e notifica solicitante.
    Se rejeitado: só muda status e notifica.
    """
    if payload.action not in ("accept", "reject"):
        raise HTTPException(400, "action deve ser 'accept' ou 'reject'.")

    req = db.get(JoinRequest, req_id)
    if not req:
        raise HTTPException(404, "Solicitação não encontrada.")
    if req.status != "pending":
        raise HTTPException(400, "Esta solicitação já foi respondida.")

    bolao = db.get(Bolao, req.bolao_id)
    if not bolao or bolao.owner_id != current_user.id:
        raise HTTPException(403, "Você não é dono deste bolão.")

    req.responded_at = datetime.utcnow()

    if payload.action == "accept":
        # Cria Membership
        membership = Membership(
            user_id=req.user_id,
            bolao_id=req.bolao_id,
            codinome=req.codinome,
        )
        db.add(membership)
        req.status = "accepted"
        notif_svc.notify_request_accepted(
            db, user_id=req.user_id, bolao_id=bolao.id, bolao_name=bolao.name,
        )
    else:
        req.status = "rejected"
        notif_svc.notify_request_rejected(
            db, user_id=req.user_id, bolao_name=bolao.name,
        )

    db.commit()
    return {"status": "ok", "action": payload.action, "request_id": req_id}


@router.patch("/{bolao_id}", response_model=BolaoRead)
def edit_bolao(
    bolao_id: int, 
    payload: schemas.BolaoUpdate, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """Edita os detalhes do bolão (apenas dono)."""
    bolao = db.get(Bolao, bolao_id)
    if not bolao:
        raise HTTPException(status_code=404, detail="Bolão não encontrado.")
        
    if bolao.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Apenas o criador pode editar este bolão.")
    
    if payload.name is not None:
        bolao.name = payload.name
    if payload.description is not None:
        bolao.description = payload.description
        
    db.add(bolao)
    db.commit()
    db.refresh(bolao)
    
    # Retorna o formato correto (BolaoRead) com a contagem de membros
    count = db.exec(select(func.count(Membership.id)).where(Membership.bolao_id == bolao_id)).one()
    return _bolao_to_read(bolao, count)