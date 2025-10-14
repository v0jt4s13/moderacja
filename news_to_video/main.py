# talk_to/news_to_video/main.py
# from __future__ import annotations
import os
import json
import uuid
import ast
# from moviepy.editor import (
#     ImageClip, VideoFileClip, AudioFileClip,
#     concatenate_videoclips, vfx
# )
from pydub import AudioSegment
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, List, Dict, Optional, Tuple
from urllib.parse import urlparse, urljoin, parse_qs, urlsplit, urlunsplit, quote, parse_qsl, urlencode
import re
import requests
from bs4 import BeautifulSoup
import sys
import traceback
import math
import mimetypes
import boto3
from io import BytesIO
from ffmpeg_resolver import get_ffmpeg_exe, get_ffprobe_exe

from loggers import news_to_video_logger
from news_to_video.renders_engines.s3_proc import ( 
    save_json,
    load_json,
    _s3_ready,
    _s3_key_for_local, 
    _s3_upload_file, 
    _s3_download_json, 
    sync_project_to_s3
)

from news_to_video.renders_engines.helpers_proc import (
    detect_media_type
)

# ---- TTS providers (wrappers) ----
HAVE_GOOGLE = False
HAVE_MS = False
try:
    from apps_utils.tts_google import google_list_voices, tts_google
    HAVE_GOOGLE = True
except Exception:
    google_list_voices = None
    tts_google = None

try:
    from apps_utils.tts_microsoft import microsoft_list_voices, tts_microsoft
    HAVE_MS = True
except Exception:
    microsoft_list_voices = None
    tts_microsoft = None

from news_to_video.config import (
    PROJECTS_DIR, 
    FORMAT_PRESETS, 
    SUPPORTED_RENDERERS
)

from config import DEFAULT_MODEL_VERSION, get_config

from apps_utils.s3_utils import (
    S3_PREFIX,
    upload_to_s3, 
    upload_audio_to_s3,
    build_and_upload_audio_indexes, 
    download_from_s3, 
    upload_json_to_s3,
    list_audio_test_files, 
    get_audio_test_texts,
    build_two_months_index,
    s3_session,
    get_s3_url,
    remove_article_from_audio_index,
    prune_audio_index_after_deletions
)
# -----------------------------
# Konfiguracja
# -----------------------------
@dataclass
class RenderProfile:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_bitrate: str = "5000k"
    audio_bitrate: str = "192k"

@dataclass
class TTSSettings:
    language: str = "pl"
    provider: str = "google"     # "google" | "microsoft"
    voice: str = ""              # provider voice id
    speed: float = 1.0           # 0.5 .. 2.0

@dataclass
class MediaItem:
    type: str   # 'image' | 'video'
    src: str
    clip: Optional[Dict] = None  # {"start": float, "end": float}

@dataclass
class BrandConfig:
    logo_path: Optional[str] = None   # ścieżka lokalna lub URL (HTTP)
    position: str = "top-right"       # top-left | top-right | bottom-left | bottom-right
    opacity: float = 0.85             # 0.0–1.0
    scale: float = 0.15               # logo szerokość = scale * video_width

@dataclass
class TransitionsConfig:
    use_xfade: bool = True            # czy stosować crossfade
    duration: float = 0.5             # sekundy (0.1–2.0)
    transition: str = "fade"          # typ xfade (np. fade, smoothleft, circleopen, wipeleft ...)

# -----------------------------
# Utils
# -----------------------------
def _get_renderer(payload: Dict) -> str:
    r = (payload.get("renderer") or {}).get("type", "local")
    r = str(r).strip().lower()
    return r if r in SUPPORTED_RENDERERS else "local"

def _get_renderer_cfg(payload: Dict) -> Dict[str, Any]:
    r = payload.get("renderer") or {}
    return r.get("config") or {}

def _slugify(text: str) -> str:
    try:
        from slugify import slugify
        return slugify(text)
    except Exception:
        return "".join(c.lower() if c.isalnum() else "-" for c in text)[:64].strip("-")

def _extract_voice_id(v: Any) -> str:
    if isinstance(v, dict):
        for key in ("name", "ShortName", "short_name", "voice", "id", "VoiceId"):
            if key in v and isinstance(v[key], str):
                return v[key]
        return str(v)
    return str(v)

def profile_for(fmt: str, base: Optional[RenderProfile] = None) -> RenderProfile:
    """Zwraca RenderProfile dopasowany do wybranego formatu społecznościowego."""
    fmt = (fmt or "16x9").lower()
    w, h = FORMAT_PRESETS.get(fmt, FORMAT_PRESETS["16x9"])
    p = base or RenderProfile()
    return RenderProfile(
        width=w, height=h, fps=p.fps,
        video_bitrate=p.video_bitrate, audio_bitrate=p.audio_bitrate
    )

def find_project_dir(project_id: str) -> Optional[str]:
    for root, dirs, files in os.walk(PROJECTS_DIR):
        if "manifest.json" in files:
            try:
                # print(11111111111)
                m = load_json(os.path.join(root, "manifest.json"))
            except Exception:
                continue
            if m.get("project_id") == project_id:
                return root
    return None

def delete_project(project_id: str) -> bool:
    pdir = find_project_dir(project_id)
    if not pdir:
        return False
    try:
        shutil.rmtree(pdir)
        return True
    except Exception:
        return False
    
def normalize_voice_id(voice_val: Any) -> str:
    if isinstance(voice_val, str):
        s = voice_val.strip()
        # spróbuj JSON
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return _extract_voice_id(obj)
        except Exception:
            pass
        # spróbuj literal_eval (repr dict z pojedynczymi cudzysłowami)
        try:
            obj = ast.literal_eval(s)
            if isinstance(obj, dict):
                return _extract_voice_id(obj)
        except Exception:
            pass
        return s
    elif isinstance(voice_val, dict):
        return _extract_voice_id(voice_val)
    return str(voice_val)

