import os
import sys
if sys.platform != "win32":
    import pwd
    import grp
else:
    pwd = None
    grp = None
    import getpass

import socket
import json
import functools
import stat
import requests
from datetime import datetime
import inspect
import re
from dotenv import load_dotenv
from pathlib import Path

# root project path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# WDM_CACHE_DIR = os.path.join(BASE_DIR, "logs")
DATA_SETTINGS_DIR = os.path.join(os.path.dirname(BASE_DIR), 'data_settings')
# Ustawienie katalogu cache na logs/ dla selenium
os.environ['WDM_LOCAL'] = '1'
os.environ['WDM_CACHE_DIR'] = os.path.join(BASE_DIR, "logs")

DATA_FILES_DIR = os.path.join(BASE_DIR, "data_files")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
RESULTS_DIR = os.path.join(BASE_DIR, "results")
LOGS_DIR_PATH = os.path.join(BASE_DIR, "logs")
# HOME_DIR moce by7 przekazany w .env (../data_settings/.env) lub b9dzie domyblnie ustawiony na katalog domowy ucytkownika
HOME_DIR = os.getenv("HOME_DIR") or str(Path.home())
ENDPOINT_DOMAIN = "https://londynek.net"

print(f'DATA_FILES_DIR = {DATA_FILES_DIR}')
print(f'TEMPLATES_DIR = {TEMPLATES_DIR}')
print(f'STATIC_DIR = {STATIC_DIR}')
print(f'RESULTS_DIR = {RESULTS_DIR}')
print(f'LOGS_DIR_PATH = {LOGS_DIR_PATH}')
print(f'ENDPOINT_DOMAIN = {ENDPOINT_DOMAIN}')

SITE_BASE = "https://londynek.net"
TELEGRAM_MSG_SEND = os.getenv("TELEGRAM_MSG_SEND", "0").strip().lower() in ("1", "true", "yes", "on")
DEFAULT_MODEL_VERSION = "gpt-4.1-mini"
EXTRA_MODEL_VERSION = "gpt-4.1-mini"

ALLOWED_COUNTRY_CODE = ['UK', 'GB', 'PL']
NOTALLOWED_COUNTRY_CODE = ['BJ', 'IN']

CACHE_TTL_SECONDS = 60  # ile sekund trzymamy wpis w pamięci
S3_PREFIX = "londynek/audio/news-agency/"
TEST_AUDIO_S3_KEY = 'londynek/audio/test_audio/'
# PARAGRAPHS_N = 0 - pełny text, = -1 wyłączone
PARAGRAPHS_N = 0


ALLOWED_EXTENSIONS = {'.json', '.jsonl', '.log'}
# Lista dozwolonych katalogów bazowych
ALLOWED_LOGS_DIR = LOGS_DIR_PATH

