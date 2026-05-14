"""
services/notifications.py — criação de notificações.

CONCEITO: todo lugar que precisa gerar notificação chama funções daqui em vez
de fazer db.add(Notification(...)) inline. Centraliza o "como" se formata cada
tipo (título, body, action_url) num lugar só — fácil de mudar copy depois.

Padrão de tipos (constantes pra evitar typo em strings soltas):
  TYPE_JOIN_REQUEST          - owner recebe quando alguém solicita
  TYPE_REQUEST_ACCEPTED      - solicitante recebe ao ser aceito
  TYPE_REQUEST_REJECTED      - solicitante recebe ao ser rejeitado
  TYPE_PHASE_LOCK_SOON       - broadcast pra todos os membros
"""
import json
from sqlmodel import Session

from models import Notification

TYPE_JOIN_REQUEST = "join_request"
TYPE_REQUEST_ACCEPTED = "request_accepted"
TYPE_REQUEST_REJECTED = "request_rejected"
TYPE_PHASE_LOCK_SOON = "phase_lock_soon"


def _create(
    db: Session,
    user_id: int,
    type_: str,
    title: str,
    body: str,
    data: dict = None,
    action_url: str = None,
) -> Notification:
    """Helper genérico. Não commita — deixa a sessão do caller decidir."""
    notif = Notification(
        user_id=user_id,
        type=type_,
        title=title,
        body=body,
        data=json.dumps(data or {}),
        action_url=action_url,
    )
    db.add(notif)
    return notif


def notify_join_request(
    db: Session,
    owner_id: int,
    requester_name: str,
    bolao_id: int,
    bolao_name: str,
    join_request_id: int,
) -> Notification:
    """Owner recebe: 'João solicitou entrar em Bolão da Firma'."""
    return _create(
        db,
        user_id=owner_id,
        type_=TYPE_JOIN_REQUEST,
        title="Nova solicitação de entrada",
        body=f"{requester_name} quer entrar em \"{bolao_name}\".",
        data={
            "join_request_id": join_request_id,
            "bolao_id": bolao_id,
            "bolao_name": bolao_name,
        },
        action_url=f"/app#torneios?request={join_request_id}",
    )


def notify_request_accepted(
    db: Session,
    user_id: int,
    bolao_id: int,
    bolao_name: str,
) -> Notification:
    return _create(
        db,
        user_id=user_id,
        type_=TYPE_REQUEST_ACCEPTED,
        title="Solicitação aceita 🎉",
        body=f"Você agora faz parte de \"{bolao_name}\".",
        data={"bolao_id": bolao_id, "bolao_name": bolao_name},
        action_url=f"/app#torneios?bolao={bolao_id}",
    )


def notify_request_rejected(
    db: Session,
    user_id: int,
    bolao_name: str,
) -> Notification:
    return _create(
        db,
        user_id=user_id,
        type_=TYPE_REQUEST_REJECTED,
        title="Solicitação não aprovada",
        body=f"Sua solicitação para \"{bolao_name}\" não foi aceita desta vez.",
        data={"bolao_name": bolao_name},
    )


def notify_phase_lock_soon(
    db: Session,
    user_id: int,
    bolao_id: int,
    bolao_name: str,
    phase_label: str,
    hours_remaining: int,
) -> Notification:
    return _create(
        db,
        user_id=user_id,
        type_=TYPE_PHASE_LOCK_SOON,
        title=f"⏰ Palpites fechando: {phase_label}",
        body=f"Em {hours_remaining}h os palpites de {phase_label} do bolão "
             f"\"{bolao_name}\" serão bloqueados.",
        data={
            "bolao_id": bolao_id,
            "bolao_name": bolao_name,
            "phase_label": phase_label,
            "hours_remaining": hours_remaining,
        },
        action_url=f"/app#torneios?bolao={bolao_id}&guess=1",
    )