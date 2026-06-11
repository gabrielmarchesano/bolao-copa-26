"""
routers/auth_router.py — endpoints de autenticação.

ROTAS:
  POST /auth/signup  → cria usuário
  POST /auth/login   → retorna JWT
  GET  /auth/me      → retorna dados do user logado
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select
from jose import jwt, JWTError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from auth import create_access_token, get_current_user, hash_password, verify_password, SECRET_KEY, ALGORITHM
from pydantic import BaseModel
from database import get_db
from models import User
from schemas import Token, UserCreate, UserRead, ForgotPasswordRequest, ResetPasswordRequest
import os
# APIRouter é como um "mini-app" que depois é plugado no FastAPI principal.
# O `prefix` evita repetir "/auth" em cada rota. `tags` agrupa no Swagger UI.
router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "601426753056-4oadkgpe5kt9iglcbqnbsa2ret0bt9po.apps.googleusercontent.com")

class GoogleLoginPayload(BaseModel):
    credential: str

@router.post("/google")
def login_with_google(payload: GoogleLoginPayload, db: Session = Depends(get_db)):
    """Recebe o token do frontend, valida no Google e gera o token do nosso app."""
    try:
        # Pede pro Google validar se o token não foi forjado
        idinfo = id_token.verify_oauth2_token(
            payload.credential, 
            google_requests.Request(), 
            GOOGLE_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Token do Google inválido ou expirado.")

    email = idinfo.get("email")
    nome_completo = idinfo.get("name", "Usuário")

    if not email:
        raise HTTPException(status_code=400, detail="O e-mail do Google não foi fornecido.")

    # 1. Verifica se o usuário já existe no nosso banco de dados
    user = db.exec(select(User).where(User.email == email)).first()

    # 2. Se não existir, fazemos o "cadastro invisível" na hora
    if not user:
        user = User(
            email=email,
            full_name=nome_completo,
            password_hash="google_sso" # Não tem senha, ele loga pelo Google
        )
        db.add(user)
        db.commit()
        db.refresh(user)
 
    # 3. Gera o JWT do NOSSO sistema para a sessão dele
    access_token = create_access_token(data={"sub": user.email})

    return {
        "access_token": access_token, 
        "token_type": "bearer", 
        "user_id": user.id,
        "name": user.full_name
    }




# ----------------------------------------------------------------------------
# SIGNUP
# ----------------------------------------------------------------------------
@router.post(
    "/signup",
    response_model=UserRead,   # filtra o retorno pra não vazar password_hash
    status_code=201,           # 201 = Created (convenção REST)
)
def signup(payload: UserCreate, db: Session = Depends(get_db)):
    """
    Cria um usuário novo.

    FLUXO:
      1. Verifica se email já existe → 400
      2. Gera hash bcrypt da senha
      3. Persiste no banco
      4. Retorna UserRead (sem senha!)

    O FastAPI converte o JSON do body em UserCreate automaticamente.
    Se faltar campo ou tipo errado → retorna 422 sem chegar aqui.
    """
    # Verifica duplicata
    existing = db.exec(select(User).where(User.email == payload.email and User.password_hash != "google_sso")).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email já cadastrado.")

    # Cria o objeto ORM
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),  # nunca guarda plano!
    )
    db.add(user)
    db.commit()        # persiste no banco
    db.refresh(user)   # recarrega pra pegar o id auto-gerado
    return user


# ----------------------------------------------------------------------------
# LOGIN
# ----------------------------------------------------------------------------
@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    Login via OAuth2 Password Flow (padrão da indústria).

    DETALHE IMPORTANTE: o body NÃO é JSON, é `application/x-www-form-urlencoded`
    com campos `username` e `password`. Usamos `username` como email (convenção).

    O frontend envia assim:
        const formData = new URLSearchParams();
        formData.append('username', email);
        formData.append('password', password);
        fetch('/auth/login', { method: 'POST', body: formData });

    Isso é o que o Swagger UI usa no botão "Authorize" — compatibilidade out-of-box.
    """
    user = db.exec(select(User).where(User.email == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        # Mesma mensagem pra email errado OU senha errada → evita enumerar users
        raise HTTPException(status_code=401, detail="Email ou senha inválidos.")

    token = create_access_token(user.id)
    return Token(access_token=token)


# ----------------------------------------------------------------------------
# ME — quem sou eu?
# ----------------------------------------------------------------------------
@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)):
    """
    Retorna os dados do usuário logado.
    Usado pelo frontend pra (1) validar que o token ainda é válido e
    (2) popular o nome do user na UI.
    """
    return current_user

@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    user = db.exec(select(User).where(User.email == payload.email)).first()
    
    if user and user.password_hash != "google_sso":
        reset_token = create_access_token(
            data={"sub": user.email, "type": "reset"}, 
            expires_delta=timedelta(minutes=15)
        )
        
        # ⚠️ Quando for pro Render, mude localhost para https://seusite.onrender.com
        base_url = "https://bolaocopa26-9swz.onrender.com" 
        reset_link = f"{base_url}/reset.html?token={reset_token}"
        
        # 🟢 Substitui o print antigo por esta linha mágica:
        background_tasks.add_task(send_reset_email, user.email, reset_link)

    return {"status": "ok", "message": "Se o e-mail existir, enviaremos as instruções."}

@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Recebe o token e a nova senha, valida e salva no banco."""
    try:
        # Decodifica o token para ver se é válido e não expirou
        payload_token = jwt.decode(payload.token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload_token.get("sub")
        token_type: str = payload_token.get("type")
        
        if email is None or token_type != "reset":
            raise HTTPException(status_code=400, detail="Token inválido para esta operação.")
            
    except JWTError:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")

    user = db.exec(select(User).where(User.email == email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")

    # Atualiza a senha no banco
    user.password_hash = hash_password(payload.new_password)
    db.add(user)
    db.commit()
    
    return {"status": "ok", "message": "Senha atualizada com sucesso!"}


def send_reset_email(to_email: str, reset_link: str):
    # Puxa o e-mail e a senha do ambiente (Render) ou usa um padrão para testes
    sender_email = os.getenv("SMTP_EMAIL", "gabrielfmarchesano@gmail.com")
    sender_password = os.getenv("SMTP_PASSWORD", "igbi jwiu waab uzdw")

    # Monta a estrutura do E-mail
    msg = MIMEMultipart()
    msg['From'] = f"Bolão da Copa 2026 <{sender_email}>"
    msg['To'] = to_email
    msg['Subject'] = "Recuperação de Senha - Bolão da Copa"

    # Corpo do E-mail
    body = f"""
    Olá!

    Você solicitou a redefinição de senha no Bolão da Copa 2026.
    Para criar uma nova senha, clique no link abaixo:

    {reset_link}

    Se você não solicitou isso, pode ignorar este e-mail tranquilamente.
    """
    msg.attach(MIMEText(body, 'plain'))

    try:
        # Conecta no servidor do Gmail e envia
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls() # Inicia conexão segura
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        print(f"✅ E-mail enviado com sucesso para {to_email}")
    except Exception as e:
        print(f"❌ Erro ao enviar e-mail: {e}")