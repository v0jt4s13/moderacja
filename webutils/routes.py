
import os
import sys
import time
import json
import re
from datetime import datetime
from pathlib import Path
from flask import request, Blueprint, render_template_string, render_template, abort, jsonify, redirect

from loggers import webutils_routes_logger

from auth import login_required
from webutils.fonts import generate_google_fonts_section
from webutils.debug_utils import printLog
from webutils.view_file import is_allowed_file, is_path_allowed
from webutils.function import analyze_file
from webutils.messages import send_telegram_message
from config import BASE_DIR, ALLOWED_EXTENSIONS, LOGS_DIR_PATH
# Fallbacks for logging paths if not explicitly configured
ALLOWED_LOGS_DIR = LOGS_DIR_PATH
ALLOWED_LOGS_DIRS = [ALLOWED_LOGS_DIR]

webutils_bp = Blueprint('webutils', __name__, url_prefix="/webutils")

TS_PATTERNS = [
    ("%Y-%m-%d %H:%M:%S,%f", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3,6})")),
    ("%Y-%m-%dT%H:%M:%S.%f", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{1,6})")),
    ("%Y-%m-%dT%H:%M:%S",    re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")),
]

def _parse_ts(ts_str: str):
    ts_str = ts_str.strip()
    for fmt, _ in TS_PATTERNS:
        try:
            return datetime.strptime(ts_str, fmt)
        except Exception:
            pass
    # ISO z „Z” lub strefą – spróbuj delikatnie usunąć 'Z' / offset
    ts_clean = re.sub(r"(Z|[+\-]\d{2}:\d{2})$", "", ts_str)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(ts_clean, fmt)
        except Exception:
            pass
    return None

def _line_looks_like_dict(s: str) -> bool:
    # np. "{'project_id': '...', 'project_dir': '...'}"
    return s.strip().startswith("{") and s.strip().endswith("}")


@webutils_bp.route('/fonts')
@login_required(role=["admin"])
def fonts():
    font_list = [
        "Roboto",
        "Lobster",
        "Playfair Display",
        "Inconsolata",
        "Montserrat",
        "Open Sans",
        "Raleway",
        "Merriweather",
        "Pacifico",
        "Source Code Pro",
        "Dancing Script",
        "Oswald",
        "Quicksand",
        "Great Vibes", 
        "Satisfy", 
        "Allura", 
        "Creepster", 
        "Nosifer", 
        "Butcherman", 
        "Frijole", 
        "UnifrakturCook", 
        "Metal Mania"
    ]

    link_tag, html_blocks = generate_google_fonts_section(font_list)

    return render_template("webutils/fonts.html", link_tag=link_tag, html_blocks=html_blocks)

@webutils_bp.route("/emoji")
@login_required(role=["admin"])
def show_emoji():
    return render_template("webutils/emoji.html")

@webutils_bp.route("/man")
# @login_required(role=["admin"])
def show_manuals():
    return render_template("webutils/open_chat.html")