PENDING_ADS_LIMIT = 50
AVAILABLE_SETUP_PACKAGES = [
        {'package_name': 'accommodation', 'package_id': 1222, 'table_name': 'jdrooms_ads', 'left_join_table_types_name': 'jdrooms_types', 'section_name_pl': 'Nieruchomości', 
         'select_fields': 'user_id, ad_title_pl as title_pl,ad_title as title_en,body_pl as body_pl,body as body_en,type_name_pl as type_name_pl,type_name as type_name_en,expiry_date,suburb,category_id,price,price_type,date_available', 'category_case': ', CASE category_id WHEN 1 THEN \'Mam do wynajęcia\' WHEN 2 THEN \'Szukam aby wynająć\' WHEN 3 THEN \'Sprzedam\' WHEN 4 THEN \'Kupię\' ELSE \'Category unknown\' END AS category_name'},
        {'package_name': 'business', 'package_id': 1281, 'table_name': 'jdbusiness_ads', 'left_join_table_types_name': 'jdbusiness_types', 'section_name_pl': 'Usługi', 
         'select_fields': 'user_id, ad_title_pl as title_pl,ad_title as title_en,body_pl as body_pl,body as body_en,type_name_pl as type_name_pl,type_name as type_name_en,expiry_date,expiry_date,suburb'},
        {'package_name': 'buysell', 'package_id': 1293, 'table_name': 'jdbuysell_ads', 'left_join_table_types_name': 'jdbuysell_types', 'section_name_pl': 'Kupię / Sprzedam', 
         'select_fields': 'user_id, ad_title_pl as title_pl,ad_title as title_en,body_pl as body_pl,body as body_en,type_name_pl as type_name_pl,type_name as type_name_en,expiry_date,expiry_date,suburb,category_id,price', 'sql_where':'where ja.type_id > 1', 'category_case': ', CASE category_id WHEN 1 THEN \'Sprzedam\' WHEN 2 THEN \'Kupię\' ELSE \'Nieznana kategoria\' END AS category_name'},
        {'package_name': 'automotive', 'package_id': 3098132, 'table_name': 'jdbuysell_ads', 'left_join_table_types_name': 'jdbuysell_types', 'section_name_pl': 'Motoryzacja', 
         'select_fields': 'user_id, ad_title_pl as title_pl,ad_title as title_en,body_pl as body_pl,body as body_en,type_name_pl as type_name_pl,type_name as type_name_en,expiry_date,expiry_date,suburb,category_id,price', 'sql_where':'where ja.type_id = 1'},
        {'package_name': 'personals', 'package_id': 1301, 'table_name': 'jdpersonals_ads', 'left_join_table_types_name': 'jdpersonals_types', 'section_name_pl': 'Towarzyskie', 
         'select_fields': 'user_id, ad_title_pl as title_pl,ad_title as title_en,body_pl as body_pl,body as body_en,type_name_pl as type_name_pl,type_name as type_name_en,expiry_date,end_date,suburb'},
        {'package_name': 'jobs', 'package_id': 1231, 'table_name': 'job_ads', 'left_join_table_types_name': 'job_sectors', 'section_name_pl': 'Praca - oferty', 
         'select_fields': '(select prim_user_id from advertisers a where a.advertiser_id = ja.advertiser_id) as user_id, job_title_pl as title_pl,job_title as title_en,job_desc_pl as body_pl,job_desc as body_en,sector_name_pl as type_name_pl,sector_name as type_name_en,end_date'}
]

CONFIG_CACHE = {}

def get_config(key=None):
    global CONFIG_CACHE

    if not CONFIG_CACHE:
        try:
            
            settings_dir = DATA_SETTINGS_DIR
            
            # Wczytaj .env
            if key == 'ai_moderation':
                env_path = os.path.join(settings_dir, ".env-ai_moderation")
            
            else:    
                env_path = os.path.join(settings_dir, ".env")

            if os.path.exists(env_path):
                # print(f'load_dotenv({env_path})')
                load_dotenv(env_path)

            # Przechowuj env jako dict
            CONFIG_CACHE["env"] = dict(os.environ)

            if key == 'ai_moderation':
                os.environ["OPENAI_API_KEY_MODERACJA"] = CONFIG_CACHE["env"]["OPENAI_API_KEY_MODERACJA"]

                return CONFIG_CACHE
            
            else:
                # 🔁 Ustaw do środowiska najważniejsze zmienne dla TTS/API
                for var in [
                    "OPENAI_API_KEY",
                    "ELEVENLABS_API_KEY",
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AZURE_TTS_KEY",
                    "AZURE_TTS_REGION",
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GCS_BUCKET",
                    "GCS_PREFIX",
                    "GOOGLE_CLOUD_PROJECT",
                    "HOME_DIR",
                    "TEST_QQ"
                    ]:
                    val = CONFIG_CACHE["env"].get(var)
                    if not val:
                        continue

                    if var == "GOOGLE_APPLICATION_CREDENTIALS":
                        if not os.path.exists(val):
                            alt_path = os.path.join(DATA_SETTINGS_DIR, os.path.basename(val))
                            if os.path.exists(alt_path):
                                print(f"[config] Adjusting GOOGLE_APPLICATION_CREDENTIALS to {alt_path}")
                                val = alt_path
                                CONFIG_CACHE["env"][var] = alt_path
                            else:
                                print(f"[config] WARNING: GOOGLE_APPLICATION_CREDENTIALS not found: {val}")
                    os.environ[var] = val

                # Wczytaj tts_config.json
                tts_path = os.path.join(settings_dir, "tts_config.json")
                if os.path.exists(tts_path):
                    with open(tts_path, "r") as f:
                        CONFIG_CACHE["tts"] = json.load(f)
                else:
                    CONFIG_CACHE["tts"] = {}

        except Exception as e:
            print(f"❌ Błąd ładowania konfiguracji: {e}")
            CONFIG_CACHE = {"env": {}, "tts": {}}

    if key:
        return CONFIG_CACHE.get(key, {})
    
    return CONFIG_CACHE


