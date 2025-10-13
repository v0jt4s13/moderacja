import boto3
import os
import json
import re
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from botocore.exceptions import ClientError
from apps_utils.openai_utils import generate_audio_from_text
from apps_utils.debug_utils import printLog
from apps_utils.main_function import sort_urls_by_paragraph
from config import CACHE_TTL_SECONDS, S3_PREFIX, TEST_AUDIO_S3_KEY, PARAGRAPHS_N
import threading
import time

_audio_index_cache = {}  # klucz: (year, month, lang) -> {"ts": float, "data": dict}
_audio_index_lock = threading.Lock()

def s3_session() -> dict:
    try:
        region_name=os.getenv("S3_REGION")
        bucket = os.getenv("AWS_S3_BUCKET")
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name
        )
        
    except Exception as e:
        print(f"❌ Błąd przy próbie połączenia do S3: {e}")
        return {}
    return s3, bucket, region_name

def upload_to_s3(file_path, s3_key):
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")

        print(f"📤 Próba zapisu do S3:", end=' ')
        print(f"   🔹 Plik lokalny: {file_path}", end='')
        print(f"   🔹 Bucket: {bucket}", end='')
        print(f"   🔹 Klucz (ścieżka): {s3_key}", end='')

        s3.upload_file(
            file_path,
            bucket, 
            s3_key,
            ExtraArgs={"ContentType": "audio/mpeg", "ACL": "public-read"}
        )


        url = f"https://{bucket}.s3.{os.getenv('S3_REGION')}.amazonaws.com/{s3_key}"
        print(f"✅ Plik zapisany w S3: {url}")
        return url

    except Exception as e:
        print(f"❌ Błąd przy zapisie do S3: {e}")
        return None

def upload_audio_to_s3(article_id, article_yy, article_mm, lang, options, paragraphs):
    audio_urls = []
    paragraphs_to_generate = None
    if PARAGRAPHS_N == 0:
        paragraphs_to_generate = paragraphs
    if PARAGRAPHS_N > 0:
        paragraphs_to_generate = paragraphs[:PARAGRAPHS_N]

    if paragraphs_to_generate:
        for i, paragraph in enumerate(paragraphs_to_generate):
            audio_file, used_settings = generate_audio_from_text(paragraph, f"{article_id}_{lang}_p{i+1}", options)

            if audio_file:
                s3_key = f"{S3_PREFIX}{article_yy}/{article_mm}/{article_id}_{lang}_p{i+1}.mp3"
                s3_url = upload_to_s3(audio_file, s3_key)
                audio_urls.append(s3_url)
    
    # 🔽 sortowanie przed zwrotem
    audio_urls = sort_urls_by_paragraph(audio_urls)

    return used_settings, audio_urls

def get_s3_url(s3_key):
    bucket = os.getenv("AWS_S3_BUCKET")
    region = os.getenv("S3_REGION")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"

def download_from_s3(s3_key, local_path):
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")

        s3.download_file(bucket, s3_key, local_path)
        return True
    except Exception as e:
        print(f"❌ [download_from_s3] Download error: {e}")
        return False

def upload_json_to_s3(data: dict, s3_key: str):
    
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")
        
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tmpfile:
            json.dump(data, tmpfile, ensure_ascii=False, indent=2)
            tmpfile_path = tmpfile.name

        s3.upload_file(tmpfile_path, bucket, s3_key)
        os.remove(tmpfile_path)
        return True
    except Exception as e:
        print(f"❌ [upload_json_to_s3] Upload error: {e}")
        return False

def list_audio_test_files():
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")

    except Exception as e:
        print(f"❌ [list_audio_test_files] list_audio_test_files error: {e}")
        return False

    prefix = TEST_AUDIO_S3_KEY

    result = {}

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".mp3"):
                continue
            parts = key[len(prefix):].split("/", 1)
            if len(parts) != 2:
                continue
            lang, filename = parts
            result.setdefault(lang, []).append({
                "key": key,
                "filename": filename,
                "url": f"https://{bucket}.s3.amazonaws.com/{key}"
            })

    return result