def absolutize(url: str, base: str) -> str:
    try:
        return urljoin(base, url)
    except Exception:
        return url

def fetch_html(page_url: str, timeout: int = 15) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NewsToVideoBot/1.0; +https://example.local)"
    }
    r = requests.get(page_url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text

def extract_article(html: str, base_url: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")

    # Tytuł
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip() or title

    # Główna treść – heurystyki: <article>, [role=main], #content, .article, itp.
    main_nodes = []
    for sel in ["article", "[role=main]", "#content", ".content", ".article", ".post", ".entry-content", ".news", ".story"]:
        main = soup.select_one(sel)
        if main:
            main_nodes.append(main)
    main = max(main_nodes, key=lambda el: len(el.get_text(" ", strip=True))) if main_nodes else soup.body or soup

    # Usuń elementy niekontentowe
    for bad in main.select("script, style, noscript, nav, footer, header, form, aside"):
        bad.decompose()

    # Zbierz akapity
    paragraphs = []
    for p in main.find_all(["p", "h2", "h3", "li"]):
        txt = p.get_text(" ", strip=True)
        if txt and len(txt) > 2:
            paragraphs.append(txt)
    text = "\n".join(paragraphs).strip()

    # Media: img, video > source/src
    media = []
    # IMG
    for img in main.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            continue
        src_abs = absolutize(src, base_url)
        mtype = detect_media_type(src_abs)
        if mtype == "image":
            media.append({"type": "image", "src": src_abs})
    # VIDEO
    for v in main.find_all("video"):
        vsrc = v.get("src")
        if vsrc:
            v_abs = absolutize(vsrc, base_url)
            if detect_media_type(v_abs) == "video":
                media.append({"type": "video", "src": v_abs})
        for s in v.find_all("source"):
            ssrc = s.get("src")
            if ssrc:
                s_abs = absolutize(ssrc, base_url)
                if detect_media_type(s_abs) == "video":
                    media.append({"type": "video", "src": s_abs})

    # Dedup
    seen = set()
    uniq_media = []
    for m in media:
        key = (m["type"], m["src"])
        if key in seen:
            continue
        seen.add(key)
        uniq_media.append(m)

    return {"title": title, "text": text, "media": uniq_media}

def _simple_sentence_split(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]

def _fallback_summarize(text: str, target_words: int) -> str:
    # Prosta strategia: weź zdania po kolei aż do limitu słów
    sentences = _simple_sentence_split(text)
    out = []
    total = 0
    for s in sentences:
        w = len(s.split())
        if total + w > target_words:
            break
        out.append(s)
        total += w
    if not out:  # gdy pierwsze zdanie za długie
        words = text.split()
        out = [" ".join(words[:target_words])]
    return " ".join(out).strip()

def _summarize_with_openai(text: str, target_words: int, language: str = "pl") -> Optional[str]:
    """
    Opcjonalne użycie OpenAI (jeśli biblioteka i klucz są dostępne).
    Zwraca streszczenie lub None przy błędzie.
    """
    try:
        # Nowy klient
        try:
            import os
            from openai import OpenAI
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                return None
            client = OpenAI(api_key=api_key)
            prompt = (
                f"Streść poniższy tekst w języku {language} tak, aby mieścił się w około {target_words} słowach. "
                "Zachowaj najważniejsze fakty i klarowną narrację dla lektora newsowego.\n"
                "Na zakończenie dodaj informację na temat źródła czyli londynek.net \n\n"
                f"--- TEKST ---\n{text}\n--- KONIEC ---"
            )
            resp = client.chat.completions.create(
                model=DEFAULT_MODEL_VERSION,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return None

    except Exception:
        print('✨ stary model klienta z openAI ✨')
        # Spróbuj starego klienta, jeśli nowy zawiedzie
        import os
        import openai  # stary klient
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        openai.api_key = api_key
        prompt = (
            f"Streść poniższy tekst w języku {language} tak, aby mieścił się w około {target_words} słowach. "
            "Zachowaj najważniejsze fakty i klarowną narrację dla lektora newsowego.\n"
            "Na zakończenie dodaj informację na temat źródła czyli londynek.net \n\n"
            f"--- TEKST ---\n{text}\n--- KONIEC ---"
        )
        resp = openai.chat.completions.create (
            model=DEFAULT_MODEL_VERSION,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150,
        )
        try:
            summary = resp["choices"][0]["message"]["content"].strip()
        except:
            summary = resp.choices[0].message.content.strip()

        return summary
    

def _make_color_segment(duration: float, profile: RenderProfile, out_path: str, color: str = "black") -> bool:
    dur = max(0.2, float(duration))
    cmd = (
        f"ffmpeg -y -f lavfi -i color=c={color}:s={profile.width}x{profile.height}:r={profile.fps} "
        f"-t {dur:.3f} -c:v libx264 -pix_fmt yuv420p -b:v {profile.video_bitrate} -an {shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)

# sprawdzenie/uzyskanie publicznego URL dla assetu (HTTP lub upload do S3)
def _is_http_url(x: str) -> bool:
    return isinstance(x, str) and (x.startswith("http://") or x.startswith("https://"))

def _ensure_remote_url(path_or_url: str, content_type: Optional[str] = None) -> Optional[str]:
    """
    Zwraca publiczny URL:
    - jeśli to już http(s) -> zwróć jak jest
    - jeśli lokalna ścieżka -> wyślij do S3 i zwróć URL
    """
    if not path_or_url:
        return None
    if _is_http_url(path_or_url):
        return path_or_url
    key = _s3_key_for_local(path_or_url)
    if not key:
        return None
    return _s3_upload_file(path_or_url, key, content_type=content_type)

# pomocnicze: generacja prostego SRT (grupowanie do 5 słów, równy podział czasu)
def _to_srt_timestamp(t: float) -> str:
    # HH:MM:SS,mmm
    if t < 0: t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _chunk_words(text: str, max_words: int = 5) -> List[str]:
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i+max_words]))
    return chunks or ([] if not text else [text])

def _write_srt_by_chunks(text: str, total_duration: float, out_path: str, max_words: int = 5):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    chunks = _chunk_words(text, max_words=max_words)
    n = max(1, len(chunks))
    # równy podział czasu między bloki
    dur = max(0.2, total_duration / n)
    t = 0.0
    with open(out_path, "w", encoding="utf-8") as f:
        for i, line in enumerate(chunks, start=1):
            t0 = t
            t1 = min(total_duration, t + dur)
            f.write(f"{i}\n{_to_srt_timestamp(t0)} --> {_to_srt_timestamp(t1)}\n{line}\n\n")
            t = t1

def _parse_iso8601(dt: str) -> Optional[datetime]:
    if not dt: 
        return None
    s = str(dt).strip()
    try:
        # obsługa "....Z"
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _encode_asset_url(u: str) -> str:
    """Zwraca URL z poprawnie zakodowaną ścieżką (bez surowych znaków diakrytycznych)."""
    if not isinstance(u, str) or not u:
        return u
    parts = urlsplit(u)
    # Zakoduj path zgodnie z RFC 3986 (zachowaj / oraz znaki rezerwowe)
    safe_chars = "/:@-._~!$&'()*+,;=%"
    path = quote(parts.path, safe=safe_chars)
    # Przepisz query bez zmian semantycznych (zachowanie oryginalnych wartości)
    if parts.query:
        q = urlencode(parse_qsl(parts.query, keep_blank_values=True), doseq=True)
    else:
        q = ""
    return urlunsplit((parts.scheme, parts.netloc, path, q, parts.fragment))

def _is_public_http(url: str) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))