# def ensure_log_path(log_file):
#     if not log_file or not isinstance(log_file, str):
#         raise ValueError(f"Ścieżka do logu nieprawidłowa: {log_file!r}")
#     if "None" in log_file:
#         raise ValueError(f"Ścieżka logu zawiera niedozwolony fragment 'None': {log_file!r}")

#     log_path = Path(log_file)
#     log_dir = log_path.parent

#     if not log_dir.exists():
#         try:
#             log_dir.mkdir(parents=True, exist_ok=True)
#         except Exception as e:
#             ctx = _show_permission_details(str(log_dir))
#             raise IOError(
#                 f"❌ Nie można utworzyć katalogu logów: {log_dir}. Błąd: {e}\n{ctx}"
#             )

#     if not os.access(str(log_dir), os.W_OK):
#         ctx = _show_permission_details(str(log_dir))
#         raise PermissionError(
#             f"Brak prawa zapisu do katalogu logu: {log_dir}\n{ctx}"
#         )

#     # Jeśli plik istnieje, sprawdźmy zapis
#     if log_path.exists() and not os.access(str(log_path), os.W_OK):
#         ctx = _show_permission_details(str(log_file))
#         raise PermissionError(
#             f"Brak prawa zapisu do pliku logu: {log_file}\n{ctx}"
#         )
#     return str(log_file)


# # --- SAFE WRITE ---
# def safe_write(func):
#     @functools.wraps(func)
#     def wrapper(*args, **kwargs):
#         try:
#             return func(*args, **kwargs)

#         except PermissionError as e:
#             path = _extract_path_from_args(args, kwargs)
#             print(f"❌ [Błąd] Brak uprawnień do pliku/katalogu: {e}")
#             if path:
#                 st = _stat_safe(path)
#                 if st:
#                     print(_show_permission_details(path, st))
#                 else:
#                     # Ścieżka może nie istnieć – pokaż katalog nadrzędny
#                     dir_path = os.path.dirname(path) or '.'
#                     print(f"(Plik nie istnieje) — sprawdzam katalog nadrzędny: {dir_path}")
#                     st_dir = _stat_safe(dir_path)
#                     print(_show_permission_details(dir_path, st_dir))

#         except FileNotFoundError as e:
#             path = _extract_path_from_args(args, kwargs)
#             print(f"❌ [Błąd] Nie znaleziono ścieżki lub pliku: {e}")
#             if path:
#                 # pokaż co z katalogiem nadrzędnym
#                 dir_path = os.path.dirname(path) or '.'
#                 st_dir = _stat_safe(dir_path)
#                 print(_show_permission_details(dir_path, st_dir))

#         except IsADirectoryError as e:
#             path = _extract_path_from_args(args, kwargs)
#             print(f"❌ [Błąd] Podana ścieżka to katalog, nie plik: {e}")
#             if path:
#                 print(_show_permission_details(path, _stat_safe(path)))

#         except OSError as e:
#             path = _extract_path_from_args(args, kwargs)
#             print(f"❌ [Błąd OS] Problem z zapisem do pliku: {e}")
#             if path:
#                 print(_show_permission_details(path, _stat_safe(path)))

#         except Exception as e:
#             print(f"❌ [Błąd] Nieoczekiwany błąd: {e}")
#     return wrapper
# # Zapisz plik zgodnie z dostarczoną metodą
# @safe_write
# def zapisz_plik(file_path, data=[]):
#     method = "w"
#     content = data[0] if data else None
#     data_type = data[1] if len(data) > 1 else None
#     method = data[2] if len(data) > 2 else method

#     if data_type == 'json.dump':
#         with open(file_path, method, encoding="utf-8") as f:
#             json.dump(content, f, indent=2, ensure_ascii=False)
#         return True
#     return False



# def unique_list(l=[]):
#     # print(f'make unique_list {len(l)}')
#     septs = [' ', '.', ',']
#     pattern = "[" + re.escape("".join(septs)) + "]"

#     unique = []
#     seen = set()

#     for item in l:
#         tokens = []
#         for part in item:
#             tokens.extend(re.split(pattern, part.lower()))
#         tokens = sorted(set(filter(None, tokens)))
#         norm = tuple(tokens)
#         if norm not in seen:
#             seen.add(norm)
#             unique.append(tokens)

#     return unique

