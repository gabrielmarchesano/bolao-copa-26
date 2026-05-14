"""
Pacote de routers da API.

Separar em routers permite organizar endpoints por domínio:
  - auth_router    → /auth/signup, /auth/login, /auth/me
  - boloes_router  → /boloes/*
  - guesses_router → /boloes/{id}/guesses/*
  - admin_router   → /admin/*

Cada router é registrado no main.py via app.include_router().
"""