# [ADD] — usuwanie lokalne z zachowaniem kopii na S3
def delete_project_local_only(project_id: str, ensure_s3: bool = True) -> bool:
    """
    Usuwa projekt tylko z lokalnego dysku. Opcjonalnie przed usunięciem wysyła
    kluczowe pliki do S3 (jeśli jeszcze ich tam nie ma).
    """
    pdir = find_project_dir(project_id)
    if not pdir:
        news_to_video_logger.info("[delete_local] project not found: %s", project_id)
        return False

    # upewnij się, że wszystko jest w S3
    if ensure_s3:
        try:
            sync_project_to_s3(pdir)
        except Exception as e:
            news_to_video_logger.info("❌ [delete_local] sync to S3 failed for %s: %s", project_id, str(e))

    try:
        shutil.rmtree(pdir)
        news_to_video_logger.info("[delete_local] removed local dir: %s", pdir)
        # spróbuj posprzątać puste katalogi rodziców (MM / YYYY)
        try:
            parent = os.path.dirname(pdir)
            for _ in range(3):
                if parent.startswith(PROJECTS_DIR) and not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                else:
                    break
        except Exception:
            pass
        return True
    except Exception as e:
        news_to_video_logger.info("❌ [delete_local] error removing %s: %s", pdir, str(e))
        return False

def summarize_to_duration(text: str, max_minutes: float = 2.0, wpm: int = 160, language: str = "pl") -> str:
    """
    Zwraca streszczenie o długości celowanej do max_minutes przy zadanym tempie mowy (wpm).
    Zakładamy ~1 słowo = 1 token mowy.
    """
    target_words = max(50, int(wpm * max_minutes * 0.9))  # bufor na pauzy
    # Spróbuj modelu, fallback do prostego skrótu
    model_sum = _summarize_with_openai(text, target_words, language=language)
    if model_sum:
        # Przytnij, gdyby model poszedł za daleko
        words = model_sum.split()
        if len(words) > target_words:
            return " ".join(words[:target_words])
        return model_sum
    return _fallback_summarize(text, target_words)

def scrap_page(url: str, language: str = "pl") -> Dict:
    """
    Główny punkt: pobiera stronę, wyodrębnia tytuł/treść/media i tworzy streszczenie <= ~2 min.
    Zwraca dict zgodny z payloadem formularza.
    """
    html = fetch_html(url)
    data = extract_article(html, url)
    title = data.get("title") or "Materiał"
    full_text = (data.get("text") or "").strip()
    # print(f'\t\t\tscrap_page len={len(full_text)}')

    summary = summarize_to_duration(full_text, max_minutes=2.0, wpm=160, language=language)

    # Ułóż media w formacie modułu
    media_items = []
    for m in data.get("media", []):
        mtype = detect_media_type(m.get("src", ""))
        if mtype in ("image", "video"):
            if len(m["src"].split('.webp')) == 2:
                m_src = m["src"].split('.webp')[0]
            elif len(m["src"].split('?')) == 2:
                m_src = m["src"].split('?')[0]
            else:
                m_src = m["src"]
            
            media_items.append({"type": mtype, "src": m_src})

    return {
        "title": title,
        "text": summary or full_text,
        "media": media_items,
        "source_url": url,
    }



# def detect_media_type_depr(path_or_url: str) -> Optional[str]:
#     p = str(path_or_url).lower()
#     if any(p.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")):
#         return "image"
#     if any(p.endswith(ext) for ext in (".mp4", ".mov", ".mkv", ".webm")):
#         return "video"
#     return None


# def list_voices_depr(provider: str) -> List[str]:
#     provider = (provider or "google").lower()
#     try:
#         if provider == "google" and HAVE_GOOGLE and callable(google_list_voices):
#             return list(google_list_voices())
#         if provider == "microsoft" and HAVE_MS and callable(microsoft_list_voices):
#             return list(microsoft_list_voices())
#     except Exception:
#         pass
#     return []