@webutils_bp.route("/logs")
@login_required(role=["admin"])
def show_logs():
    
    # send_telegram_message('TEST')
    webutils_routes_logger.info(f'START show_logs() ==> {os.path.join("/var/www", ".local/var/log/talk_to")}')
    # home/vs/projects/ai/__ops01/logs
    selected_file = request.args.get("file")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    all_files_with_mtime_by_folder = {}
    first_available_file = None
    parsed_data = None

    webutils_routes_logger.info(f'ALLOWED_LOGS_DIRS==>{ALLOWED_LOGS_DIRS}')
    webutils_routes_logger.info(f'ALLOWED_LOGS_DIR==>{ALLOWED_LOGS_DIR}')

    for log_dir in ALLOWED_LOGS_DIRS:
        folder_path = Path(log_dir)
        if folder_path.is_dir():
            files_with_mtime = []
            for f in folder_path.iterdir():
                if f.is_file() and f.suffix in ['.log', '.json', '.jsonl']:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    files_with_mtime.append((f.name, mtime))

            if files_with_mtime:
                all_files_with_mtime_by_folder[log_dir] = sorted(files_with_mtime, key=lambda item: item[0])
                if first_available_file is None and files_with_mtime:
                    first_available_file = files_with_mtime[0][0]

    # Ustal wybrany plik
    if selected_file is None:
        selected_file = first_available_file if first_available_file else "brak_plikow"

    log_path = None
    if selected_file != "brak_plikow":
        for log_dir, files_mtimes in all_files_with_mtime_by_folder.items():
            for filename, mtime in files_mtimes:
                if filename == selected_file:
                    log_path = Path(log_dir) / selected_file
                    break
            if log_path:
                break

    file_content_json = {}
    file_content_lines = []
    file_type = ""
    result = None
    entries = []

    webutils_routes_logger.info(f'log_path===>{log_path}') #, log_path.exists()==>{log_path.exists()}')
    # if log_path and log_path.exists():
    if log_path:
        result = analyze_file(log_path)
        file_type = result.get("file_type", "")

        raw_data = result.get("data") or []
        # Spięcie parsera dla trzech rodzin formatów:
        # 1) JSON/JSONL jedna linia = jeden JSON obiekt
        if file_type == "json":
            # jeżeli analyze_file zaczytał JSON jako dict/list:
            if isinstance(raw_data, list):
                # Każdy element listy próbujemy opakować jako wpis
                for rec in raw_data:
                    ts = None
                    ts_str = ""
                    body = rec
                    if isinstance(rec, dict):
                        ts_str = rec.get("timestamp", "")
                        ts = _parse_ts(ts_str) if ts_str else None
                    entries.append({
                        "ts": ts,
                        "ts_str": ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3] if ts else ts_str or "",
                        "scope": rec.get("section") if isinstance(rec, dict) else "",
                        "level": rec.get("level", "") if isinstance(rec, dict) else "",
                        "body": rec,
                    })
            elif isinstance(raw_data, dict):
                ts_str = raw_data.get("timestamp", "")
                ts = _parse_ts(ts_str) if ts_str else None
                entries.append({
                    "ts": ts,
                    "ts_str": ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3] if ts else ts_str or "",
                    "scope": raw_data.get("section", ""),
                    "level": raw_data.get("level", ""),
                    "body": raw_data,
                })
            else:
                # Na wszelki wypadek potraktuj jako zwykłe linie
                file_type = "log"
                raw_data = [str(raw_data)]

        # 2) Plik .jsonl (linia po linii JSON)
        if file_type in ("jsonl",) or (file_type == "log" and any(line.strip().startswith("{") for line in raw_data)):
            tmp_entries = []
            for line in raw_data:
                line = line.rstrip("\n")
                try:
                    obj = json.loads(line)
                    ts_str = obj.get("timestamp", "")
                    ts = _parse_ts(ts_str) if ts_str else None
                    tmp_entries.append({
                        "ts": ts,
                        "ts_str": ts.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3] if ts else ts_str or "",
                        "scope": obj.get("section", ""),
                        "level": obj.get("level", obj.get("severity", "")),
                        "body": obj,
                    })
                except Exception:
                    # nie-JSON — zostaw do dalszej obróbki „logowej”
                    pass
            if tmp_entries:
                entries.extend(tmp_entries)

        # 3) Zwykłe logi tekstowe (z „-” lub z „|”)
        if file_type == "log":
            prev_idx = None
            for line in raw_data:
                line = line.rstrip("\n")

                # a) Wariant „|”: YYYY...ms | scope | LEVEL | msg
                m_pipe = re.match(
                    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3,6})\s*\|\s*(?P<scope>[^|]+)\s*\|\s*(?P<level>[^|]+)\s*\|\s*(?P<body>.*)$",
                    line
                )

                if m_pipe:
                    ts = _parse_ts(m_pipe.group("ts"))
                    entries.append({
                        "ts": ts,
                        "ts_str": m_pipe.group("ts"),
                        "scope": m_pipe.group("scope").strip(),
                        "level": m_pipe.group("level").strip(),
                        "body": m_pipe.group("body"),
                    })
                    prev_idx = len(entries) - 1
                    continue

                # b) Wariant „-”: YYYY...ms - LEVEL - msg
                m_dash = re.match(
                    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3,6})\s*-\s*(?P<level>[A-Z]+)\s*-\s*(?P<body>.*)$",
                    line
                )

                if m_dash:
                    ts = _parse_ts(m_dash.group("ts"))
                    entries.append({
                        "ts": ts,
                        "ts_str": m_dash.group("ts"),
                        "scope": "",
                        "level": m_dash.group("level").strip(),
                        "body": m_dash.group("body"),
                    })
                    prev_idx = len(entries) - 1
                    continue

                # c) Linia-detal (np. { 'project_id': ... }) – dołącz do poprzedniej, jeśli była
                if prev_idx is not None and _line_looks_like_dict(line):
                    # Dołącz „na czysto”, bez próby json.loads (może być `'` zamiast `"`).
                    entries[prev_idx]["body"] = f"{entries[prev_idx]['body']}\n{line}"
                    continue

                # d) Nieparsowalna linia — zachowaj jako „bez TS”
                entries.append({
                    "ts": None,
                    "ts_str": "",
                    "scope": "",
                    "level": "",
                    "body": line,
                })
                prev_idx = len(entries) - 1

        # Ostateczne sortowanie: najpierw te z timestampem malejąco, potem bez TS
        entries.sort(key=lambda e: (e["ts"] is not None, e["ts"] or datetime.min), reverse=True)

        # Legacy: jeśli chcesz coś jeszcze pokazać starym blokiem, zbuduj „file_content_lines”
        file_content_lines = [f"{e['ts_str']} | {e['scope']} | {e['level']} | {e['body']}" if e["ts_str"] else str(e["body"]) for e in entries]

    elif selected_file != "brak_plikow":
        file_content_lines = [f"❌ Nie znaleziono pliku: {selected_file}"]
    else:
        file_content_lines = ["❌ Nie znaleziono plików logów (.log, .json, .jsonl) w monitorowanych folderach."]

    # from flask import jsonify
    # json_line = jsonify(file_content_lines[1])
    # print(f'\n\t\tfile_content_lines len={len(file_content_lines)} type={type(file_content_lines)} \n{type(file_content_lines[1])} jsonify={json_line}')
   
    fcn = 0
    for l in all_files_with_mtime_by_folder:
        fcn += len(all_files_with_mtime_by_folder[l])
        # print(f'{l} {len(all_files_with_mtime_by_folder[l])}')
    
    return render_template(
        "webutils/logs.html",
        all_files_with_mtime_by_folder=all_files_with_mtime_by_folder,
        len_all_files_with_mtime_by_folder=fcn,
        file_type=file_type,
        file_content_json=file_content_json,
        file_content_lines=file_content_lines,
        parsed_data=parsed_data,
        current_time=now,
        selected_file=selected_file,
        log_dir=ALLOWED_LOGS_DIR,
        log_path=log_path,
        result=result,
        entries=entries
    )

