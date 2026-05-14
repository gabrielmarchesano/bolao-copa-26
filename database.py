"""
database.py — configuração do banco de dados.

CONCEITO: "Dual-driver" — o mesmo código roda em SQLite (dev) e Postgres (prod).
Isso é possível porque o SQLModel (baseado em SQLAlchemy) abstrai o driver:
você escreve `db.add(user)` e ele traduz pro dialeto correto do banco.

Fluxo:
1. DATABASE_URL é lida de variável de ambiente
2. Se não tiver env var → usa SQLite local (arquivo ./bolao.db)
3. Se tiver postgres://... → usa Postgres (via psycopg2-binary)

COMO TROCAR DE BANCO EM PRODUÇÃO:
  export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
  (Railway/Render fazem isso automaticamente ao provisionar o Postgres)
"""
from __future__ import annotations

import os
from typing import Generator

from sqlmodel import SQLModel, Session, create_engine

# ----------------------------------------------------------------------------
# Connection string
# ----------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bolao.db")

# Railway/Heroku legado entregam "postgres://" mas SQLAlchemy 2.x exige
# "postgresql://". Correção automática para não quebrar em produção.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------
# SQLite precisa de flag especial pra funcionar com FastAPI (que usa threads).
# Em Postgres essa flag não é usada.
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# echo=True imprime todas as queries SQL no console. Útil pra aprender/debugar.
# Em produção, sempre echo=False.
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
)


# ----------------------------------------------------------------------------
# Inicialização (criar tabelas)
# ----------------------------------------------------------------------------
def init_db() -> None:
    """
    Cria todas as tabelas definidas nos models.
    Chamado no startup do FastAPI (ver main.py → lifespan).

    IMPORTANTE: o import de `models` aqui dentro é intencional.
    Se importarmos no topo do arquivo, criamos um ciclo: database.py importa
    models.py, que importa database.py pra usar o Session. Import lazy resolve.
    """
    # noqa: F401 — imports só pra SQLModel descobrir as classes com table=True
    from models import User, Bolao, Membership, Guess, MatchResult, JoinRequest, Notification  # noqa: F401

    SQLModel.metadata.create_all(engine)


# ----------------------------------------------------------------------------
# Dependency (injeção no FastAPI)
# ----------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """
    Dependency do FastAPI. Uso:

        @app.get("/users")
        def list_users(db: Session = Depends(get_db)):
            return db.exec(select(User)).all()

    O `yield` entrega a sessão pro endpoint; quando o endpoint termina,
    o `with` fecha a sessão automaticamente (commit ou rollback).
    """
    with Session(engine) as session:
        yield session
