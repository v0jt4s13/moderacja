# tts_elevenlabs.py
import os
import requests
from xml.sax.saxutils import escape  # dla spójności z MS plikiem (tu nie używamy SSML)
from typing import List, Dict, Any

ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"  # inne: "mp3_44100_192", "pcm_16000", itd.

def _get_api_key() -> str:
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("Brak zmiennej środowiskowej ELEVENLABS_API_KEY")
    return api_key

def _headers_json(api_key: str) -> Dict[str, str]:
    return {
        "xi-api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def _headers_audio(api_key: str) -> Dict[str, str]:
    return {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }

def _fetch_all_voices(api_key: str) -> List[Dict[str, Any]]:
    print('_fetch_all_voices')
    """Pobiera listę głosów z ElevenLabs. Zwraca surowe obiekty voice."""
    url = f"{ELEVEN_API_BASE}/voices"
    try:
        print(f'requests.get({url}, headers={_headers_json(api_key)}, timeout=30)')
        r = requests.get(url, headers=_headers_json(api_key), timeout=30)
        if not r.ok:
            print(f"ElevenLabs list voices error: {r.status_code} {r.text}")
            return []
        data = r.json() or {}
        print(data)
        return data.get("voices", [])
    except Exception as e:
        print(f"Wyjątek przy pobieraniu listy głosów: {e}")
        return []

def _normalize_voice_row(v: Dict[str, Any]) -> Dict[str, Any]:
    """Mapuje voice -> {name, language_codes, gender} w stylu Twojego pliku MS."""
    name = v.get("name") or ""
    labels = v.get("labels") or {}
    # Uwaga: ElevenLabs nie zwraca standardowych locale; spróbujemy wydobyć wskazówki.
    # Często w labels pojawiają się klucze typu 'gender', 'accent', 'age', itp.
    gender = (labels.get("gender") or "").upper()
    # Spróbujmy heurystyki językowej:
    # - Jeśli w name/labels pojawia się 'Polish' lub 'PL', oznacz pl-PL
    # - Inaczej brak lub heurystyka dla en-GB/uk-UA
    lang_codes = []
    hay = " ".join([
        name,
        str(labels.get("language", "")),
        str(labels.get("accent", "")),
        str(labels.get("description", "")),
    ]).lower()

    if "polish" in hay or " pl" in hay or "pl-" in hay:
        lang_codes.append("pl-PL")
    elif "english" in hay and "british" in hay:
        lang_codes.append("en-GB")
    elif "ukrainian" in hay or "uk-ua" in hay or "ukrain" in hay:
        lang_codes.append("uk-UA")

    return {
        "name": name,
        "language_codes": lang_codes,
        "gender": gender or "",
        # dodatkowo, ale nie pokazujemy na liście (przydaje się do mapowania):
        "_voice_id": v.get("voice_id"),
    }

def _resolve_voice_id(api_key: str, voice: str) -> str:
    """
    Jeśli użytkownik poda voice_id – użyj go.
    Jeśli poda nazwę – szukaj dokładnie (case-insensitive), a potem częściowo.
    """
    # heurystyka: voice_id to zazwyczaj 20–40 znaków alfanum (bez spacji/dużo myślników)
    if isinstance(voice, str) and len(voice) >= 20 and " " not in voice:
        return voice  # traktuj jako voice_id

    voices = _fetch_all_voices(api_key)
    # exact, case-insensitive
    for v in voices:
        if v.get("name", "").lower() == voice.lower():
            return v.get("voice_id", "")
    # partial
    for v in voices:
        if voice.lower() in v.get("name", "").lower():
            return v.get("voice_id", "")
    print(f"Nie znaleziono voice_id dla nazwy '{voice}'. Użyj dokładnej nazwy lub voice_id.")
    return ""

def tts_elevenlabs(text, voice="nova", speed=1.0, output_path=None):
    """
    Analog do tts_microsoft():
    - Zwraca True/False
    - Używa ElevenLabs TTS
    - Zapisuje audio do output_path
    """
    if not output_path:
        return False

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return False

    # prędkość – ElevenLabs wspiera 'speed' w voice_settings.
    try:
        spd = float(speed)
    except Exception:
        spd = 1.0
    # sensowny zakres
    spd = max(0.5, min(2.0, spd))

    # Ustal voice_id
    voice_id = _resolve_voice_id(api_key, voice)
    if not voice_id:
        return False

    url = f"{ELEVEN_API_BASE}/text-to-speech/{voice_id}"
    params = {"output_format": DEFAULT_OUTPUT_FORMAT}
    payload = {
        "text": text,
        "model_id": DEFAULT_MODEL_ID,
        "voice_settings": {
            # typowe ustawienia – można dostroić
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": spd,
        }
    }

    try:
        r = requests.post(url, headers=_headers_audio(api_key), params=params, json=payload, timeout=120)
    except Exception as e:
        print(f"Err1: {e}")
        return False

    if r.ok:
        try:
            with open(output_path, "wb") as f:
                f.write(r.content)
            print('🎉 🎉 🎉 Synthesis succeeded.')
            return True
        except Exception as e:
            print(f"Błąd zapisu pliku audio: {e}")
            return False
    else:
        try:
            # spróbujmy pokazać komunikat JSON, jeśli jest
            err = r.json()
        except Exception:
            err = r.text
        print(f"Synthesis failed. HTTP {r.status_code}: {err}")
        return False

def elevenlabs_list_voices():
    return list_voices()

def list_voices():
    """
    Zwraca listę w formacie:
    [{"name": ..., "language_codes": [...], "gender": ...}, ...]
    Pierwsza sekcja: PL
    Druga sekcja (po separatorze): inne wybrane języki (en-GB, uk-UA)
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        # zachowujemy kompatybilność – zwróć chociaż nagłówki sekcji
        return [
            {"name": "--- brak klucza API ---", "language_codes": [], "gender": ""},
            {"name": "--- głosy wspierające język PL ---", "language_codes": [], "gender": ""},
            {"name": "--- pozostałe głosy ---", "language_codes": [], "gender": ""},
        ]

    # print(f'api_key={api_key}')

    raw_voices = _fetch_all_voices(api_key)
    rows = [_normalize_voice_row(v) for v in raw_voices]

    # Podział jak w pliku MS
    pl_voices = [v for v in rows if any("pl" in code.lower() for code in v["language_codes"])]
    allowed_langs = {"en-gb", "uk-ua"}
    other_voices = [
        v for v in rows
        if not any("pl" in code.lower() for code in v["language_codes"])
        and any(code.lower() in allowed_langs for code in v["language_codes"])
    ]

    first_row = [{
        "name": "--- głosy wspierające język PL ---",
        "language_codes": [],
        "gender": ""
    }]
    brake_voices = [{
        "name": "--- pozostałe głosy ---",
        "language_codes": [],
        "gender": ""
    }]

    # Usuń pomocniczy klucz zanim oddasz wynik
    def drop_internal_keys(lst: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for v in lst:
            vv = dict(v)
            vv.pop("_voice_id", None)
            out.append(vv)
        return out

    return first_row + drop_internal_keys(pl_voices) + brake_voices + drop_internal_keys(other_voices)




test_voice = {
    'voices': [
        {
            'voice_id': '9BWtsMINqrJLrRacOk9x', 
            'name': 'Aria', 
            'samples': None, 
            'category': 'premade', 
            'fine_tuning': {
                'is_allowed_to_fine_tune': True, 
                'state': {
                    'eleven_multilingual_v2': 'fine_tuned', 
                    'eleven_turbo_v2_5': 'fine_tuned', 
                    'eleven_flash_v2_5': 'fine_tuned', 
                    'eleven_v2_flash': 'fine_tuned', 
                    'eleven_v2_5_flash': 'fine_tuned', 
                    'eleven_turbo_v2': 'fine_tuned', 
                    'eleven_flash_v2': 'fine_tuned'
                }, 
                'verification_failures': [], 
                'verification_attempts_count': 0, 
                'manual_verification_requested': False, 
                'language': 'en', 
                'progress': {
                    'eleven_flash_v2_5': 1.0, 
                    'eleven_v2_flash': 1.0, 
                    'eleven_flash_v2': 1.0, 
                    'eleven_v2_5_flash': 1.0
                }, 
                'message': {
                    'eleven_flash_v2_5': 'Done!', 
                    'eleven_v2_flash': 'Done!', 
                    'eleven_flash_v2': 'Done!', 
                    'eleven_v2_5_flash': 'Done!'
                }, 
                'dataset_duration_seconds': None, 
                'verification_attempts': None, 
                'slice_ids': None, 
                'manual_verification': None, 
                'max_verification_attempts': 5, 
                'next_max_verification_attempts_reset_unix_ms': 1700000000000
            }, 
            'labels': {
                'accent': 'american', 
                'descriptive': 'husky', 
                'age': 'middle_aged', 
                'gender': 'female', 
                'language': 'en', 
                'use_case': 'informative_educational'
            }, 
            'description': 'A middle-aged female with an African-American accent. Calm with a hint of rasp.', 
            'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3', 
            'available_for_tiers': [], 
            'settings': None, 
            'sharing': None, 
            'high_quality_base_model_ids': [
                'eleven_v2_flash', 
                'eleven_flash_v2', 
                'eleven_turbo_v2_5', 
                'eleven_multilingual_v2', 
                'eleven_v2_5_flash', 
                'eleven_flash_v2_5', 
                'eleven_turbo_v2'
            ], 
            'verified_languages': [
                {
                    'language': 'en', 
                    'model_id': 'eleven_v2_flash', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'en', 
                    'model_id': 'eleven_flash_v2', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'en', 
                    'model_id': 'eleven_turbo_v2_5', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'en', 
                    'model_id': 'eleven_multilingual_v2', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'en', 
                    'model_id': 'eleven_v2_5_flash', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'en', 
                    'model_id': 'eleven_flash_v2_5', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'en', 
                    'model_id': 'eleven_turbo_v2', 
                    'accent': 'american', 
                    'locale': 'en-US', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/405766b8-1f4e-4d3c-aba1-6f25333823ec.mp3'
                }, {
                    'language': 'fr', 
                    'model_id': 'eleven_multilingual_v2', 
                    'accent': 'standard', 
                    'locale': 'fr-FR', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/ae97c224-d4d0-4e03-a9ab-36f031f48e94.mp3'
                }, {
                    'language': 'zh', 
                    'model_id': 'eleven_multilingual_v2', 
                    'accent': 'standard', 
                    'locale': 'cmn-CN', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/b6a58993-1cf7-4ea8-b3b1-a60b3641d5bf.mp3'
                }, {
                    'language': 'tr', 
                    'model_id': 'eleven_multilingual_v2', 
                    'accent': 'standard', 
                    'locale': 'tr-TR', 
                    'preview_url': 'https://storage.googleapis.com/eleven-public-prod/premade/voices/9BWtsMINqrJLrRacOk9x/9342915e-dd15-4a11-af37-96670decd65a.mp3'
                }
            ], 
            'safety_control': None, 
            'voice_verification': {
                'requires_verification': False, 
                'is_verified': False, 
                'verification_failures': [], 
                'verification_attempts_count': 0, 
                'language': None, 
                'verification_attempts': None
            }, 
            'permission_on_resource': None, 
            'is_owner': False, 
            'is_legacy': False, 
            'is_mixed': False, 
            'created_at_unix': None
        }, {
            'voice_id': 'EXAVITQu4vr4xnSDxMaL', 
            'name': 'Sarah', 
            'samples': None, 'category': 'premade', 
            'fine_tuning': {}
        }
    ]
}