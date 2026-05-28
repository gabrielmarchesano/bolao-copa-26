"""
auth.py — autenticação e autorização.

CONCEITOS-CHAVE que você vai usar em qualquer API Python moderna:

1. HASH DE SENHA (bcrypt via passlib)
   Nunca guarde senha em texto plano. bcrypt é um algoritmo de hash "slow by design":
   ele leva ~100ms pra calcular, o que faz força bruta ser inviável.
   Cada hash tem salt único embutido — dois usuários com senha "123456"
   geram hashes completamente diferentes.

2. JWT (JSON Web Token)
   Uma string criptograficamente assinada que contém {user_id, expiration}.
   Cliente guarda no localStorage → envia em todo request no header
   `Authorization: Bearer <token>`. Servidor valida a assinatura e sabe quem é o user
   SEM precisar consultar o banco. Escala.

3. DEPENDENCY INJECTION
   `get_current_user` é um Depends() que você pluga em qualquer endpoint
   privado. O FastAPI automaticamente:
     - extrai o token do header Authorization
     - valida
     - busca o user no banco
     - injeta na função
   Se algum passo falha → retorna 401 antes mesmo do endpoint rodar.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import Session, select
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from database import get_db
from models import Membership, User

# ----------------------------------------------------------------------------
# Configurações (lidas do .env)
# ----------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-troque-em-producao-com-secrets-token_urlsafe")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
# Token fixo pra endpoints de admin (registrar resultado de jogo).
# Em produção: troca por uma role no User (is_admin) ou RBAC completo.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-dev-token-troque-isso")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "601426753056-4oadkgpe5kt9iglcbqnbsa2ret0bt9po.apps.googleusercontent.com")
# ----------------------------------------------------------------------------
# Hashing
# ----------------------------------------------------------------------------
# `CryptContext` gerencia múltiplos algoritmos. Se um dia mudar de bcrypt pra
# argon2, basta adicionar aqui — hashes antigos continuam funcionando.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Gera hash bcrypt (~60 chars). Inclui salt automático."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Compara senha plana com hash. Timing-safe (proteção contra timing attacks)."""
    return pwd_context.verify(plain, hashed)


# ----------------------------------------------------------------------------
# JWT
# ----------------------------------------------------------------------------
# Esquema OAuth2: o FastAPI usa isso pra (1) extrair o token do header
# Authorization e (2) renderizar o botão "Authorize" no Swagger UI.
# `tokenUrl` aponta pro endpoint de login — é só metadados pro Swagger.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def create_access_token(user_id: int) -> str:
    """
    Gera JWT assinado com SECRET_KEY.
    Payload minimalista: só user_id e expiração.
    Nunca coloque dados sensíveis (senha, CPF) no JWT — ele é só base64, qualquer
    um lê o conteúdo. A assinatura só garante que não foi modificado.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency que injeta o User logado nos endpoints. Use assim:

        @app.get("/me")
        def me(user: User = Depends(get_current_user)):
            return user

    Se o token for inválido/expirado/ausente → FastAPI retorna 401.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    user = db.get(User, user_id)
    if not user:
        raise credentials_exception
    return user


# ----------------------------------------------------------------------------
# Helper: exige que o user seja membro do bolão
# ----------------------------------------------------------------------------
def get_membership_or_403(
    bolao_id: int,
    current_user: User,
    db: Session,
) -> Membership:
    """
    Retorna o Membership do current_user no bolao_id, ou 403.
    Usado pra garantir que um user só veja/altere dados de bolões que participa.

    Note: NÃO é um Depends — é uma função helper chamada dentro dos endpoints.
    Se quisesse virar Depends, precisaria acessar bolao_id via Path, o que
    deixa a assinatura do endpoint mais poluída.
    """
    stmt = select(Membership).where(
        Membership.user_id == current_user.id,
        Membership.bolao_id == bolao_id,
    )
    membership = db.exec(stmt).first()
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="Você não é membro desse bolão.",
        )
    return membership


# ----------------------------------------------------------------------------
# Admin (token fixo via header) — MVP simples
# ----------------------------------------------------------------------------
def require_admin(x_admin_token: str = Header(...)):
    """
    Exige o header `X-Admin-Token: <ADMIN_TOKEN>`.

    Uso em endpoints:
        @router.post("/admin/...", dependencies=[Depends(require_admin)])

    MVP pragmático. Em produção: migrar pra campo `User.is_admin` + RBAC.
    """
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Admin token inválido.")
    return True
