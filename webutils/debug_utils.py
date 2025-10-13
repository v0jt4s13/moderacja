import os
import sys
from pathlib import Path
import json
from datetime import datetime
import inspect

HOME_DIR = os.getenv("HOME_DIR")

def printLog(msg: str, log_file_path=None):
    """
    Zapis 1 linii do /tmp/news_reader_debug.log w UTF-8.
    Format: filename.py | YYYY-mm-dd HH:MM:SS | treść
    """
    try:
        print(message)
    except:
        pass
    
    try:
        frm = inspect.stack()[1]
        fname = os.path.basename(frm.filename)
    except Exception:
        fname = "?"
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{fname} | {ts} | {msg}\n"

    log_file = log_file_path or f"{HOME_DIR}/logs/moderation/moderacja_ldnk_utils.log"
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        # kluczowe: encoding="utf-8", errors="replace"
        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            f.write(line)
    except Exception as e:
        # ostatnia deska ratunku: standard error (też w utf-8 jeśli się da)
        try:
            sys.stderr.write(f"❌ [LOG-FAIL] {e} while logging: {line}")
        except Exception:
            pass

def printLog_depr(message, log_file_path=None):
    try:
        print(message)
        # domyślna ścieżka
        log_file = log_file_path or f"{HOME_DIR}/logs/moderation/moderacja_ldnk_utils.log"

        # upewnij się, że ścieżka to string
        if not isinstance(log_file, (str, os.PathLike)):
            raise ValueError(f"log_file musi być stringiem: {log_file}")

        # nazwę pliku, z którego log pochodzi
        caller = inspect.stack()[1].filename
        caller_file = os.path.basename(caller)

        # timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"{caller_file} | {timestamp} | {message}\n"

        with open(log_file, "a") as f:
            f.write(log_entry)

    except Exception as e:
        print(f"❌ [webutils/debug_utils.py] Nie można zapisać logu: {e}")

def log_moderation_action(action, moderator=None, target_user_id=None, extra=None, ip=None, log_file_path=None, description=None):
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "moderator": moderator,
        "target_user_id": target_user_id,
        "extra": extra,
        "ip": ip,
        "description": description
    }

    log_file = log_file_path or f"{os.environ.get('HOME')}/logs/mod_log.jsonl" 
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")