# def load_spam_conditions(log_file_path=None):
#     # print(f'def load_spam_conditions() START ==> <br>\nlog_file_path={log_file_path} <br>\nENDPOINT_DOMAIN={ENDPOINT_DOMAIN}', log_file_path)
#     if not os.path.exists(SPAM_FILE):
#         return []
#     with open(SPAM_FILE, "r", encoding="utf-8") as f:
#         spam_conditions = json.load(f) # list of list [['data'], ['123','234']]

#     spam_words_endpoint_url = f"{ENDPOINT_DOMAIN}/api/get-data?hash=206A7E40FDBBD758317F569F53CA10D55002B4E4"
    
#     return extend_spam_conditions_from_endpoint(spam_words_endpoint_url, spam_conditions)
#     # return extend_spam_conditions_from_endpoint(spam_words_endpoint_url, [])

# def extend_spam_conditions_from_endpoint(url, spam_conditions):
    
#     try:
#         response = requests.get(url, timeout=5)
#         response.raise_for_status()
#         data = response.json()

#         banned_words = data.get("banned_words", [])
#         added = 0
#         # spam_conditions = [['golden', ' virginia'], ['polski-lekarz.co.uk']]
#         # banned_words = [['golden virginia'], ['polski-lekarz.co.uk']]

#         # print(1111)
#         for word in banned_words:
#             # print(f'banned_words== {isinstance(word, list)} =>{word}')
#             if isinstance(word, str):
#                 # print(2222)
#                 word = [word]
            
#             if isinstance(word, list):
#                 # print(3333, len(word), type(word), word)
#                 word = word[0]
#                 # print(3333, len(word), type(word), word)
                
#                 condition = []
#                 clean_word = word.strip().lower()
#                 septs = [' ', '.', ',']
#                 for sep in septs: 
#                     stripped_word = clean_word.split(sep)
#                     # print(f'stripped_word==>{stripped_word}')
#                     if len(stripped_word) > 1:
#                         for each_word in stripped_word:
#                             condition.append(each_word)
#                     elif len(stripped_word) == 1:
#                         condition = [clean_word]
#                     else:
#                         continue

#                     # print(f'condition={condition}')
#                     if condition not in spam_conditions:
#                         spam_conditions.append(condition)
#                         added += 1

#         spam_conditions = unique_list(spam_conditions)

#         return unique_list(spam_conditions)

#     except Exception as e:
#         print(f"❌ [extend_spam_conditions] Błąd: {e}")
#         return 0

# def search_url_in_string(tekst):
#     """
#     Przeszukuje ciąg znaków w poszukiwaniu adresów URL i formatuje je do HTML.

#     Args:
#         tekst: Ciąg znaków do przeszukania.

#     Returns:
#         Ciąg znaków z sformatowanymi linkami HTML.
#     """
#     def wrap_url(match):
#         url = match.group(0)
#         return f'<a href="{url}" target="_blank">{url}</a>'

#     url_match = r'(https?://\S+)'

#     return re.sub(url_match, wrap_url, tekst)


# # --- HELPERS ---
# def _get_user_context_depr():
#     """Zwraca słownik z informacją o użytkowniku uruchamiającym proces."""
#     try:
#         euid = os.geteuid()
#         egid = os.getegid()
#         uid = os.getuid()
#         gid = os.getgid()
#     except AttributeError:
#         # Windows/środowiska bez geteuid – fallback
#         euid = uid = os.getuid() if hasattr(os, "getuid") else None
#         egid = gid = os.getgid() if hasattr(os, "getgid") else None

#     user = pwd.getpwuid(euid).pw_name if euid is not None else None
#     group = grp.getgrgid(egid).gr_name if egid is not None else None
#     # bezpieczne podejrzenie umask
#     try:
#         cur_umask = os.umask(0)
#         os.umask(cur_umask)
#     except Exception:
#         cur_umask = None

#     try:
#         groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
#     except Exception:
#         groups = None

#     return {
#         "uid": uid,
#         "gid": gid,
#         "euid": euid,
#         "egid": egid,
#         "user": user,
#         "group": group,
#         "groups": groups,
#         "umask_oct": f"{cur_umask:o}" if cur_umask is not None else None,
#         "cwd": os.getcwd(),
#     }

# def _format_mode_bits(st_mode: int):
#     """Zwraca (symboliczne, ósemkowe) uprawnienia."""
#     return stat.filemode(st_mode), oct(st_mode)

# def _who_can_write(path: str) -> str:
#     """Czy EUID ma zapis do ścieżki? (uwzględnia sticky bits/ACL pomijamy)"""
#     try:
#         can = os.access(path, os.W_OK)
#         return "tak" if can else "nie"
#     except Exception:
#         return "nieznane"