@webutils_bp.route("/viewfile")
@login_required(role=["admin"])
def view_file():
    rel_path = request.args.get('filepath')
    file_type = request.args.get('file_type')
    if not rel_path:
        return "Brak parametru ?filepath=", 400

    # 🔁 Jeśli adres URL – przekieruj
    if rel_path.startswith("http://") or rel_path.startswith("https://"):
        return redirect(rel_path)
    
    # 🔒 Ścieżka lokalna – kontynuuj
    full_path = os.path.abspath(os.path.join(BASE_DIR, rel_path))
    
    if not is_path_allowed(full_path):
        abort(403, description="Nieautoryzowany dostęp do pliku")

    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        return "Plik nie istnieje", 404

    if not is_allowed_file(full_path):
        return "Niedozwolone rozszerzenie pliku", 415

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            ext = os.path.splitext(full_path)[1]
            if ext == '.json':
                content = json.load(f)
                return jsonify(content)
            elif ext == '.jsonl':
                lines = [json.loads(line) for line in f if line.strip()]
                return jsonify(lines)
            else:  # .log lub inne tekstowe
                content = f.read()
                return render_template('view_file/log_view.html', content=content, filename=rel_path)
    except Exception as e:
        return f"Błąd podczas odczytu pliku: {e}", 500




