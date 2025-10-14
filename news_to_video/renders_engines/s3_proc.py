# ver. 1.5
import json
import os
import tempfile
from flask import current_app
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from news_to_video.config import (
    BASE_DIR, 
    VIDEO_S3_PREFIX,
    VIDEO_S3_BUCKET,
    VIDEO_S3_BASE_URL
)
from apps_utils.s3_utils import (
    s3_session
)
from news_to_video.renders_engines.helpers_proc import (
    _to_iso_utc,
    _parse_dt_any,
    _prefix_join, 
    _guess_mime,
    _rel_to_base, 
    _find_project_root, 
    _project_folder_and_date,
    _json_default,
    detect_media_type
)

from loggers import news_to_video_logger
from urllib.parse import urlparse


# KONFIG S3 + helpery do pracy z prefixami i listowaniem S3 
def save_json(path: str | os.PathLike, data: dict) -> None:
    """
    Zapis JSON z:
      - serializacją datetime/date/Path (ISO 8601 UTC),
      - zapisem atomowym (tmp → os.replace), żeby nie zostawiać uszkodzonych plików.
    """
    path = str(path)
    dname = os.path.dirname(path) or "."
    os.makedirs(dname, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_manifest_", dir=dname, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_default)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        # w razie błędu usuń plik tymczasowy
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

def save_json_depr(path: str, data: Dict):
    # print(f'\n\t\tSTART ==> save_json({path}, {type(data)}) ')
    # os.makedirs(os.path.dirname(path), exist_ok=True)
    # with open(path, "w", encoding="utf-8") as f:
    #     json.dump(data, f, ensure_ascii=False, indent=2)
    # # mirror to S3 (jeśli skonfigurowane i ścieżka w BASE_DIR)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    print(f'\n\t\t[save_json] path ==> {path} ===> data type ==> {type(data)}\n\n')
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if _s3_ready():
        news_to_video_logger.info(f'[save_json] path==>{path}')
        key = _s3_key_for_local(path)
        news_to_video_logger.info(f'[save_json] key==>{key}')
        if key:
            url = _s3_upload_file(path, key, content_type="application/json")
            # print(f'\n\t\t[save_json] url ==> {url}\n\n')
    return False

def load_json(path: str | os.PathLike) -> Optional[dict]:
    """
    Bezpieczny odczyt JSON z wyraźnym logiem przy błędach parsowania.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        try:
            with open(path, "r", encoding="utf-8") as f:
                preview = f.read(300)
        except Exception:
            preview = "<unreadable>"
        news_to_video_logger.error("[load_json] JSONDecodeError %s: %s\nPreview: %s", path, e, preview)
        return None
    except Exception as e:
        news_to_video_logger.error("[load_json] Error %s: %s", path, e)
        return None

def load_json_depr(path: str) -> Dict:
    # fallback do S3 (gdy brak lokalnego)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    data = _s3_download_json(path)
    if data is not None:
        return data

    # ostatecznie błąd
    raise FileNotFoundError(f"JSON not found locally or on S3: {path}")

# -----------------------------
# Local helpers (robust JSON load)
# -----------------------------
def _safe_load_manifest(mpath: str):
    """
    Odczytaj manifest i zwróć dict albo None.
    Nie podnosi wyjątków przy uszkodzonym JSON – load_json już loguje błąd.
    """
    try:
        m = load_json(mpath)
        return m if isinstance(m, dict) else None
    except Exception:
        return None

def s3_media_tree() -> Dict[str, Any]:
    """
    Zwraca drzewo katalogów z S3 wg struktury:
    <VIDEO_S3_PREFIX>/projects/<YYYY>/<MM>/<folder>/
    Sortowanie:
      - lata malejąco (bieżący rok -> wcześniejsze)
      - miesiące malejąco (bieżący miesiąc -> wcześniejsze)
      - foldery alfabetycznie rosnąco

    Dla każdego folderu dołącza:
      - preview_url (MP4; preferuje output_16x9.mp4)
      - manifest (tytuł, status)
      - outputs (słownik)
      - tts (provider, voice, speed, language)
    """
    if not _s3_ready():
        raise RuntimeError("S3 is not configured (s3_session/VIDEO_S3_BUCKET).")

    s3, default_bucket, region = s3_session()
    bucket = _s3_env_bucket() or default_bucket
    base = _s3_env_prefix()
    projects_root = base if base.rstrip("/").endswith("projects") else _prefix_join(base, "projects")
    # print('projects_root projects_root projects_root')
    # --- Lata ---
    year_prefixes = _s3_list_common_prefixes(s3, bucket, _prefix_join(projects_root))
    years: List[int] = []
    year_map: Dict[int, str] = {}
    for yp in year_prefixes:
        name = yp.strip("/").split("/")[-1]
        if name.isdigit() and len(name) == 4:
            yi = int(name)
            years.append(yi)
            year_map[yi] = yp
    years.sort(reverse=True)
    
    tree: Dict[str, Any] = {
        "bucket": bucket,
        "base_prefix": projects_root if projects_root.endswith("/") else projects_root + "/",
        "years": [],
    }

    for y in years:
        y_pref = year_map[y]
        # --- Miesiące ---
        month_prefixes = _s3_list_common_prefixes(s3, bucket, y_pref)
        months: List[int] = []
        month_map: Dict[int, str] = {}
        for mp in month_prefixes:
            name = mp.strip("/").split("/")[-1]
            if name.isdigit():
                try:
                    mi = int(name)
                    if 1 <= mi <= 12:
                        months.append(mi)
                        month_map[mi] = mp
                except Exception:
                    pass
        months.sort(reverse=True)
        
        month_nodes = []
        for m in months:
            m_pref = month_map[m]
            # --- Foldery końcowe ---
            folder_prefixes = _s3_list_common_prefixes(s3, bucket, m_pref)
            entries = []
            for fp in folder_prefixes:
                folder_name = fp.strip("/").split("/")[-1]
                if not folder_name:
                    continue

                created_src = None
                # manifest.json w folderze projektu
                manifest_key = _prefix_join(fp, "manifest.json")
                manifest = _s3_get_json_by_key(s3, bucket, manifest_key.strip("/")) or {}
                # print(f'created_at==>{manifest.get("created_at")}')

                # 1) preferuj datę z manifestu, jeśli jest
                if manifest:
                    created_src = manifest.get('created_at') or manifest.get('created') \
                                or manifest.get('datetime') or manifest.get('date')

                # 2) fallback do S3 LastModified z obiektu list_objects_v2
                if not created_src and "LastModified" in manifest:
                    created_src = manifest["LastModified"]

                # 2) fallback do najświeższego LastModified w folderze
                if not created_src:
                    created_src = _s3_latest_last_modified(s3, bucket, fp)
  
                # Zapisz w entries spójne ISO (dla UI/logów), ale sortowanie i tak działa na obu formach:
                created_iso = _to_iso_utc(created_src)  # może zwrócić None, gdy całkiem nieczytelne

                # outputs/tts z manifestu (jeśli brak, puste)
                project_id = manifest.get("project_id", {}) or {}
                created_at = manifest.get("created_at", {}) or {}
                outputs = manifest.get("outputs", {}) or {}
                payload = manifest.get("payload", {}) or {}
                tts = payload.get("tts", {}) or {}
                title = manifest.get("title") or folder_name
                status = manifest.get("status") or "unknown"

                # podgląd wideo: preferuj URL-e z manifestu, potem skanuj MP4 w katalogu
                preview_url = (
                    outputs.get("mp4_16x9_url")
                    or outputs.get("mp4_1x1_url")
                    or outputs.get("mp4_9x16_url")
                    or ""
                )
                if not preview_url:
                    mp4_keys = _s3_list_videos_in_prefix(s3, bucket, fp)
                    if mp4_keys:
                        preview_url = _s3_build_url(bucket, region, mp4_keys[0])

                entry = {
                    "folder": folder_name,
                    "project_id": manifest.get("project_id") or folder_name,
                    "prefix": fp,  # np. 'londynek/video/projects/2025/09/proj-.../'
                    "manifest_key": manifest_key,  # pełna ścieżka do manifestu, jeśli masz
                    "created_at": created_iso if created_iso else created_src,  # ISO string lub datetime
                    "preview_url": preview_url,
                    "title": title,
                    "status": status,
                    "outputs": outputs,
                    "tts": {
                        "provider": tts.get("provider"),
                        "voice": tts.get("voice"),
                        "speed": tts.get("speed"),
                        "language": tts.get("language"),
                    },
                }
                entries.append(entry)

            # print('created_at:', entry.get("created_at"))

            # alfabetycznie po nazwie folderu
            # entries.sort(key=lambda e: e["folder"].lower())
            # po dacie create
            # entries.sort(key=lambda e: datetime.fromisoformat(e["created_at"]), reverse=True)
            # entries.sort(key=lambda e: datetime.fromisoformat(entry["created_at"]), reverse=True)

            # sortuj malejąco po dacie utworzenia (obsługa str/datetime przez _parse_dt_any)
            try:
                # sortuj malejąco po dacie utworzenia (zawsze tz-aware dzięki _parse_dt_any)
                entries.sort(key=lambda e: _parse_dt_any(e.get("created_at")), reverse=True)
            except Exception as sort_err:
                print(f'❌  Sort error: {sort_err}')

            month_nodes.append({
                "month": f"{m:02d}",
                "prefix": m_pref,
                "entries": entries,
            })

        tree["years"].append({
            "year": str(y),
            "prefix": y_pref,
            "months": month_nodes,
        })

    return tree

def _format_size(num) -> Optional[str]:
    try:
        value = float(num)
    except (TypeError, ValueError):
        return None
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return None

def _parse_gallery_location(location: str, default_bucket: str) -> Tuple[str, str]:
    """
    Zamienia wejściowy adres (URL, s3:// lub klucz) na parę (bucket, prefix).
    """
    if not location:
        raise ValueError("Pusty adres S3.")

    raw = location.strip()
    if not raw:
        raise ValueError("Pusty adres S3.")

    # s3://bucket/key
    if raw.lower().startswith("s3://"):
        parsed = urlparse(raw)
        bucket = parsed.netloc or default_bucket
        prefix = parsed.path.lstrip("/")
        return (bucket or default_bucket, prefix)

    # http(s)://...
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        path = parsed.path.lstrip("/")
        bucket = default_bucket

        # https://bucket.s3.region.amazonaws.com/key...
        if ".s3." in parsed.netloc:
            bucket_part = parsed.netloc.split(".s3.", 1)[0]
            if bucket_part:
                bucket = bucket_part
        else:
            # dopasuj do VIDEO_S3_BASE_URL
            if VIDEO_S3_BASE_URL:
                base_parsed = urlparse(VIDEO_S3_BASE_URL if "://" in VIDEO_S3_BASE_URL else f"https://{VIDEO_S3_BASE_URL}")
                base_path = base_parsed.path.lstrip("/")
                if parsed.netloc == base_parsed.netloc and base_path and path.startswith(base_path):
                    path = path[len(base_path):].lstrip("/")

        return (bucket or default_bucket, path)

    # traktuj jako klucz względem bucket
    return (default_bucket, raw.lstrip("/"))


def _list_media_under_prefix(s3, bucket: str, prefix: str, region: str) -> List[Dict[str, Any]]:
    """
    Zwraca listę zasobów multimedialnych (image/video) pod zadanym prefiksem.
    """
    if not prefix:
        return []

    normalized_prefix = prefix
    items: List[Dict[str, Any]] = []
    token = None
    base_url = _s3_env_base_url(bucket, region)

    while True:
        kwargs = {
            "Bucket": bucket,
            "Prefix": normalized_prefix,
            "MaxKeys": 1000,
        }
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            key = obj.get("Key", "")
            if not key or key.endswith("/"):
                continue
            media_type = detect_media_type(key)
            if not media_type:
                continue
            items.append({
                "key": key,
                "url": f"{base_url}/{key}",
                "type": media_type,
                "size": obj.get("Size"),
                "last_modified": _to_iso_utc(obj.get("LastModified")),
                "size_human": _format_size(obj.get("Size")),
            })
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    return items


def fetch_gallery_entries(locations: List[str], page: int = 1, per_page: int = 24) -> List[Dict[str, Any]]:
    """
    Dla podanych adresów S3 zwraca listę zasobów do wykorzystania w galerii.
    """
    if not _s3_ready():
        raise RuntimeError("Konfiguracja S3 jest niedostępna.")

    if isinstance(locations, str):
        locations = [locations]

    s3, default_bucket, region = s3_session()
    base_prefix = _s3_env_prefix()

    results: List[Dict[str, Any]] = []

    for loc in locations:
        entry: Dict[str, Any] = {
            "input": loc,
            "items": [],
            "bucket": None,
            "prefix": None,
            "error": None,
            "warning": None,
            "count": 0,
        }
        results.append(entry)

        try:
            bucket, prefix = _parse_gallery_location(loc, default_bucket)
            entry["bucket"] = bucket
            entry["requested_prefix"] = prefix

            items = _list_media_under_prefix(s3, bucket, prefix, region)

            # jeśli brak wyników, spróbuj z prefiksem bazowym
            if not items and base_prefix and not prefix.startswith(base_prefix):
                combined = f"{base_prefix}/{prefix}".lstrip("/")
                items = _list_media_under_prefix(s3, bucket, combined, region)
                if items:
                    entry["prefix"] = combined
                else:
                    entry["prefix"] = prefix
            else:
                entry["prefix"] = prefix

            total = len(items)
            # Oblicz stronicowanie (1-based)
            page = max(1, int(page or 1))
            per_page = max(1, int(per_page or 24))
            start = (page - 1) * per_page
            end = start + per_page
            entry["items"] = items[start:end]
            entry["count"] = total
            entry["page"] = page
            entry["per_page"] = per_page
            entry["pages"] = (total + per_page - 1) // per_page if total else 1

            # Lista pod-folderów (CommonPrefixes) dla łatwego przeglądania
            try:
                folder_prefix = entry.get("prefix") or prefix
                folders = _s3_list_common_prefixes(s3, bucket, folder_prefix)
                entry["folders"] = folders
                # Zbuduj URL-e folderów, preferując domenę z oryginalnego wejścia (jeśli HTTP/S),
                # w przeciwnym razie użyj domyślnej bazy S3 (bucket/region lub VIDEO_S3_BASE_URL)
                base_url = None
                _loc = (loc or "").strip()
                if _loc.startswith("http://") or _loc.startswith("https://"):
                    _p = urlparse(_loc)
                    base_url = f"{_p.scheme}://{_p.netloc}"
                else:
                    base_url = _s3_env_base_url(bucket, region)

                folder_infos = []
                for fkey in folders:
                    # Uniknij podwójnych //
                    furl = f"{base_url}/{fkey.lstrip('/')}"
                    folder_infos.append({"key": fkey, "url": furl})
                entry["folders_info"] = folder_infos
                if folders and not items:
                    entry["warning"] = (entry.get("warning") or "") + (" " if entry.get("warning") else "") + f"Znaleziono {len(folders)} podfolderów."
            except Exception as _:
                entry["folders"] = []
                entry["folders_info"] = []

            if not items:
                entry["warning"] = "Brak rozpoznanych plików graficznych lub wideo pod wskazanym prefiksem."

        except Exception as exc:
            entry["error"] = str(exc)

    return results

def _s3_env_bucket(s3_bucket=None) -> Optional[str]:
    """Resolve S3 bucket name from multiple env sources.

    Prefer explicit `VIDEO_S3_BUCKET` used by news_to_video. If not set,
    fall back to the more generic `AWS_S3_BUCKET` used in other modules,
    or the provided `s3_bucket` argument.
    """
    return (VIDEO_S3_BUCKET or os.getenv("AWS_S3_BUCKET") or s3_bucket or None)

def _s3_ready() -> bool:
    # s3_session is always callable; check that some bucket is configured
    # either via VIDEO_S3_BUCKET or AWS_S3_BUCKET.
    return bool(_s3_env_bucket())

def _s3_key_for_local(abs_path: str) -> Optional[str]:
    """
    Buduje klucz S3. Jeśli plik należy do projektu (ma ancestor z manifest.json),
    to klucz ma postać:
      <VIDEO_S3_PREFIX>/projects/<YYYY>/<MM>/<folder>/<rel_od_project_dir>
    W przeciwnym razie: <VIDEO_S3_PREFIX>/<rel_od_BASE_DIR>
    """
    # mapuj /tmp → BASE_DIR (jak było dotąd)
    abs_path = abs_path.replace("/tmp", BASE_DIR)
    rel_base = _rel_to_base(abs_path)
    if not rel_base:
        return None

    # Czy to plik z projektu?
    pdir = _find_project_root(abs_path)
    if pdir:
        project_folder_name, created = _project_folder_and_date(pdir)
        yyyy = created.strftime("%Y")
        mm   = created.strftime("%m")
        # relatywna ścieżka w obrębie projektu
        rel_in_project = os.path.relpath(os.path.abspath(abs_path), os.path.abspath(pdir)).replace("\\", "/")
        prefix = _s3_projects_base(_s3_env_prefix())  # "<PREFIX>/projects"
        print('\n\t\t[_s3_key_for_local] * * * * * * * * * * * * *')
        print(f"\t\t[_s3_key_for_local] {prefix}/{yyyy}/{mm}/{project_folder_name}/".replace("//", "/"))
        print('\t\t[_s3_key_for_local] * * * * * * * * * * * * *\n')
        
        # return f"{prefix}/{yyyy}/{mm}/{project_folder_name}/".replace("//", "/")
        # ZWRÓĆ pełny KLUCZ do pliku w obrębie projektu (a nie sam katalog)
        return f"{prefix}/{yyyy}/{mm}/{project_folder_name}/{rel_in_project}".replace("//", "/")

    # Fallback — poza projektem zostaw dotychczasowe zachowanie
    prefix = _s3_env_prefix()
    return f"{prefix}/{rel_base}".replace("//", "/")

def _s3_list_common_prefixes(s3, bucket: str, prefix: str, delimiter: str = "/") -> List[str]:
    """
    Zwraca listę "folderów" (CommonPrefixes) dla danego prefixu.
    Obsługuje paginację ListObjectsV2.
    """
    prefixes: List[str] = []
    continuation: Optional[str] = None
    while True:
        kwargs = {
            "Bucket": bucket,
            "Prefix": prefix,
            "Delimiter": delimiter,
            "MaxKeys": 1000,
        }
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = s3.list_objects_v2(**kwargs)
        for cp in resp.get("CommonPrefixes", []):
            p = cp.get("Prefix")
            if p:
                prefixes.append(p)
        if resp.get("IsTruncated"):
            continuation = resp.get("NextContinuationToken")
        else:
            break
    return prefixes

# helper do pobrania JSON-a z S3 po kluczu
def _s3_get_json_by_key(s3, bucket: str, key: str) -> Optional[Dict[str, Any]]:
    # print(f"\n\t\tSTART ==> _s3_get_json_by_key(s3, {bucket}, {key})\n\t{key.split('/')[-2:]}")
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        json_data = json.loads(data.decode("utf-8"))
        # print(f'\n\t\tEND ==> _s3_get_json_by_key() ==> json_data type={type(json_data)}')
        return json_data
     
    except Exception as e:
        news_to_video_logger.info("❌ [_s3_get_json_by_key] get manifest failed bucket=%s key=%s err=%s", bucket, key, str(e))
        return None


def _s3_latest_last_modified(s3, bucket: str, prefix: str) -> Optional[datetime]:
    """
    Zwróć najnowszy LastModified (datetime) spośród obiektów pod danym prefixem.
    Gdy brak obiektów – None.
    """
    latest = None
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for it in resp.get("Contents", []):
            lm = it.get("LastModified")
            if lm and (latest is None or lm > latest):
                latest = lm
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return latest


def _s3_env_prefix() -> str:
    # katalog bazowy dla projektu w bucket
    return (VIDEO_S3_PREFIX or "londynek/video").strip("/")

def s3_project_prefix(project_id: str, released_at: datetime | None = None) -> str:
    # jeśli masz datę z manifestu → użyj jej, inaczej teraz()
    dt = released_at or datetime.utcnow()
    yyyy = dt.strftime("%Y")
    mm = dt.strftime("%m")
    return f"{_s3_env_prefix()}/projects/{yyyy}/{mm}/{project_id}/"

def _s3_env_base_url(bucket: str, region: str) -> str:
    # preferuj VIDEO_S3_BASE_URL, w przeciwnym razie standardowy endpoint
    base = VIDEO_S3_BASE_URL
    if base:
        return base.rstrip("/")
    return f"https://{bucket}.s3.{region}.amazonaws.com"

# bezpieczny upload do s3 -> _s3_upload_file
def _s3_upload_file(local_path: str, s3_key: str, content_type: Optional[str] = None) -> Optional[str]:
    news_to_video_logger.info(f'\n\t\tSTART ==> _s3_upload_file(local_path: {local_path}, s3_key: {s3_key}, content_type: {content_type})\n')
    """Upload via s3_session(); ustawia public-read; zwraca publiczny URL. Logowanie bez f-stringów z nawiasami."""
    if not _s3_ready():
        return None
    try:
        s3, default_bucket, region = s3_session()
        bucket = _s3_env_bucket() or default_bucket
        base_url = _s3_env_base_url(bucket, region)
        content_type = content_type or _guess_mime(local_path)
    except Exception as e:
        # nie używamy f-stringa z nawiasami klamrowymi
        news_to_video_logger.info("❌ [_s3_upload_file] get data ERROR path=%s key=%s err=%s", local_path, s3_key, str(e))
        return None

    try:
        # INFO: używamy stylu printf loggera, żeby uniknąć problemów z format specifier w f-stringach
        news_to_video_logger.info("[_s3_upload_file] s3.upload_file start: \npath=%s \nkey=%s \nctype=%s \nbucket=%s",
                                  local_path, s3_key, content_type, bucket)

        s3.upload_file(
            local_path,
            bucket,
            s3_key,
            ExtraArgs={"ContentType": content_type, "ACL": "public-read"}
        )

        url = f"{base_url}/{s3_key}"
        news_to_video_logger.info("[_s3_upload_file] upload_file done: url=%s", url)
        return url

    except Exception as e:
        # nie używamy f-stringa z nawiasami klamrowymi
        news_to_video_logger.info("❌ [_s3_upload_file] upload_file ERROR path=%s key=%s err=%s", local_path, s3_key, str(e))
        return None

def _s3_download_json(abs_path: str) -> Optional[dict]:
    print(f'\n\t\tSTART ==> _s3_download_json({abs_path})')
    """Pobierz JSON z S3 na podstawie ścieżki względem BASE_DIR."""
    if not _s3_ready():
        return None
    key = _s3_key_for_local(abs_path)
    if not key:
        return None

    try:
        s3, default_bucket, region = s3_session()
        bucket = _s3_env_bucket() or default_bucket
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        return json.loads(data.decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        news_to_video_logger.info("[_s3_download_json] manifest not found bucket=%s key=%s", bucket, key)
        return None
    except Exception as e:
        news_to_video_logger.error("❌ [_s3_download_json] manifest read error bucket=%s key=%s err=%s", bucket, key, str(e))
        return None
    
# budowa URL i wyszukiwanie MP4 w folderze
def _s3_build_url(bucket: str, region: str, key: str) -> str:
    base = _s3_env_base_url(bucket, region)
    return f"{base}/{key.lstrip('/')}"

def _s3_list_videos_in_prefix(s3, bucket: str, prefix: str) -> List[str]:
    """Zwraca listę kluczy MP4 w danym 'katalogu' (rekurencyjnie pod prefixem)."""
    keys: List[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            k = obj.get("Key", "")
            if k.lower().endswith(".mp4"):
                keys.append(k)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    # preferuj „output_16x9.mp4”, potem inne uporządkowane alfabetycznie
    keys.sort(key=lambda k: (0 if k.endswith("output_16x9.mp4") else 1, k.lower()))
    return keys

# helper: synchronizacja wyników do S3 przed usunięciem lokalnym
def sync_project_to_s3(project_dir: str) -> bool:
    print(f'\n\t\tSTART ==> sync_project_to_s3({project_dir})')
    """
    Upewnia się, że kluczowe pliki projektu są w S3:
    - manifest.json
    - outputs: mp4_* / srt / ass / audio
    Nie kasuje lokalnych plików — tylko dosyła brakujące do S3.
    """
    if not _s3_ready():
        news_to_video_logger.info("[S3] not configured; skip sync")
        return False

    manifest_path = os.path.join(project_dir, "manifest.json")
    try:
        m = load_json(manifest_path)
    except Exception as e:
        news_to_video_logger.info("❌ [S3][sync] cannot load manifest (%s): %s", manifest_path, str(e))
        return False

    outs = m.get("outputs", {}) or {}

    # manifest.json
    m_key = _s3_key_for_local(manifest_path)
    if m_key:
        url = _s3_upload_file(manifest_path, m_key, content_type="application/json")

    # files to ensure in S3 -> (local_key, content_type, url_key)
    items = []
    for k, ctype, urlk in [
        ("mp4_16x9", "video/mp4", "mp4_16x9_url"),
        ("mp4_1x1",  "video/mp4", "mp4_1x1_url"),
        ("mp4_9x16", "video/mp4", "mp4_9x16_url"),
        ("srt",      "application/x-subrip", "srt_url"),
        ("ass",      "text/plain", "ass_url"),
        ("audio",    "audio/mpeg", "audio_url"),
    ]:
        if outs.get(k):
            items.append((outs[k], ctype, urlk))

    updated = False
    for local_path, ctype, url_key in items:
        if outs.get(url_key):
            continue  # już jest URL
        key = _s3_key_for_local(local_path)
        if not key:
            continue
        url = _s3_upload_file(local_path, key, content_type=ctype)
        if url:
            outs[url_key] = url
            updated = True

    if updated:
        m["outputs"] = outs
        save_json(manifest_path, m)  # mirror to S3 też się wykona
        # jeszcze raz dopychamy manifest.json (na wszelki wypadek)
        m_key = _s3_key_for_local(manifest_path)
        if m_key:
            url = _s3_upload_file(manifest_path, m_key, content_type="application/json")
    return True

def _s3_projects_base(prefix: str) -> str:
    """Zadbaj, by bazą był <VIDEO_S3_PREFIX>/projects/ (bez podwójnych //)."""
    base = prefix.strip("/")
    return base if base.endswith("projects") else f"{base}/projects"
