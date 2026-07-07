# Bolão da Copa 2026 — Plataforma Full-Stack

Uma plataforma completa de palpites para a Copa do Mundo 2026. Desenvolvida com foco em performance, experiência mobile (Mobile First) e regras dinâmicas de fechamento de palpites.

**Stack Tecnológica:**
* **Backend:** Python, FastAPI, SQLModel (SQLAlchemy + Pydantic)
* **Banco de Dados:** SQLite (Desenvolvimento) / PostgreSQL hospedado no Neon (Produção)
* **Frontend:** HTML, CSS e Vanilla JavaScript (Fetch API)
* **Deploy:** Render
* **Fonte de Dados:** Integração com a API do [football-data.org](https://www.football-data.org/) com sistema de cache inteligente (Rate Limit Protector).

---

## Estrutura do Projeto

```text
copa-bolao-app/
├── main.py                      # App FastAPI + inclusão de routers + middlewares
├── database.py                  # Engine dual-driver (SQLite/Postgres) + get_db()
├── models.py                    # Tabelas: User, Bolao, Membership, Guess, MatchResult, ExtraGuess, etc.
├── schemas.py                   # Schemas Pydantic para input/output e validação
├── auth.py                      # Hash bcrypt, autenticação JWT, dependências de usuário e admin
├── scoring.py                   # Motor matemático de pontuação (pesos dinâmicos por fase e bônus)
├── routers/
│   ├── __init__.py
│   ├── admin_router.py          # /admin -> Override de resultados, controle de fases e recálculo
│   ├── auth_router.py           # /auth -> /signup, /login, /me
│   ├── boloes_router.py         # /boloes -> CRUD, convites, aprovação de membros e ranking
│   ├── guesses_router.py        # /boloes/{id}/guesses -> Lógica e travas de palpites
│   └── notifications_router.py  # /notifications -> Gestão de avisos em tempo real
├── services/
│   ├── __init__.py
│   ├── matches.py               # Fetch da API externa + Cache Dinâmico + Adapter
│   ├── notifications.py         # Regras de disparo de notificações
│   ├── phase_control.py         # Override manual de bloqueio/desbloqueio de fases
│   ├── phases.py                # Lógica de lock automático (minutos antes do kickoff)
│   └── ranking.py               # Geração de posições, setas de variação e empates
├── static/                      # Assets (logos, bandeiras, ícones)
├── templates/
│   ├── app.html                 # Dashboard Principal (Single Page Application via JS)
│   ├── login.html               # Tela de Login e Cadastro
│   └── reset.html               # Fluxo de recuperação de senha
├── .env.example                 # Template de variáveis de ambiente
├── .gitignore
├── README.md
└── requirements.txt             # Dependências do projeto

```

## Como rodar o projeto localmente

### 1. Preparando o ambiente
Clone o repositório e crie um ambiente virtual na raiz do projeto:
```bash
python -m venv .venv
Ative o ambiente virtual:

```
```bash
# No Linux/macOS
source .venv/bin/activate

# No Windows
.venv\Scripts\activate
Instale todas as dependências necessárias:

```
```bash
python -m pip install -r requirements.txt

```

### 2. Variáveis de Ambiente
O sistema precisa de algumas chaves para funcionar (como a comunicação com a API de futebol e a segurança do login).

Copie o arquivo de exemplo para criar o seu arquivo definitivo:


```bash
cp .env.example .env
Abra o arquivo .env gerado e preencha as suas credenciais:
```

``` snippet

SECRET_KEY=sua_chave_secreta_jwt
ADMIN_TOKEN=seu_token_de_admin_para_rotas_fechadas
API_TOKEN=seu_token_do_football_data
DATABASE_URL=postgresql://user:pass@host:5432/dbname  # Para testar localmente com SQLite, basta comentar ou apagar esta linha
```
### 3. Rodando o Servidor
Com tudo configurado e o ambiente ativado, inicie o servidor do FastAPI:


```bash
python -m uvicorn main:app --reload --port 8000
Feito isso, o sistema já estará rodando na sua máquina!
```
Acesse a plataforma: http://localhost:8000

Acesse a documentação automática da API (Swagger UI): http://localhost:8000/docs
