import requests
import json

BASE_URL = "http://localhost:8000"

# 1. Registrar usuário
print("1. Registrando usuário...")
user_data = {
    "email": "test@example.com",
    "password": "test123456",
    "full_name": "Test User"
}
r = requests.post(f"{BASE_URL}/auth/signup", json=user_data)
print(f"Status: {r.status_code}")
if r.status_code != 201:
    print(f"Erro: {r.text}")
else:
    print("Usuário registrado com sucesso!")

# 2. Fazer login
print("\n2. Fazendo login...")
login_data = {
    "username": "test@example.com",
    "password": "test123456"
}
r = requests.post(f"{BASE_URL}/auth/login", data=login_data)
print(f"Status: {r.status_code}")
if r.status_code != 200:
    print(f"Erro: {r.text}")
else:
    token_data = r.json()
    token = token_data["access_token"]
    print(f"Token obtido: {token[:20]}...")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # 3. Criar bolão
    print("\n3. Criando bolão...")
    bolao_data = {
        "name": "Copa 2026 - Teste",
        "visibility": "public"
    }
    r = requests.post(f"{BASE_URL}/boloes", json=bolao_data, headers=headers)
    print(f"Status: {r.status_code}")
    if r.status_code != 201:
        print(f"Erro: {r.text}")
    else:
        bolao = r.json()
        print(f"Bolão criado: {bolao['name']}")
    
    # 4. Buscar bolões
    print("\n4. Buscando bolões...")
    r = requests.get(f"{BASE_URL}/boloes/search?q=Copa", headers=headers)
    print(f"Status: {r.status_code}")
    if r.status_code != 200:
        print(f"Erro: {r.text}")
    else:
        results = r.json()
        print(f"Encontrados {len(results)} bolões")
        print(json.dumps(results, indent=2))
