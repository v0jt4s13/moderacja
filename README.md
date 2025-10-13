
**Uruchomienie lokalne (Flask)**
- Wymagania: `Python >= 3.10`
- Systemowe (opcjonalnie do funkcji audio/wideo): `ffmpeg`

1) Klonowanie i środowisko
- `git clone <URL_repozytorium>` i przejdź do katalogu projektu `moderacja`
- Utwórz i aktywuj venv:
  - PowerShell (Windows):
    - `py -3 -m venv .venv`
    - `.\.venv\Scripts\Activate.ps1`
  - Linux/macOS:
    - `python3 -m venv .venv`
    - `source .venv/bin/activate`

2) Instalacja zależności (minimalny zestaw do startu serwera)
- `pip install flask python-dotenv boto3 pydub requests beautifulsoup4 python-slugify google-cloud-texttospeech azure-cognitiveservices-speech`
- Uwaga: część modułów TTS jest importowana w czasie startu — powyższe paczki są wymagane nawet jeśli nie używasz tych funkcji w UI.

3) Zmienne środowiskowe (logowanie)
- Wymagane do zalogowania przez `/login`:
  - `FLASK_SECRET_KEY` – dowolny losowy ciąg
  - `ADMIN_PASSWORD` – hasło dla użytkownika `admin`
- PowerShell (sesja):
  - `$env:FLASK_SECRET_KEY = "dev-secret"`
  - `$env:ADMIN_PASSWORD   = "admin123"`
- Bash:
  - `export FLASK_SECRET_KEY=dev-secret`
  - `export ADMIN_PASSWORD=admin123`

4) Alternatywa: plik `.env`
- Aplikacja wczytuje `.env` z katalogu nadrzędnego `data_settings`.
- Utwórz folder i plik: `mkdir ..\data_settings` oraz `..\data_settings\.env`
- Przykład zawartości `..\data_settings\.env`:
  - `FLASK_SECRET_KEY=dev-secret`
  - `ADMIN_PASSWORD=admin123`
  - (opcjonalnie, jeśli chcesz użyć S3/TTS): `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_REGION`, `AWS_S3_BUCKET`, `OPENAI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_SPEECH_KEY`, `AZURE_REGION`

5) Start aplikacji
- `python app.py`
- Aplikacja nasłuchuje na wolnym porcie, np. `http://127.0.0.1:5000/` (port jest wypisany w konsoli przy starcie).
- Zaloguj się na `/login` używając `admin` i ustawionego `ADMIN_PASSWORD`.

6) Znane pułapki i rozwiązywanie problemów
- Brak modułu `price_compare`: repo zawiera szablony, ale brak modułu Pythona. Jeśli otrzymasz błąd importu przy starcie:
  - Tymczasowo wyłącz ten moduł komentując 2 linie w `app.py:20` oraz `app.py:23` (`from price_compare.routes import price_compare_bp` i `app.register_blueprint(price_compare_bp)`).
- Biblioteki TTS/S3: jeśli nie chcesz ich używać, ale import nadal się nie powiódł, sprawdź czy zainstalowałeś paczki z punktu 2).
- `ffmpeg`: wymagany przez `pydub` dopiero przy operacjach na audio/wideo. Do samego startu serwera nie jest konieczny.
- Uprawnienia do logów: pliki logów są zapisywane w `logs/` w katalogu projektu. Katalog jest tworzony automatycznie.

7) Przydatne adresy
- Strona główna: `http://127.0.0.1:<PORT>/`
- Generator video news: `http://127.0.0.1:<PORT>/news-to-video/`
- Materiały reklamowe (S3): `http://127.0.0.1:<PORT>/materialy-reklamowe/`

8) Dalsza konfiguracja (opcjonalnie)
- Integracje chmurowe (S3, Google/Azure/OpenAI TTS) wymagają odpowiednich kluczy środowiskowych jak wyżej oraz często dodatkowych plików konfiguracyjnych (np. `GOOGLE_APPLICATION_CREDENTIALS` do pliku JSON z kontem usługi).
- Jeśli korzystasz z funkcji audio/wideo, doinstaluj `ffmpeg` (np. `choco install ffmpeg` na Windows lub `sudo apt-get install -y ffmpeg` na Debian/Ubuntu).