def _voices_from_config(provider: str) -> List[str]:
    cfg = get_config("tts") or {}
    p_cfg = cfg.get(provider) or {}
    raw = p_cfg.get("voices")
    extracted: List[str] = []

    def _push(voice_id: Optional[str]) -> None:
        if not voice_id:
            return
        voice_id = str(voice_id)
        if voice_id:
            extracted.append(voice_id)

    if isinstance(raw, dict):
        for v_id in raw.keys():
            _push(v_id)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                v_id = (
                    item.get("voice_id")
                    or item.get("id")
                    or item.get("name")
                    or item.get("value")
                )
                _push(v_id)
            elif isinstance(item, str):
                _push(item)

    if not extracted:
        return []

    seen = set()
    ordered = []
    for voice_id in extracted:
        if voice_id not in seen:
            seen.add(voice_id)
            ordered.append(voice_id)

    def _is_pl(voice: str) -> bool:
        lower = voice.lower()
        return lower.startswith("pl") or lower.startswith("pl-")

    def _is_preferred_other(voice: str) -> bool:
        lower = voice.lower()
        return lower.startswith(("en", "en-", "uk", "uk-"))

    pl_voices = [v for v in ordered if _is_pl(v)]
    other_pref = [v for v in ordered if not _is_pl(v) and _is_preferred_other(v)]
    remaining = [v for v in ordered if v not in pl_voices and v not in other_pref]

    result: List[str] = []
    if pl_voices:
        result.append("--- głosy wspierające język PL ---")
        result.extend(pl_voices)
    if other_pref or remaining:
        result.append("--- pozostałe głosy ---")
        result.extend(other_pref + remaining)
    return result


def list_voices(provider: str) -> List[str]:
    provider = (provider or "google").lower()
    try:
        if provider == "google" and HAVE_GOOGLE and callable(google_list_voices):
            raw = google_list_voices()
            # print(raw)
            return [_extract_voice_id(v) for v in (raw or [])]
        if provider == "microsoft" and HAVE_MS and callable(microsoft_list_voices):
            raw = microsoft_list_voices()
            return [_extract_voice_id(v) for v in (raw or [])]
    except Exception as err:
        news_to_video_logger.warning("list_voices provider=%s error: %s", provider, err)

    fallback = _voices_from_config(provider)
    if fallback:
        return fallback
    return []


