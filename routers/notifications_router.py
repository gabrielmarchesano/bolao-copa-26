"""
routers/notifications_router.py — endpoints de notificações.

ROTAS:
  GET  /notifications             → lista + unread_count (polling-friendly)
  POST /notifications/{id}/read   → marca uma como lida
  POST /notifications/read-all    → marca todas como lidas
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, func, select

from auth import get_current_user
from database import get_db
from models import Notification, User
from schemas import NotificationRead, NotificationsEnvelope

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationsEnvelope)
def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    only_unread: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lista notificações do usuário, mais recentes primeiro.
    `unread_count` sempre reflete total não-lido (independe do filtro).
    """
    base_q = select(Notification).where(Notification.user_id == current_user.id)
    if only_unread:
        q = base_q.where(Notification.is_read == False)  # noqa: E712
    else:
        q = base_q
    q = q.order_by(Notification.created_at.desc()).limit(limit)

    rows = db.exec(q).all()
    unread_count = db.exec(
        select(func.count(Notification.id))
        .where(Notification.user_id == current_user.id, Notification.is_read == False)  # noqa: E712
    ).one()

    items = [
        NotificationRead(
            id=n.id, type=n.type, title=n.title, body=n.body,
            data=json.loads(n.data or "{}"),
            is_read=n.is_read, action_url=n.action_url, created_at=n.created_at,
        )
        for n in rows
    ]
    return NotificationsEnvelope(unread_count=unread_count, items=items)


@router.post("/{notif_id}/read")
def mark_read(
    notif_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = db.get(Notification, notif_id)
    if not n or n.user_id != current_user.id:
        raise HTTPException(404, "Notificação não encontrada.")
    if not n.is_read:
        n.is_read = True
        n.read_at = datetime.utcnow()
        db.commit()
    return {"status": "ok"}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    unread = db.exec(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        )
    ).all()
    now = datetime.utcnow()
    for n in unread:
        n.is_read = True
        n.read_at = now
    db.commit()
    return {"status": "ok", "marked": len(unread)}