# def _stat_safe(path: str):
#     try:
#         return os.stat(path)
#     except Exception:
#         return None

# def _owner_group_from_stat_depr(st):
#     try:
#         owner = pwd.getpwuid(st.st_uid).pw_name
#     except Exception:
#         owner = str(st.st_uid)
#     try:
#         group = grp.getgrgid(st.st_gid).gr_name
#     except Exception:
#         group = str(st.st_gid)
#     return owner, group

# def _owner_group_from_stat(st):
#     if pwd and grp:
#         try:
#             owner = pwd.getpwuid(st.st_uid).pw_name
#         except Exception:
#             owner = str(st.st_uid)
#         try:
#             group = grp.getgrgid(st.st_gid).gr_name
#         except Exception:
#             group = str(st.st_gid)
#     else:
#         # Windows - UID/GID zwykle nie używane, można podać puste lub inne informacje
#         owner = ""
#         group = ""
#     return owner, group

# def _get_user_context():
#     try:
#         euid = os.geteuid()
#         egid = os.getegid()
#         uid = os.getuid()
#         gid = os.getgid()
#     except AttributeError:
#         # Windows: fallback
#         euid = uid = None
#         egid = gid = None

#     if pwd and euid is not None:
#         try:
#             user = pwd.getpwuid(euid).pw_name
#         except Exception:
#             user = None
#     else:
#         # Windows: użyj getpass
#         # import getpass
#         user = getpass.getuser()

#     if grp and egid is not None:
#         try:
#             group = grp.getgrgid(egid).gr_name
#         except Exception:
#             group = None
#     else:
#         group = None

#     try:
#         cur_umask = os.umask(0)
#         os.umask(cur_umask)
#     except Exception:
#         cur_umask = None

#     try:
#         if grp:
#             groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
#         else:
#             groups = None
#     except Exception:
#         groups = None

#     return {
#         "uid": uid,
#         "gid": gid,
#         "euid": euid,
#         "egid": egid,
#         "user": user,
#         "group": group,
#         "groups": groups,
#         "umask_oct": f"{cur_umask:o}" if cur_umask is not None else None,
#         "cwd": os.getcwd(),
#     }


# def _show_permission_details(path: str, st=None) -> str:
#     """Buduje czytelny opis uprawnień pliku/katalogu + kontekstu użytkownika."""
#     path = str(path)
#     st = st or _stat_safe(path)
#     uc = _get_user_context()

#     lines = []
#     lines.append(f"Ścieżka: {path}")
#     if st:
#         perm_sym, perm_oct = _format_mode_bits(st.st_mode)
#         owner, group = _owner_group_from_stat(st)
#         lines.append(f"Uprawnienia: {perm_sym} ({perm_oct})")
#         lines.append(f"Właściciel: {owner} (uid={st.st_uid}), Grupa: {group} (gid={st.st_gid})")
#     else:
#         lines.append("Brak stat() – plik/katalog może nie istnieć.")

#     lines.append(f"EUID proces: {uc['euid']} ({uc['user']}), EGID: {uc['egid']} ({uc['group']})")
#     if uc["groups"]:
#         lines.append(f"Grupy dodatkowe: {', '.join(uc['groups'])}")
#     if uc["umask_oct"] is not None:
#         lines.append(f"Umask: 0o{uc['umask_oct']}")
#     lines.append(f"CWD: {uc['cwd']}")
#     try:
#         lines.append(f"Czy EUID ma zapis do ścieżki? {_who_can_write(path)}")
#     except Exception:
#         pass
#     return "\n".join(lines)

# def _extract_path_from_args(args, kwargs):
#     """
#     Wydobywa prawdopodobną ścieżkę z pierwszego parametru lub z nazwanych (file_path/log_file/log_file_path).
#     """
#     if args:
#         # common cases: printLog(message, log_file_path=...), zapisz_plik(file_path, ...)
#         # w zapisz_plik pierwszym argiem jest file_path
#         if isinstance(args[0], (str, os.PathLike)):
#             return str(args[0])
#         # w printLog pierwszym argiem jest message, a drugim może być log_file_path
#         if len(args) > 1 and isinstance(args[1], (str, os.PathLike)):
#             return str(args[1])

#     for k in ("file_path", "log_file", "log_file_path", "path"):
#         if k in kwargs and kwargs[k]:
#             return str(kwargs[k])
#     return None