def update_manifest(project_dir: str, patch: Dict) -> Dict:
    print(f'\n\t\tSTART ==> update_manifest({project_dir})')
    """
    Aktualizuje pola na poziomie root w manifest.json (np. status, error, outputs).
    Priorytet odczytu: S3 -> lokalnie.
    """
    mpath = os.path.join(project_dir, "manifest.json")

    manifest = None
    if _s3_ready():
        try:
            manifest = _s3_download_json(mpath)
            if manifest:
                news_to_video_logger.info("[manifest] update_manifest: loaded from S3: %s", mpath)
        except Exception as e:
            news_to_video_logger.info("❌ [manifest] update_manifest: S3 load error: %s", str(e))

    if manifest is None:
        if os.path.isfile(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            news_to_video_logger.info("[manifest] type=%s, update_manifest: loaded local: %s", type(manifest), mpath)
        else:
            raise FileNotFoundError(f"manifest.json not found in {project_dir}")

    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(manifest.get(k), dict):
            manifest[k].update(v)
        else:
            manifest[k] = v

    save_json(mpath, manifest)

    # >>> DODAJ — jeżeli właśnie skończyliśmy render lub podbito outputs
    try:
        should_sync = (
            (patch.get("status") == "done") or
            ("outputs" in (patch or {}))
        )
        if should_sync and _s3_ready():
            sync_project_to_s3(project_dir)
            manifest["s3_synced"] = True
            save_json(mpath, manifest)
    except Exception as e:
        # opcjonalnie dopisz do manifestu błąd synca
        manifest["error"] = f"S3 sync error: {e}"
        save_json(mpath, manifest)
    # <<< KONIEC DODAWKI

    return manifest




def update_manifest_payload(project_dir: str, payload_updates: Dict):
    print(f'\n\t\tSTART ==> update_manifest_payload({project_dir}, {type(payload_updates)})')
    """
    Aktualizuje sekcję 'payload' w manifest.json.
    Priorytet odczytu: S3 -> lokalnie. Zapis: lokalnie + mirror do S3 (obsługiwany w save_json).
    """
    mpath = os.path.join(project_dir, "manifest.json")
    print(f'\t[update_manifest_payload] mpath===>{mpath}')

    manifest = None

    # 1) Spróbuj pobrać manifest z S3 (PRIORYTET)
    if _s3_ready():
        try:
            print(f'\t[update_manifest_payload] try download manifest from S3 ==> _s3_download_json({mpath})')
            manifest = _s3_download_json(mpath)
            if manifest:
                news_to_video_logger.info(f"[manifest] loaded from S3: {mpath}")
        except Exception as e:
            news_to_video_logger.info(f"❌ [manifest] S3 load error for {mpath}: {e}")

    # 2) Fallback: plik lokalny (nie używaj load_json aby nie nadpisać priorytetu S3)
    if manifest is None:
        print(f'\t[update_manifest_payload] manifest nie został odnaleziony na S3')
        if os.path.isfile(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            news_to_video_logger.info(f"[manifest] loaded from local: {mpath}")
        else:
            # 3) Brak manifestu — utwórz minimalny szkielet
            news_to_video_logger.info(f"[manifest] not found (S3/local). Creating new: {mpath}")
            manifest = {
                "project_id": os.path.basename(project_dir),
                "title": "News video",
                "created_at": datetime.now(timezone.utc),
                "payload": {},
                "status": "created",
                "outputs": {},
                "logs": [],
            }

    # 4) Aktualizacja payloadu
    manifest.setdefault("payload", {})
    manifest["payload"].update(payload_updates or {})

    # 5) Zapis (lokalnie + mirror do S3)
    save_json(mpath, manifest)


def _run(cmd: str) -> Tuple[bool, str]:
    """Run shell command, return (ok, stderr_out).
    Transparently replaces leading 'ffmpeg'/'ffprobe' with resolved paths.
    """
    tokens = shlex.split(cmd)
    if tokens:
        if tokens[0] == "ffmpeg":
            tokens[0] = get_ffmpeg_exe()
        elif tokens[0] == "ffprobe":
            tokens[0] = get_ffprobe_exe()
    proc = subprocess.run(tokens, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode == 0, proc.stderr.decode("utf-8", "ignore")


def _ffprobe_duration(path: str) -> float:
    """Return duration in seconds using ffprobe (resolver-aware)."""
    cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {shlex.quote(path)}"
    tokens = shlex.split(cmd)
    if tokens and tokens[0] == "ffprobe":
        tokens[0] = get_ffprobe_exe()
    proc = subprocess.run(tokens, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return float(proc.stdout.decode().strip())
    except Exception:
        return 0.0


# -----------------------------
# Pipeline
# -----------------------------
def create_project(payload: Dict) -> Dict:
    print(f'\n\t\tSTART ===> create_project()')

    title = payload.get("title") or "news-video"
    project_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    slug = _slugify(title) or project_id
    project_dir = os.path.join(PROJECTS_DIR, f"{datetime.now():%Y/%m}/{slug}-{project_id}")
    os.makedirs(project_dir, exist_ok=True)
    manifest = {
        "project_id": project_id,
        "title": title,
        "created_at": datetime.now(timezone.utc),
        "payload": payload,
        "status": "created",
        "outputs": {},
        "logs": [],
    }
    print(f'\n\t* * * * * * * * * * * * * * \n\t\tcreate_project() -> save_json \nproject_dir==>{project_dir}\nPROJECTS_DIR==>{PROJECTS_DIR}\n\t* * * * * * * * * * * * * * ')

    save_json(os.path.join(project_dir, "manifest.json"), manifest)
    
    print(f'\t\tEND ===> return "project_id": {project_id}, "project_dir": {project_dir}')

    return {"project_id": project_id, "project_dir": project_dir}


def segment_text(text: str, max_chars: int = 220) -> List[Dict]:
    if not text:
        return []
    import re
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    segments = []
    buf = ""
    sid = 1
    for s in sentences:
        if len(buf) + len(s) + 1 <= max_chars:
            buf = (buf + " " + s).strip()
        else:
            if buf:
                segments.append({"id": sid, "text": buf})
                sid += 1
                buf = s
            else:
                segments.append({"id": sid, "text": s})
                sid += 1
                buf = ""
    if buf:
        segments.append({"id": sid, "text": buf})
    return segments


def tts_call(provider: str, text: str, voice_id: str, speed: float, output_path: str) -> bool:
    # print(f'\n\t\tSTART ==> tts_call({provider}, {type(text)}, {voice_id}, {speed}, {output_path})')
    """
    Call provider TTS. Providers save directly to MP3 `output_path`.
    Odporne na błędy sieci/API — w razie wyjątku zwraca False (pipeline użyje ciszy).
    """
    provider = (provider or "google").lower()

    if provider == "google" and HAVE_GOOGLE and callable(tts_google):
        try:
            # próba z voice=
            # news_to_video_logger.info(f'[tts_call] ==> tts_google({text}, voice={voice_id}, speed={speed}, {output_path})')
            return bool(tts_google(text, voice=voice_id, speed=speed, output_path=output_path))
        except TypeError:
            # alternatywna sygnatura z lector=
            try:
                return bool(tts_google(text, lector=voice_id, speed=speed, output_path=output_path))
            except Exception as e:
                news_to_video_logger.error(f'❌ [tts_call][google] fallback (lector=) error: {e}')
                traceback.print_exc()
                return False
        except Exception as e:
            news_to_video_logger.error(f'❌ [tts_call][google] error: {e}')
            traceback.print_exc()
            return False

    if provider == "microsoft" and HAVE_MS and callable(tts_microsoft):
        try:
            # próba z voice=
            print(f'[tts_call] ==> tts_microsoft({text}, voice={voice_id}, speed={speed}, {output_path})')
            return bool(tts_microsoft(text, voice=voice_id, speed=speed, output_path=output_path))
        except TypeError:
            # alternatywna sygnatura z lector=
            try:
                return bool(tts_microsoft(text, lector=voice_id, speed=speed, output_path=output_path))
            except Exception as e:
                news_to_video_logger.error(f'❌ [tts_call][microsoft] fallback (lector=) error: {e}')
                # print(f"[TTS][microsoft] fallback (lector=) error: {e}", file=sys.stderr)
                traceback.print_exc()
                return False
        except Exception as e:
            news_to_video_logger.error(f'❌ [tts_call][microsoft] error: {e}')
            print(f"[TTS][microsoft] error: {e}", file=sys.stderr)
            traceback.print_exc()
            return False

    return False


def _make_silence_mp3(duration_sec: float, out_path: str) -> bool:
    # Generate silent MP3 using ffmpeg
    dur = max(1.0, float(duration_sec))
    cmd = (
        f"ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono "
        f"-t {dur:.3f} -acodec libmp3lame -q:a 4 {shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)


def _concat_audio_mp3(parts: List[str], out_path: str) -> bool:
    # Concat mp3 parts (re-encode to be safe)
    if not parts:
        return False
    list_path = os.path.join(os.path.dirname(out_path), "alist.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = (
        f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(list_path)} "
        f"-acodec libmp3lame -b:a 192k {shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)


def synthesize_tts(segments: List[Dict], settings: TTSSettings, out_dir: str) -> Tuple[str, List[Dict]]:
    # print(f'\n\t\tSTART ==> synthesize_tts({type(segments)}, {type(settings)}, {out_dir})')
    """
    Generate per-segment MP3 using selected provider and stitch into one MP3.
    Returns (audio_path, timeline).
    """
    os.makedirs(out_dir, exist_ok=True)
    timeline: List[Dict] = []
    cursor = 0.0
    parts: List[str] = []

    news_to_video_logger.info(f'[synthesize_tts] segments len: {len(segments)}')
    for seg in segments:
        # print('===>seg<===')
        seg_id = seg["id"]
        txt = seg["text"]
        seg_mp3 = os.path.join(out_dir, f"seg_{seg_id:03d}.mp3")

        ok = tts_call(settings.provider, txt, settings.voice, settings.speed, seg_mp3)
        if not ok or not os.path.isfile(seg_mp3):
            # Fallback: generate silence with estimated duration
            chars = max(20, len(txt))
            est_wpm = 160.0 * max(0.5, min(2.0, settings.speed))
            sec = max(1.2, min(14.0, (chars / 5.0) / (est_wpm / 60.0)))
            _make_silence_mp3(sec, seg_mp3)

        dur_sec = round(_ffprobe_duration(seg_mp3), 2) or 1.2
        timeline.append({
            "id": seg_id,
            "text": txt,
            "start": round(cursor, 2),
            "end": round(cursor + dur_sec, 2),
        })
        cursor += dur_sec
        parts.append(seg_mp3)

    out_path = os.path.join(out_dir, "narration.mp3")
    _concat_audio_mp3(parts, out_path)

    print(f'\n\t\tEND ==> synthesize_tts\nreturn:\n out_path: {out_path}\n timeline: {timeline}')
    return out_path, timeline


def _build_scale_pad_filter(profile: RenderProfile) -> str:
    # Keep aspect, pad to target
    w, h = profile.width, profile.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )


def _make_image_segment(image_path: str, duration: float, profile: RenderProfile, out_path: str) -> bool:
    vf = _build_scale_pad_filter(profile)
    cmd = (
        f"ffmpeg -y -loop 1 -t {duration:.3f} -i {shlex.quote(image_path)} "
        f"-vf \"{vf},fps={profile.fps}\" -r {profile.fps} "
        f"-c:v libx264 -pix_fmt yuv420p -b:v {profile.video_bitrate} -an {shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)


def _make_video_segment(video_path: str, start: Optional[float], end: Optional[float],
                        profile: RenderProfile, out_path: str) -> Tuple[bool, float]:
    vf = _build_scale_pad_filter(profile)
    ss = f"-ss {float(start):.3f} " if start is not None else ""
    to = ""
    if end is not None and start is not None and end > start:
        to = f"-to {float(end):.3f} "
    elif end is not None and start is None:
        to = f"-to {float(end):.3f} "
    cmd = (
        f"ffmpeg -y {ss}{to}-i {shlex.quote(video_path)} "
        f"-vf \"{vf},fps={profile.fps}\" -r {profile.fps} -an "
        f"-c:v libx264 -pix_fmt yuv420p -b:v {profile.video_bitrate} {shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    dur = _ffprobe_duration(out_path) if ok else 0.0
    return (ok and os.path.exists(out_path), dur)


def _concat_videos(parts: List[str], out_path: str) -> bool:
    if not parts:
        return False
    list_path = os.path.join(os.path.dirname(out_path), "vlist.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(list_path)} -c copy {shlex.quote(out_path)}"
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)

# funkcje pomocnicze do napisów i brandingu
def _escape_sub_path(path: str) -> str:
    # Escapowanie ścieżki dla filtra subtitles (ffmpeg/libass)
    return path.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


# [MODIFY] talk_to/news_to_video/main.py — branding+napisy: zaakceptuj .ass lub .srt (automatycznie wykryj po rozszerzeniu)
def _apply_branding_and_subtitles(video_in: str, subs_path: Optional[str], branding: Optional[BrandConfig],
                                  profile: RenderProfile, video_out: str) -> bool:
    """
    Nakłada logo (jeśli podano) i/lub wypala napisy (.ass lub .srt). Reenkoduje wideo (bez audio).
    """
    has_logo = bool(branding and branding.logo_path)
    has_subs = bool(subs_path and os.path.isfile(subs_path))

    if not has_logo and not has_subs:
        cmd = f"ffmpeg -y -i {shlex.quote(video_in)} -c copy {shlex.quote(video_out)}"
        ok, _ = _run(cmd)
        return ok and os.path.exists(video_out)

    inputs = ["-y", "-i", video_in]
    filter_parts = []
    mapsrc = "[0:v]"

    if has_logo:
        inputs += ["-i", branding.logo_path]  # HTTP/HTTPS też ok
        logo_w = max(32, int(profile.width * float(branding.scale or 0.15)))
        margin = 24
        pos = (branding.position or "top-right").lower()
        x_expr = f"main_w-w-{margin}" if "right" in pos else f"{margin}"
        y_expr = f"main_h-h-{margin}" if "bottom" in pos else f"{margin}"
        filter_parts.append(f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={float(branding.opacity or 0.85):.2f}[lg]")
        filter_parts.append(f"{mapsrc}[lg]overlay=x={x_expr}:y={y_expr}:format=auto[vlg]")
        mapsrc = "[vlg]"

    if has_subs:
        sub_escaped = _escape_sub_path(subs_path)  # type: ignore[arg-type]
        # ffmpeg automatycznie rozpoznaje ASS/SRT po rozszerzeniu
        filter_parts.append(f"{mapsrc}subtitles='{sub_escaped}'[vout]")
        mapsrc = "[vout]"

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", *inputs,
        "-filter_complex", filter_complex,
        "-map", mapsrc,
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-b:v", profile.video_bitrate,
        "-r", str(profile.fps),
        "-an", shlex.quote(video_out)
    ]
    cmd_str = " ".join(str(x) for x in cmd)
    ok, _ = _run(cmd_str)
    return ok and os.path.exists(video_out)



def _apply_branding_and_subtitles_depr(video_in: str, srt_path: Optional[str], branding: Optional[BrandConfig],
                                  profile: RenderProfile, video_out: str) -> bool:
    """
    Nakłada logo (jeśli podano) i/lub wypala napisy z pliku SRT. Reenkoduje wideo (video bez audio).
    """
    has_logo = bool(branding and branding.logo_path)
    has_subs = bool(srt_path and os.path.isfile(srt_path))

    # Jeśli brak efektów – skopiuj
    if not has_logo and not has_subs:
        cmd = f"ffmpeg -y -i {shlex.quote(video_in)} -c copy {shlex.quote(video_out)}"
        ok, _ = _run(cmd)
        return ok and os.path.exists(video_out)

    inputs = ["-y", "-i", video_in]
    filter_parts = []
    mapsrc = "[0:v]"

    if has_logo:
        inputs += ["-i", branding.logo_path]  # ffmpeg może czytać HTTP/HTTPS
        # Oblicz docelową szerokość logo względem video_width
        logo_w = max(32, int(profile.width * float(branding.scale or 0.15)))
        # Pozycja
        margin = 24
        pos = (branding.position or "top-right").lower()
        x_expr = f"main_w-w-{margin}" if "right" in pos else f"{margin}"
        y_expr = f"main_h-h-{margin}" if "bottom" in pos else f"{margin}"
        # Filtry: skala + przezroczystość + overlay
        filter_parts.append(f"[1:v]scale={logo_w}:-1,format=rgba,colorchannelmixer=aa={float(branding.opacity or 0.85):.2f}[lg]")
        filter_parts.append(f"{mapsrc}[lg]overlay=x={x_expr}:y={y_expr}:format=auto[vlg]")
        mapsrc = "[vlg]"

    if has_subs:
        sub_escaped = _escape_sub_path(srt_path)  # type: ignore[arg-type]
        filter_parts.append(f"{mapsrc}subtitles='{sub_escaped}'[vout]")
        mapsrc = "[vout]"

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", *inputs,
        "-filter_complex", filter_complex,
        "-map", mapsrc,
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-b:v", profile.video_bitrate,
        "-r", str(profile.fps),
        "-an", shlex.quote(video_out)
    ]
    # Ostatni element to ścieżka – jest już zacytowana, połączymy w string:
    cmd_str = " ".join(str(x) for x in cmd)
    ok, _ = _run(cmd_str)
    return ok and os.path.exists(video_out)

# funkcje pomocnicze xfade concat (przejścia między segmentami)
def _xfade_concat(paths: List[str], durations: List[float], out_path: str,
                  transition: str = "fade", duration: float = 0.5,
                  profile: Optional[RenderProfile] = None) -> bool:
    """
    Łączy N klipów bez audio z wykorzystaniem filtra xfade (crossfade).
    """
    if not paths:
        return False
    if len(paths) == 1:
        # Skopiuj wideo do out_path
        cmd = f"ffmpeg -y -i {shlex.quote(paths[0])} -c copy {shlex.quote(out_path)}"
        ok, _ = _run(cmd)
        return ok and os.path.exists(out_path)

    profile = profile or RenderProfile()
    # Budujemy wejścia
    inputs = []
    for p in paths:
        inputs += ["-i", p]

    # Budujemy łańcuch xfade
    # [0:v][1:v]xfade=...:offset=dur0-d, out [v01]
    # [v01][2:v]xfade=...:offset=(dur0+dur1)-2*d, out [v02]
    # ...
    filter_parts = []
    label_prev = "[0:v]"
    offset_acc = 0.0
    last_label = ""
    d = float(max(0.1, min(2.0, duration)))
    for i in range(1, len(paths)):
        cur_in = f"[{i}:v]"
        offset = max(0.0, offset_acc + max(0.0, durations[i-1]) - d)
        out_label = f"[v{i:02d}]"
        filter_parts.append(f"{label_prev}{cur_in}xfade=transition={transition}:duration={d:.3f}:offset={offset:.3f}{out_label}")
        label_prev = out_label
        offset_acc += max(0.0, durations[i-1]) - d
        last_label = out_label

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", last_label,
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-b:v", profile.video_bitrate,
        "-r", str(profile.fps),
        "-an", shlex.quote(out_path)
    ]
    cmd_str = " ".join(str(x) for x in cmd)
    ok, _ = _run(cmd_str)
    return ok and os.path.exists(out_path)

# [ADD] talk_to/news_to_video/main.py — oblicz efektywny czas wizualiów z uwzględnieniem xfade
def _effective_visual_duration(durations: List[float], use_xfade: bool, xfade_d: float) -> float:
    if not durations:
        return 0.0
    total = sum(durations)
    if use_xfade and len(durations) >= 2:
        total -= xfade_d * (len(durations) - 1)
    return max(0.0, total)


# funkcje pomocnicze prepare_media_segments: zwróć też durations
# NIE powielaj tutaj, tylko zakoduj i zwróć czasy
def prepare_media_segments(media_list: List[MediaItem], audio_duration: float,
                           profile: RenderProfile, work_dir: str,
                           default_img_duration: float = 4.5) -> Tuple[List[str], List[float], float]:
    """
    Encode each media item into an MP4 segment with uniform params.
    ZWRACA: (segment_paths, durations, total_duration) — BEZ powielania.
    Powielanie (jeśli potrzeba) nastąpi później z uwzględnieniem przejść (xfade overlap).
    """
    os.makedirs(work_dir, exist_ok=True)
    seg_paths: List[str] = []
    durations: List[float] = []

    idx = 1
    for m in media_list:
        seg_out = os.path.join(work_dir, f"seg_{idx:03d}.mp4")
        if m.type == "image":
            ok = _make_image_segment(m.src, default_img_duration, profile, seg_out)
            dur = default_img_duration if ok else 0.0
        elif m.type == "video":
            start = float(m.clip.get("start", 0.0)) if m.clip else None
            end = float(m.clip.get("end")) if (m.clip and "end" in m.clip) else None
            ok, dur = _make_video_segment(m.src, start, end, profile, seg_out)
        else:
            ok, dur = False, 0.0

        if ok and dur > 0:
            seg_paths.append(seg_out)
            durations.append(dur)
            idx += 1

    total = sum(durations)
    return seg_paths, durations, total



def prepare_media_segments_depr(media_list: List[MediaItem], audio_duration: float,
                           profile: RenderProfile, work_dir: str,
                           default_img_duration: float = 4.5) -> Tuple[List[str], float]:
    """
    Encode each media item into an MP4 segment with uniform params.
    Repeat the last segment until total video duration >= audio_duration.
    Returns (segment_paths, total_video_duration)
    """
    os.makedirs(work_dir, exist_ok=True)
    seg_paths: List[str] = []
    durations: List[float] = []

    idx = 1
    for m in media_list:
        seg_out = os.path.join(work_dir, f"seg_{idx:03d}.mp4")
        if m.type == "image":
            ok = _make_image_segment(m.src, default_img_duration, profile, seg_out)
            dur = default_img_duration if ok else 0.0
        elif m.type == "video":
            start = float(m.clip.get("start", 0.0)) if m.clip else None
            end = float(m.clip.get("end")) if (m.clip and "end" in m.clip) else None
            ok, dur = _make_video_segment(m.src, start, end, profile, seg_out)
        else:
            ok, dur = False, 0.0

        if ok and dur > 0:
            seg_paths.append(seg_out)
            durations.append(dur)
            idx += 1

    total = sum(durations)
    if seg_paths:
        # Repeat last segment to cover audio duration
        last_seg = seg_paths[-1]
        last_dur = durations[-1] if durations else 0.0
        while total < audio_duration and last_dur > 0:
            seg_paths.append(last_seg)  # reuse same file path in concat list
            durations.append(last_dur)
            total += last_dur

    return seg_paths, durations, total


# [MODIFY] talk_to/news_to_video/main.py — MUX: usuń '-shortest', aby nie ucinać audio
def _mux_video_audio(video_path: str, audio_path: str, profile: RenderProfile, out_path: str) -> bool:
    cmd = (
        f"ffmpeg -y -i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
        f"-c:v copy -c:a aac -b:a {profile.audio_bitrate} -movflags +faststart "
        f"{shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)


def _mux_video_audio_depr(video_path: str, audio_path: str, profile: RenderProfile, out_path: str) -> bool:
    cmd = (
        f"ffmpeg -y -i {shlex.quote(video_path)} -i {shlex.quote(audio_path)} "
        f"-c:v copy -c:a aac -b:a {profile.audio_bitrate} -shortest -movflags +faststart "
        f"{shlex.quote(out_path)}"
    )
    ok, _ = _run(cmd)
    return ok and os.path.exists(out_path)


def generate_srt(timeline: List[Dict], out_path: str) -> str:
    def fmt(ts: float) -> str:
        ms = int((ts - int(ts)) * 1000)
        h = int(ts // 3600)
        m = int((ts % 3600) // 60)
        s = int(ts % 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    lines = []
    for i, seg in enumerate(timeline, start=1):
        lines.append(str(i))
        lines.append(f"{fmt(seg['start'])} --> {fmt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path

# [ADD] talk_to/news_to_video/main.py — generator ASS z limitem 5 słów i skalą czcionki do rozdzielczości
def generate_ass_from_timeline(timeline: List[Dict], profile: RenderProfile, out_path: str,
                               max_words: int = 5, min_chunk_dur: float = 0.7) -> str:
    """
    Tworzy plik .ass z dialogami (max `max_words` na ekranie).
    Czas każdego segmentu jest dzielony na porcje równomiernie; jeśli segment za krótki,
    minimalne czasy chunków są zmniejszane, ale liczba słów nadal ≤ max_words.
    """
    def fmt_ass(ts: float) -> str:
        h = int(ts // 3600); m = int((ts % 3600) // 60); s = int(ts % 60)
        cs = int(round((ts - int(ts)) * 100))  # centiseconds
        return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

    fontsize = max(16, int(profile.height * 0.05))  # ~5% wysokości
    margin_v = max(30, int(profile.height * 0.08))

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {profile.width}",
        f"PlayResY: {profile.height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,Arial,{fontsize},&H00FFFFFF,&H000000FF,&H64000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,40,40,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    lines = []
    for seg in timeline:
        start, end = float(seg["start"]), float(seg["end"])
        dur = max(0.1, end - start)
        words = [w for w in seg["text"].split() if w]
        if not words:
            continue

        chunk_count = math.ceil(len(words) / max_words)
        # dopasuj minimalny czas chanku, jeśli segment jest krótki
        min_chunk = float(min_chunk_dur)
        if chunk_count * min_chunk > dur:
            min_chunk = dur / chunk_count

        # równy podział czasu
        base = dur / chunk_count

        # zbuduj paczki słów (≤ max_words)
        groups = [words[i:i + max_words] for i in range(0, len(words), max_words)]
        for i, grp in enumerate(groups):
            # czas chunku
            cstart = start + i * base
            cend = start + (i + 1) * base
            # gwarancja min czasu (zawężenie ostatniego, jeżeli trzeba)
            if (cend - cstart) < min_chunk:
                cend = cstart + min_chunk
                if cend > end:
                    cend = end
            # łamanie na dwie linie: 3 + 2 słowa (jeśli >3)
            if len(grp) > 3:
                split = math.ceil(len(grp) / 2)
                text = " ".join(grp[:split]) + r"\N" + " ".join(grp[split:])
            else:
                text = " ".join(grp)
            lines.append(f"Dialogue: 0,{fmt_ass(cstart)},{fmt_ass(cend)},Default,,0,0,0,,{text}")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(header + lines))
    return out_path
