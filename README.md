# Bolão da Copa 2026 — Projeto Completo

Stack: **FastAPI** + **SQLModel** + **SQLite** (dev) / **Postgres** (prod) + JWT.
Fonte de dados dos jogos: [openfootball/worldcup.json](https://github.com/openfootball/worldcup.json).

---

## 📁 Estrutura do projeto

```
copa-bolao-app/
├── main.py                      # App FastAPI + inclui routers + lifespan
├── database.py                  # Engine dual-driver + get_db()
├── models.py                    # 5 tabelas (User, Bolao, Membership, Guess, MatchResult)
├── schemas.py                   # Schemas Pydantic input/output
├── auth.py                      # hash bcrypt, JWT, get_current_user, require_admin
├── scoring.py                   # Regra de pontuação (10/7/5/5/0)
├── routers/
│   ├── __init__.py
│   ├── auth_router.py           # /auth/signup, /auth/login, /auth/me
│   ├── boloes_router.py         # /boloes CRUD + /join + /ranking
│   ├── guesses_router.py        # /boloes/{id}/guesses (palpites)
│   └── admin_router.py          # /admin/matches/{id}/result
├── services/
│   └── matches.py               # Fetch openfootball + cache + BRT
├── templates/
│   ├── login.html               # Login/Cadastro (auth real integrada)
│   └── app.html                 # Dashboard (tabs + auth guard)
├── static/                      # logo, bandeira, vídeo
├── requirements.txt
├── .env.example                 # Template de variáveis de ambiente
└── .gitignore
```

---

## 🚀 Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Acesse **http://localhost:8000**. Swagger UI em **http://localhost:8000/docs**.

### Variáveis de ambiente (opcional em dev)

Copia `.env.example` → `.env` e ajusta. Importante em produção:

```bash
SECRET_KEY=<gera com: python -c "import secrets; print(secrets.token_urlsafe(32))">
ADMIN_TOKEN=<outro valor aleatório>
DATABASE_URL=postgresql://user:pass@host:5432/dbname   # só em prod
```

---