def get_audio_test_texts():
    
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")

    except Exception as e:
        print(f"❌ [get_audio_test_texts] get_audio_test_texts error: {e}")
        return False
    
    key = f"{TEST_AUDIO_S3_KEY}test_text_audio.json"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        # print(f's3.download_file({bucket}, {key}, {tmp.name})')
        # s3.download_file(bucket, key, tmp.name)
        try:
            # print(f's3.download_file({bucket}, {key}, {tmp.name})')
            s3.download_file(bucket, key, tmp.name)
            with open(tmp.name, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as e:
            print(f"❌ [get_audio_test_texts] Błąd pobierania test_text_audio.json: {e}")
            return {}
        # finally:
        #     os.unlink(tmp.name)

    tmp.close() # To zamyka plik zanim ponownie go otworzymy
    try:
        with open(tmp.name, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    except Exception as e:
        print(f"❌ [get_audio_test_texts] Błąd pobierania test_text_audio.json: {e}")
        return {}

def remove_article_from_audio_index(s3_prefix: str, article_yy: str, article_mm: str, lang: str, article_id: int) -> dict:
    """
    Usuwa rekord article_id z pliku indeksu audionews_{YYYY}_{MM}_{lang}.json
    i zapisuje plik z powrotem na S3 (public-read).
    Zwraca dict z podsumowaniem.
    """
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")

    except Exception as e:
        print(f"❌ [remove_article_from_audio_index] get_audio_test_texts error: {e}")
        return False

    # Wczytaj indeks
    idx = fetch_audio_index_from_s3(s3_prefix, f"{int(article_yy):04d}", f"{int(article_mm):02d}", lang)
    data = idx.get("data") or {}
    key = str(int(article_id))

    if key not in data:
        # nic do usunięcia – ale i tak zapisz spójny plik (bez zmian)
        return {"status": "ok", "removed": 0, "kept": len(data), "note": "not_found"}

    data.pop(key, None)

    # porządkowanie (opcjonalnie)
    if "dedupe_audio_index" in globals() and callable(dedupe_audio_index):
        try:
            idx = dedupe_audio_index({"status": "success", "data": data})
        except Exception as e:
            print(f"⚠️ dedupe_audio_index error: {e}")
            idx = {"status": "success", "data": data}
    else:
        idx = {"status": "success", "data": data}

    # Zapisz plik na S3
    filename = f"audionews_{int(article_yy):04d}_{int(article_mm):02d}_{lang}.json"
    s3_key = f"{s3_prefix}{int(article_yy):04d}/{int(article_mm):02d}/{filename}"

    from io import BytesIO
    payload = json.dumps(idx, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        s3.upload_fileobj(
            BytesIO(payload),
            bucket,
            s3_key,
            ExtraArgs={"ContentType": "application/json", "ACL": "public-read"}
        )
    except Exception as e:
        return {"status": "error", "error": f"upload error: {e}", "s3_key": s3_key}

    return {
        "status": "ok",
        "removed": 1,
        "kept": len(idx.get("data", {})),
        "s3_key": s3_key
    }

def build_and_upload_audio_indexes(new_data_dict, s3_prefix, article_yy, article_mm, lang):
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")
    except Exception as e:
        print(f"❌ Błąd przy próbie połączenia do S3: {e}")
        return None

    # S3 ścieżka pliku JSON
    filename = f"audionews_{article_yy}_{article_mm}_{lang}.json"
    s3_key = f"{s3_prefix}{article_yy}/{article_mm}/{filename}"

    existing_data = {
        "status": "success",
        "data": {}
    }

    # Pobranie istniejącego pliku z S3 (jeśli istnieje)
    try:
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        content = response['Body'].read().decode('utf-8')
        existing_data = json.loads(content)
        if "data" not in existing_data:
            existing_data["data"] = {}
        # print("ℹ️ Załadowano istniejący plik z S3")
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            print("⚠️ Plik nie istnieje, zostanie utworzony nowy")
        else:
            print(f"❌ Błąd przy pobieraniu pliku z S3: {e}")
            return None

    # Dodanie nowych wpisów
    for article_id, article_data in new_data_dict.items():
        key = str(article_id)
        if key in existing_data["data"]:
            # już istnieje – SCAL wpisy (np. zmerguj urls i metadane)
            current = existing_data["data"][key]
            # merge urls
            cur_urls = current.get("urls") or []
            new_urls = article_data.get("urls") or []
            if not isinstance(cur_urls, list): cur_urls = [cur_urls]
            if not isinstance(new_urls, list): new_urls = [new_urls]
            merged_urls = sorted(set(cur_urls + new_urls))

            # nadpisz meta (poza URL), jeśli chcesz – albo zostaw starsze
            for k, v in article_data.items():
                if k != "urls":
                    current[k] = v
            current["urls"] = merged_urls
            existing_data["data"][key] = current
        else:
            existing_data["data"][key] = article_data
            
    # Serializacja i wysyłka na S3
    # Usuń duplikaty przed zapisem
    existing_data = dedupe_audio_index(existing_data)

    # Serializacja i wysyłka na S3
    json_bytes = json.dumps(existing_data, ensure_ascii=False, indent=2).encode("utf-8")
    file_buffer = BytesIO(json_bytes)

    try:
        s3.upload_fileobj(
            file_buffer,
            bucket,
            s3_key,
            ExtraArgs={
                "ContentType": "application/json",
                "ACL": "public-read"
            }
        )
        # print(f"✅ Załadowano do S3: {s3_key}")
    except Exception as e:
        print(f"❌ Błąd przy wysyłaniu do S3: {e}")
        return None

def load_audio_index_from_s3(year: int, month: int, lang: str, force_refresh: bool = False) -> dict:

    # --- CACHE CHECK ---
    cache_key = (int(year), int(month), str(lang))
    now = time.time()
    
    # bucket = os.getenv("AWS_S3_BUCKET")
    # prefix = os.getenv("S3_INDEX_PREFIX", "")
    s3_key = f"{S3_PREFIX}{int(year):04d}/{int(month):02d}/audionews_{int(year):04d}_{int(month):02d}_{lang}.json"
    s3, bucket, region = s3_session()
    
    try:
        # print(f's3 type={type(s3)} Bucket={bucket}, Key={s3_key}')
        obj = s3.get_object(Bucket=bucket, Key=s3_key)
        body = obj["Body"].read().decode("utf-8")
        raw = json.loads(body)
        data = _normalize_audio_index(raw)
    except s3.exceptions.NoSuchKey:
        printLog(f"⚠️ S3 NoSuchKey")
        data = {}
    except Exception as e:
        printLog(f"⚠️ Nie udało się pobrać {s3_key} z S3: {e}")
        data = {}

    # --- CACHE SET ---
    with _audio_index_lock:
        _audio_index_cache[cache_key] = {"ts": now, "data": data}

    return data

def fetch_audio_index_from_s3(s3_prefix: str, article_yy: str, article_mm: str, lang: str) -> dict:
    """
    Pobiera z S3 plik audionews_{YYYY}_{MM}_{lang}.json i zwraca jako dict:
    {"status": "success", "data": {...}}
    Jeśli plik nie istnieje -> zwraca pustą strukturę.
    """

    s3, bucket, region = s3_session()
    
    # print(f'1. article_mm ==> {article_mm} ==> article_mm ==> {article_mm}')
    # if int(article_mm) < 10 and len(str(article_mm)) == 1:
    #     article_mm = f'0{str(article_mm)}'
    # print(f'2. article_mm ==> {article_mm} ==> article_mm ==> {article_mm}')
    filename = f"audionews_{article_yy}_{article_mm}_{lang}.json"
    s3_key = f"{s3_prefix}{article_yy}/{article_mm}/{filename}"

    existing_data = {"status": "success", "data": {}}

    try:
        # /londynek/audio/news-agency/2025/08/audionews_2025_08_pl.json
        # /londynek/audio/news-agency/2025/7/audionews_2025_7_pl.json
        # print(f's3.get_object(Bucket={bucket}, Key={s3_key})')
        obj = s3.get_object(Bucket=bucket, Key=s3_key)
        content = obj['Body'].read().decode('utf-8')
        loaded = json.loads(content)
        # .get('data')
        # print(f'type loaded={type(loaded)} \n {loaded.keys()}')

        # exit()
        if isinstance(loaded, dict):
            if "data" not in loaded or not isinstance(loaded["data"], dict):
                loaded["data"] = {}
            existing_data = loaded
        # print(existing_data)
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'NoSuchKey':
            # brak pliku — zwróć pusty słownik
            print(e.response.get('Error', {}).get('Code'))
            pass
        else:
            raise
    return existing_data

def prune_audio_index_after_deletions(s3_prefix: str, article_yy: str, article_mm: str, lang: str):
    """
    Po fizycznym usunięciu plików MP3 na S3 odświeża indeks:
      - usuwa wpisy bez plików
      - aktualizuje listy URLs na podstawie aktualnej zawartości S3
    Zwraca dict z podsumowaniem operacji.
    """
    try:
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION")
        )
        bucket = os.getenv("AWS_S3_BUCKET")
        region = os.getenv("S3_REGION") or "eu-west-2"
    except Exception as e:
        print(f"❌ Błąd przy próbie połączenia do S3: {e}")
        return {"status": "error", "error": str(e)}

    # Klucze do indeksu
    filename = f"audionews_{article_yy}_{article_mm}_{lang}.json"
    index_key = f"{s3_prefix}{article_yy}/{article_mm}/{filename}"

    # Wczytaj istniejący indeks (jeśli jest)
    index_data = {"status": "success", "data": {}}
    try:
        resp = s3.get_object(Bucket=bucket, Key=index_key)
        content = resp["Body"].read().decode("utf-8")
        loaded = json.loads(content)
        if isinstance(loaded, dict):
            index_data.update(loaded)
            if "data" not in index_data or not isinstance(index_data["data"], dict):
                index_data["data"] = {}
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            # Nie ma indeksu — nic do czyszczenia
            printLog(f"[INFO] Brak indeksu do czyszczenia: s3://{bucket}/{index_key}") if 'printLog' in globals() else None
            return {"status": "ok", "removed": 0, "updated": 0, "kept": 0, "note": "index_missing"}
        else:
            print(f"❌ Błąd pobierania indeksu: {e}")
            return {"status": "error", "error": str(e)}
    except Exception as e:
        print(f"❌ Błąd parsowania indeksu: {e}")
        return {"status": "error", "error": str(e)}

    # Zbuduj mapę URL-i z aktualnej zawartości S3 dla YYYY/MM/
    month_prefix = f"{s3_prefix}{article_yy}/{article_mm}/"
    id_to_urls = {}

    # pomocnicze: publiczny URL do klucza
    def public_url_for_key(key: str) -> str:
        # jeśli masz util get_s3_url, użyj go:
        if "get_s3_url" in globals() and callable(get_s3_url):
            return get_s3_url(key)
        # minimalny fallback:
        return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

    continuation = None
    pattern = re.compile(rf"(\d+)_({re.escape(lang)})_p\d+\.mp3$", re.IGNORECASE)

    while True:
        kwargs = {"Bucket": bucket, "Prefix": month_prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation

        listing = s3.list_objects_v2(**kwargs)
        for obj in listing.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".mp3"):
                continue
            # sprawdź, czy to plik typu <id>_<lang>_pN.mp3
            base = key.rsplit("/", 1)[-1]
            m = pattern.search(base)
            if not m:
                continue
            article_id = m.group(1)
            id_to_urls.setdefault(article_id, [])
            id_to_urls[article_id].append(public_url_for_key(key))

        if listing.get("IsTruncated"):
            continuation = listing.get("NextContinuationToken")
        else:
            break

    # Posprzątaj/dopasuj istniejące wpisy indeksu do aktualnych plików
    existing = index_data.get("data", {})
    updated = 0
    removed = 0

    # Zaktualizuj istniejące wpisy i oznacz do usunięcia puste
    to_delete_keys = []
    for aid, entry in existing.items():
        new_urls = sorted(id_to_urls.get(aid, []))
        if new_urls:
            entry["urls"] = new_urls
            updated += 1
        else:
            to_delete_keys.append(aid)

    for aid in to_delete_keys:
        existing.pop(aid, None)
        removed += 1

    # Dodaj ewentualne nowe ID, które nie istniały wcześniej (opcjonalnie – zwykle po usunięciu nie ma nowych)
    # Jeśli chcesz, możesz to wyłączyć – tu zostawiamy jako „bez zmian”:
    for aid, urls in id_to_urls.items():
        if aid not in existing:
            existing[aid] = {"id": int(aid), "lang": lang, "urls": sorted(urls)}
            updated += 1  # traktujemy jako zaktualizowane/dodane

    # Usuń duplikaty w strukturze (jeśli masz util)
    if "dedupe_audio_index" in globals() and callable(dedupe_audio_index):
        try:
            index_data = dedupe_audio_index(index_data)
        except Exception as e:
            print(f"⚠️ dedupe_audio_index error: {e}")

    # Zapisz z powrotem na S3
    try:
        payload = json.dumps(index_data, ensure_ascii=False, indent=2).encode("utf-8")
        s3.upload_fileobj(
            BytesIO(payload),
            bucket,
            index_key,
            ExtraArgs={"ContentType": "application/json", "ACL": "public-read"}
        )
    except Exception as e:
        print(f"❌ Błąd przy wysyłaniu zaktualizowanego indeksu do S3: {e}")
        return {"status": "error", "error": str(e)}

    kept = len(index_data.get("data", {}))
    return {"status": "ok", "removed": removed, "updated": updated, "kept": kept, "index_key": index_key}

def _normalize_audio_index(raw) -> dict:
    """
    Zwraca CZYSTĄ mapę { article_id(str): entry } niezależnie od formatu wejścia:
    - nowy: {"status": "...", "data": {...}}
    - stary: {"105881": {...}, ...}
    - bardzo stary: [{"id": 105881, ...}, ...]
    """
    if not raw:
        return {}

    # nowy format (z wrapperem)
    if isinstance(raw, dict) and "data" in raw:
        data = raw.get("data") or {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    # stary: już sama mapa id->entry
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    # bardzo stary: lista
    if isinstance(raw, list):
        return {str(e["id"]): e for e in raw if isinstance(e, dict) and "id" in e}

    return {}

def build_two_months_index(lang: str, force_refresh: bool = False) -> dict:
    # print(f'def build_two_months_index({lang}: str, {force_refresh}: bool = False)')
    try:
        now = datetime.utcnow()
        year_now, month_now = now.year, now.month
        prev = (now.replace(day=1) - timedelta(days=1))
        year_prev, month_prev = prev.year, prev.month
    except Exception as err1:
        print(f'❌ [build_two_months_index] err1={err1}')
    
    try:
        # print(f'load_audio_index_from_s3({year_prev}, {month_prev}, {lang}, {force_refresh})')
        idx_prev = load_audio_index_from_s3(year_prev, month_prev, lang, force_refresh)
    except Exception as err2:
        print(f'❌ [build_two_months_index] err2={err2}')
    # print('END idx_prev')

    try:
        idx_now  = load_audio_index_from_s3(year_now, month_now, lang, force_refresh)
    except Exception as err3:
        print(f'❌ [build_two_months_index] err3={err3}')
    
    try:
        merged = {}
        merged.update(idx_prev)  # mapa id->entry
        merged.update(idx_now)   # nadpisze duplikaty „nowymi”, ale nie wytnie pozostałych
    except Exception as err4:
        print(f'❌ [build_two_months_index] err4={err4}')
    
    # printLog(f"Index {lang}: prev={len(idx_prev)} now={len(idx_now)} merged={len(merged)}")

    return merged

def dedupe_audio_index(existing_data: dict) -> dict:
    """
    Usuwa duplikaty wpisów:
    - scala wielokrotne wpisy tego samego article_id (klucz jako string),
    - deduplikuje listę URL'i w każdym artykule,
    - wybiera nowsze metadata po polu 'datetime' (o ile istnieje).
    """
    if not isinstance(existing_data, dict):
        return {"status": "success", "data": {}}

    data = existing_data.get("data", {})
    if not isinstance(data, dict):
        existing_data["data"] = {}
        return existing_data

    merged = {}
    for art_id, art_data in data.items():
        key = str(art_id)

        # normalizacja struktur
        urls = art_data.get("urls") or []
        if not isinstance(urls, list):
            urls = [urls]
        urls = sorted(set(urls))

        art_data["urls"] = urls

        if key not in merged:
            merged[key] = art_data
            continue

        # scalanie duplikatu
        prev = merged[key]
        prev_urls = prev.get("urls") or []
        merged_urls = sorted(set(prev_urls + urls))

        # wybierz nowszy wpis po datetime (opcjonalnie)
        prev_dt = prev.get("datetime") or ""
        new_dt = art_data.get("datetime") or ""
        winner = art_data if new_dt > prev_dt else prev
        winner["urls"] = merged_urls
        merged[key] = winner

    existing_data["data"] = merged
    return existing_data

def build_and_upload_audio_indexes_test(done_json, s3_prefix, article_yy, article_mm, lang):
    print('londynek/audio/news-agency/2025/06/audionews_2025_06_pl.json')
    filename = f"audionews_{article_yy}_{article_mm}_{lang}.json"
    s3_key = f"{s3_prefix}{article_yy}/{article_mm}/{filename}"
    print(get_s3_url(s3_key))

    with open(done_json, "r") as f:
        entries = json.load(f)
    print(f'entries==> type={type(entries)}  len={len(entries)}')
    indexes = {}

    for entry in entries:
        if not entry.get("datetime") or not entry.get("lang"):
            continue

        try:
            dt = datetime.fromisoformat(entry["datetime"])
        except ValueError:
            continue

        article_id = str(entry["id"])

        key = f"{article_yy}_{article_mm}_{lang}"
        if key not in indexes:
            indexes[key] = {}

        indexes[key][article_id] = {
            "lang": lang,
            "voice": entry.get("voice", "unknown"),
            "urls": entry.get("urls", []),
            "datetime": entry["datetime"]
        }

    # print(indexes)