@webutils_bp.route("/files")
@login_required(role=["admin"])
def show_files():
    from config import BASE_DIR
    
    printLog(f'BASE_DIR==>{BASE_DIR}')
    base_dir = BASE_DIR

    ignore_list = {".git", ".env", "venv", "__pycache__", "__init__.py"}

    rel_path = request.args.get("path", "")
    full_path = os.path.normpath(os.path.join(base_dir, rel_path))

    printLog(f'full_path==>{full_path}')

    if not full_path.startswith(base_dir):
        return "⛔ Niedozwolony dostęp.", 403

    file_info_list = []
    try:
        entries = sorted(os.listdir(full_path))
    except Exception as e:
        return f"Błąd odczytu katalogu: {e}", 500

    printLog(f'entries==>{entries}')

    for entry in entries:
        if entry in ignore_list:
            continue

        path = os.path.join(full_path, entry)
        try:
            stat = os.stat(path)

            if os.name == 'posix':
                import pwd, grp
                owner = pwd.getpwuid(stat.st_uid).pw_name
                group = grp.getgrgid(stat.st_gid).gr_name
            else:
                owner = group = "Windows"

            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

            file_info_list.append({
                "name": entry,
                "is_dir": os.path.isdir(path),
                "mtime": mtime,
                "owner": owner,
                "group": group,
                "rel_path": os.path.relpath(path, base_dir)
            })

        except Exception as e:
            file_info_list.append({
                "name": entry,
                "is_dir": False,
                "mtime": "error",
                "owner": "error",
                "group": str(e),
                "rel_path": ""
            })

    return render_template("webutils/files.html", files=file_info_list, current_path=rel_path)



def show_files_depr():
    from config import BASE_DIR
    
    printLog(f'BASE_DIR==>{BASE_DIR}')
    # base_dir = os.path.dirname(__file__)
    base_dir = BASE_DIR

    ignore_list = {".git", ".env", "venv", "__pycache__", "__init__.py"}
    # {"static", "templates"}

    # ⬇️ Parametr URL ?path=subdir1/subdir2
    rel_path = request.args.get("path", "")

    full_path = os.path.normpath(os.path.join(base_dir, rel_path))

    printLog(f'full_path==>{full_path}')

    # 🛡️ Zabezpieczenie: nie wyjdziesz poza bazowy katalog
    if not full_path.startswith(base_dir):
        return "⛔ Niedozwolony dostęp.", 403

    file_info_list = []
    try:
        entries = sorted(os.listdir(full_path))
    except Exception as e:
        return f"Błąd odczytu katalogu: {e}", 500

    printLog(f'entries==>{entries}')

    for entry in entries:
        if entry in ignore_list:
            continue

        path = os.path.join(full_path, entry)
        try:
            stat = os.stat(path)
            owner = pwd.getpwuid(stat.st_uid).pw_name
            group = grp.getgrgid(stat.st_gid).gr_name
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

            file_info_list.append({
                "name": entry,
                "is_dir": os.path.isdir(path),
                "mtime": mtime,
                "owner": owner,
                "group": group,
                "rel_path": os.path.relpath(os.path.join(full_path, entry), base_dir)
            })

        except Exception as e:
            file_info_list.append({
                "name": entry,
                "is_dir": False,
                "mtime": "error",
                "owner": "error",
                "group": str(e),
                "rel_path": ""
            })

    return render_template("webutils/files.html", files=file_info_list, current_path=rel_path)
