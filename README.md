
**Uruchomienie lokalne (Flask)**
- Wymagania: `Python 3.10–3.12` (dla 3.13 patrz uwagi dot. `audioop` poniżej)
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
- Szybki zestaw minimalny:
  - `pip install flask openai requests beautifulsoup4 python-dotenv markupsafe`
- Rozszerzony zestaw (obsługa S3/TTS/audio/wideo):
  - `pip install flask openai requests beautifulsoup4 python-dotenv markupsafe boto3 pydub python-slugify google-cloud-texttospeech azure-cognitiveservices-speech`
- Wejście/wyjście audio (opcjonalnie, jeśli nagrywasz mikrofon lub używasz zależności tego wymagających):
  - `pip install pyaudio`
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
 - Windows i moduł `pwd`: `pwd`/`grp` są częścią biblioteki standardowej tylko na Linux/macOS. Na Windows nie instaluje się ich przez pip. Kod jest dostosowany, aby nie wymagać `pwd` na Windows (patrz poprawka w `logging_config.py`). Funkcja `/webutils/logs` może nadal wymagać dostosowania na Windows.
- Błąd `ModuleNotFoundError: No module named 'pyaudioop'`:
   - W Pythonie 3.13 usunięto moduł standardowy `audioop` (PEP 594). Niektóre biblioteki próbują użyć jego zamiennika (`pyaudioop`).
   - Rozwiązania:
     - Preferowane: użyj Pythona 3.12.x (zalecana wersja dla tego projektu). Na Windows: `py -3.12 -m venv .venv` i aktywuj venv.
     - Jeśli musisz zostać na 3.13: `pip install audioop-lts`. W repo dodany jest plik `pyaudioop.py`, który działa jako shim i re-eksportuje API `audioop` pod nazwą `pyaudioop`, więc importy `import pyaudioop as audioop` będą działały.
     - Jeżeli błąd pochodzi z `pyaudio`/pakietów rozpoznawania mowy, rozważ przypięcie wersji zgodnych z 3.12 lub przejście na 3.12.

7) Przydatne adresy
- Strona główna: `http://127.0.0.1:<PORT>/`
- Generator video news: `http://127.0.0.1:<PORT>/news-to-video/`
- Materiały reklamowe (S3): `http://127.0.0.1:<PORT>/materialy-reklamowe/`

8) Dalsza konfiguracja (opcjonalnie)
- Integracje chmurowe (S3, Google/Azure/OpenAI TTS) wymagają odpowiednich kluczy środowiskowych jak wyżej oraz często dodatkowych plików konfiguracyjnych (np. `GOOGLE_APPLICATION_CREDENTIALS` do pliku JSON z kontem usługi).
- Jeśli korzystasz z funkcji audio/wideo, doinstaluj `ffmpeg` (np. `choco install ffmpeg` na Windows lub `sudo apt-get install -y ffmpeg` na Debian/Ubuntu).
