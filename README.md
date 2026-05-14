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

## 🎯 Endpoints completos

### Páginas
| Método | Rota     | Descrição                      |
|--------|----------|--------------------------------|
| GET    | `/`      | Login/Cadastro                 |
| GET    | `/app`   | Dashboard (requer token)       |

### Autenticação
| Método | Rota           | Body                                        |
|--------|----------------|---------------------------------------------|
| POST   | `/auth/signup` | `{email, password, full_name}`              |
| POST   | `/auth/login`  | form-urlencoded `username` + `password`     |
| GET    | `/auth/me`     | —  (requer Bearer token)                    |

### Bolões 🔐
| Método | Rota                         | Descrição                                    |
|--------|------------------------------|----------------------------------------------|
| POST   | `/boloes`                    | Cria bolão (owner vira primeiro membro)      |
| GET    | `/boloes/me`                 | Meus bolões                                  |
| GET    | `/boloes/{id}`               | Detalhes (só membros)                        |
| POST   | `/boloes/join`               | Body: `{invite_code, codinome}`              |
| GET    | `/boloes/{id}/ranking`       | Ranking ordenado por pontos                  |

### Palpites 🔐
| Método | Rota                              | Descrição                               |
|--------|-----------------------------------|-----------------------------------------|
| GET    | `/boloes/{id}/guesses`            | Meus palpites nesse bolão               |
| POST   | `/boloes/{id}/guesses`            | Upsert — `{match_id, score1, score2}`   |

### Matches (públicos, sem auth)
| Método | Rota                              |
|--------|-----------------------------------|
| GET    | `/api/matches/upcoming?limit=N`   |
| GET    | `/api/matches/all`                |
| GET    | `/api/matches/group/{A..L}`       |
| GET    | `/api/matches/date/{YYYY-MM-DD}`  |

### Admin 🔑 (header `X-Admin-Token`)
| Método | Rota                                 | Body                    |
|--------|--------------------------------------|-------------------------|
| POST   | `/admin/matches/{match_id}/result`   | `{score1, score2}`      |

---

## 🧪 Fluxo de teste end-to-end

### 1. Cadastro + Login (via Swagger UI `/docs`)

No Swagger, expanda `POST /auth/signup` e manda:
```json
{"email": "raf@bolao.com", "password": "senha123", "full_name": "Raf Valdivia"}
```

Depois `POST /auth/login` (form): `username=raf@bolao.com`, `password=senha123`.
Copia o `access_token` retornado.

No topo do Swagger, clica em **Authorize** 🔓 e cola: `Bearer <seu_token>` (ou só o token, o Swagger formata sozinho).

### 2. Criar bolão

`POST /boloes`:
```json
{"name": "Bolão da Firma", "visibility": "private", "stake": 50.0}
```

Resposta traz o `invite_code` (ex: `COPA-A2X9K7`). Guarda.

### 3. Entrar em bolão (com outro user)

Cria outro user, loga com ele, e chama `POST /boloes/join`:
```json
{"invite_code": "COPA-A2X9K7", "codinome": "Pelé"}
```

### 4. Palpitar

Olha `GET /api/matches/upcoming` pra pegar um `match_id`. Depois:

`POST /boloes/1/guesses`:
```json
{"match_id": 6, "score1": 2, "score2": 1}
```

### 5. Registrar resultado (admin)

`POST /admin/matches/6/result` com header `X-Admin-Token: admin-dev-token-troque-isso`:
```json
{"score1": 2, "score2": 1}
```

Resposta: `{"status": "ok", "guesses_updated": 47}` — todos os palpites desse jogo tiveram pontos recalculados.

### 6. Ver ranking

`GET /boloes/1/ranking` → lista ordenada por `total_points` desc.

---

## 📚 Guia de estudo

Sugestão de ordem pra entender o código (lê arquivo por arquivo):

1. **`database.py`** — como o engine dual-driver funciona, dependency injection
2. **`models.py`** — tabelas e relações SQLModel; entende por que `Membership` é separado
3. **`schemas.py`** — por que separar model de schema (segurança: não vazar password_hash)
4. **`auth.py`** — hash bcrypt + JWT + dependency `get_current_user`
5. **`routers/auth_router.py`** — signup/login com comentários explicando OAuth2 Password Flow
6. **`routers/boloes_router.py`** — queries agregadas (`func.count`, `func.sum`, GROUP BY)
7. **`routers/guesses_router.py`** — lógica de lock temporal (regra de negócio)
8. **`scoring.py`** — função pura, fácil de testar, isolada
9. **`routers/admin_router.py`** — transação atômica (upsert + recálculo no mesmo commit)
10. **`main.py`** — como tudo se junta via `include_router` e `lifespan`

---

## 🔮 Próximos passos sugeridos

### Features faltantes na UI
- Tela de palpite ao clicar num jogo do Calendário (modal com score1/score2)
- Tab Simulador (bracket interativo do mata-mata)
- Edição de perfil e codinome por bolão
- Convidar amigo via link único (compartilhamento WhatsApp)

### Backend
- Tabela `MatchResult` sendo populada via job automático (cron + API-Football)
- Webhook pra notificar palpites fechados
- Rate limiting nos endpoints públicos

### Deploy
- Railway com Postgres gerenciado (grátis em hobby tier)
- Configurar env vars: `SECRET_KEY`, `ADMIN_TOKEN`, `DATABASE_URL`
- HTTPS automático pelo Railway

### Segurança
- Rotacionar `SECRET_KEY` periodicamente
- Refresh tokens (hoje só tem access token de 24h)
- Rate limit no login (anti-brute-force)
- Migrar `ADMIN_TOKEN` pra campo `User.is_admin`
