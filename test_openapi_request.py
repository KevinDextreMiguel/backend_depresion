import requests

r = requests.get('http://localhost:8000/openapi.json', timeout=30)
print(f'Status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    print(f'OpenAPI version: {data.get("openapi")}')
    print(f'Title: {data.get("info", {}).get("title")}')
    print('✓ OpenAPI schema is valid!')
else:
    print(f'Error: {r.text[:200]}')

