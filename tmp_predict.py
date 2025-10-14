import os, json, requests
api = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_GEMINI_API_KEY')
if not api:
    raise SystemExit('no api key')
model='models/imagen-4.0-fast-generate-001'
url=f'https://generativelanguage.googleapis.com/v1beta/{model}:predict'
payload={
    "prompt": {"text": "Fotorealistyczne zdjęcie psa biegającego po plaży"},
    "image_format": "png",
    "sample_count": 1
}
resp=requests.post(url, params={'key': api}, json=payload, timeout=120)
print(resp.status_code)
print(resp.text[:400])
