# ver 1.4
# ver. 1.4
import json
import os
from flask import current_app
from pathlib import Path
from typing import Dict, Any, List, Optional, List, Tuple
from datetime import datetime, timezone, date
# sentinel dla błędnych dat – "minus nieskończoność"
_DT_NEG = datetime(1970, 1, 1, tzinfo=timezone.utc)
from urllib.parse import urlparse, parse_qs
from pathlib import PurePosixPath

from loggers import news_to_video_logger
from news_to_video.config import (
    BASE_DIR, 
    IMG_EXT, 
    VID_EXT
)

def detect_media_type(path_or_url: str) -> Optional[str]:
    """
    Rozpoznaje typ pliku (image|video) dla ścieżek lokalnych i URL-i z dodatkowymi parametrami.
    Nie modyfikuje oryginalnego URL-a — jedynie analizuje część path i query.
    Obsługuje podwójne rozszerzenia (np. .jpg.webp) i data:URI.
    """
    if not path_or_url:
        return None
    s = path_or_url.strip()

    # data URI
    if s.startswith("data:"):
        header = s[5:].split(";", 1)[0].lower()
        if header.startswith("image/"):
            return "image"
        if header.startswith("video/"):
            return "video"

    # parse URL (lub potraktuj jako ścieżkę)
    try:
        u = urlparse(s)
        path = u.path or s
        query = u.query or ""
    except Exception:
        path, query = s, ""

    # usuń ewentualne pozostałości ?/# w path (na wszelki wypadek)
    path = path.split("?", 1)[0].split("#", 1)[0]


    # sprawdź sufiksy ścieżki (obsługa podwójnych rozszerzeń, np. .jpg.webp)
    suffixes = [ext.lower() for ext in PurePosixPath(path).suffixes]
    for ext in reversed(suffixes):
        if ext in IMG_EXT:
            return "image"
        if ext in VID_EXT:
            return "video"

    # spróbuj odczytać format z query: ?format=webp / ?ext=mp4
    try:
        params = parse_qs(query.lower())
        fmt_vals = (params.get("format") or params.get("ext") or [])
        if fmt_vals:
            f = fmt_vals[0].strip(".").lower()
            if f in {e.strip(".") for e in IMG_EXT}:
                return "image"
            if f in {e.strip(".") for e in VID_EXT}:
                return "video"
        # szybki fallback na tekstowe wystąpienia w query
        q = query.lower()
        if any(k in q for k in ("format=jpg", "format=jpeg", "format=png", "format=webp", "ext=jpg", "ext=jpeg", "ext=png", "ext=webp")):
            return "image"
        if any(k in q for k in ("format=mp4", "format=webm", "format=mkv", "ext=mp4", "ext=webm", "ext=mkv")):
            return "video"
    except Exception:
        pass

    # ostateczny fallback: szukaj rozszerzenia w całym path
    base = path.lower()
    if any(e in base for e in IMG_EXT):
        return "image"
    if any(e in base for e in VID_EXT):
        return "video"

    return None

def _validate_manifest(manifest: dict) -> bool:
    """Zwraca True jeśli manifest jest poprawny, False jeśli nie."""
    if not isinstance(manifest, dict):
        return False
    if not manifest.get("project_id"):
        news_to_video_logger.error("[manifest] Brak project_id")
        return False
    if not manifest.get("payload"):
        news_to_video_logger.error("[manifest] Brak payload")
        return False
    try:
        json.dumps(manifest)  # czy serializowalny
    except Exception as e:
        news_to_video_logger.error("[manifest] Błąd serializacji JSON: %s", e)
        return False
    return True

def _to_iso_utc(v) -> Optional[str]:
    """Zamień dowolną reprezentację czasu na ISO 8601 w UTC; w razie porażki → None."""
    dt = _parse_dt_any(v)
    if dt == _DT_NEG:
        return None
    return dt.isoformat()

# -------------------------
# JSON helpers
# -------------------------
def _json_default(o):
    """Serializacja nietypowych typów do JSON (datetime/date/Path/itp.)."""
    if isinstance(o, datetime):
        # zawsze ISO UTC
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        return o.astimezone(timezone.utc).isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    # ostatnia deska ratunku – konwersja do str, żeby nie wysypać całego dumpa
    return str(o)

def _parse_dt_any(v) -> datetime:
    """
    Przyjmuje: datetime | ISO string ('Z' lub offset) | None/other.
    Zwraca: datetime tz-aware w UTC. Gdy nie parsuje – _DT_NEG.
    """
    dt = None
    if isinstance(v, datetime):
        dt = v
    elif isinstance(v, str):
        s = v.strip()
        # toleruj nadmiarowe 'Z' lub brak offsetu
        # normalizuj 'Z' -> '+00:00'
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            dt = None
    # jeśli dalej brak – zwróć sentinel
    if dt is None:
        return _DT_NEG
    # zapewnij tz-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # sprowadź do UTC
    return dt.astimezone(timezone.utc)

def _parse_dt_any_depr(v):
    """
    Przyjmuje: datetime | ISO string (z 'Z' lub offsetem) | None | cokolwiek.
    Zwraca: datetime (na potrzeby sortowania), gdy się nie uda -> datetime.min
    """
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        s = v.strip()
        # Python nie lubi sufiksu 'Z' → zastąp offsetem
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        try:
            return datetime.fromisoformat(s)
        except Exception:
            pass
    return datetime.min

def _prefix_join(*parts: str) -> str:
    p = "/".join([str(x).strip("/") for x in parts if x is not None and str(x).strip("/") != ""])
    return p + ("/" if p and not p.endswith("/") else "")

def _guess_mime(path: str, default: str = "application/octet-stream") -> str:
    # rozszerz znane typy
    ext = (os.path.splitext(path)[1] or "").lower()
    if ext == ".mp4":
        return "video/mp4"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".srt":
        return "application/x-subrip"
    if ext == ".ass":
        return "text/plain"
    if ext in {".json"}:
        return "application/json"
    ctype, _ = mimetypes.guess_type(path)
    return ctype or default

def _rel_to_base(abs_path: str) -> Optional[str]:
    try:
        abs_base = os.path.abspath(BASE_DIR)
        ap = os.path.abspath(abs_path)
        if not ap.startswith(abs_base):
            return None
        rel = os.path.relpath(ap, abs_base).replace("\\", "/")
        return rel
    except Exception:
        return None

def _find_project_root(start_abs_path: str) -> Optional[str]:
    """Idź w górę od ścieżki i znajdź katalog projektu (taki, który zawiera manifest.json)."""
    cur = os.path.abspath(start_abs_path)
    base = os.path.abspath(BASE_DIR)
    while True:
        if os.path.isfile(os.path.join(cur, "manifest.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur or not parent.startswith(base):
            return None
        cur = parent

def _project_folder_and_date(project_dir: str) -> Tuple[str, datetime]:
    """Zwróć (folder, data_utw) dla projektu na podstawie manifestu lub mtime katalogu."""
    folder = os.path.basename(project_dir.rstrip("/"))
    mpath = os.path.join(project_dir, "manifest.json")
    created = None
    try:
        if os.path.isfile(mpath):
            with open(mpath, "r", encoding="utf-8") as f:
                m = json.load(f)
            # preferuj created_at z manifestu
            created = _parse_iso8601(m.get("created_at")) or created
            folder = m.get("project_id") or folder
    except Exception:
        pass
    if not created:
        try:
            ts = os.path.getctime(project_dir)
            created = datetime.utcfromtimestamp(ts)
        except Exception:
            created = datetime.utcnow()
    return folder, created

