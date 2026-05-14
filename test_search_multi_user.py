import requests
import json

BASE_URL = "http://localhost:8000"

# Criar 2 usuários
print("1. Criando primeiro usuário...")
r = requests.post(f"{BASE_URL}/auth/signup", json={
    "email": "user1@example.com",
    "password": "test123456",
    "full_name": "User One"
})
print(f"   Status: {r.status_code}")

print("2. Criando segundo usuário...")
r = requests.post(f"{BASE_URL}/auth/signup", json={
    "email": "user2@example.com",
    "password": "test123456",
    "full_name": "User Two"
})
print(f"   Status: {r.status_code}")

# User 1 fazendo login
print("3. User 1 fazendo login...")
r = requests.post(f"{BASE_URL}/auth/login", data={
    "username": "user1@example.com",
    "password": "test123456"
})
token1 = r.json()["access_token"]
headers1 = {"Authorization": f"Bearer {token1}"}

# User 1 criando bolão
print("4. User 1 criando bolão...")
r = requests.post(f"{BASE_URL}/boloes", json={
    "name": "Copa 2026 Brasil",
    "visibility": "public"
}, headers=headers1)
print(f"   Status: {r.status_code}")

# User 2 fazendo login
print("5. User 2 fazendo login...")
r = requests.post(f"{BASE_URL}/auth/login", data={
    "username": "user2@example.com",
    "password": "test123456"
})
token2 = r.json()["access_token"]
headers2 = {"Authorization": f"Bearer {token2}"}

# User 2 buscando bolões
print("6. User 2 buscando bolões com 'Copa'...")
r = requests.get(f"{BASE_URL}/boloes/search?q=Copa", headers=headers2)
print(f"   Status: {r.status_code}")
if r.status_code == 200:
    results = r.json()
    print(f"   Encontrados: {len(results)} bolão(ões)")
    if results:
        print(json.dumps(results, indent=2, ensure_ascii=False))
else:
    print(f"   Erro: {r.text}")
