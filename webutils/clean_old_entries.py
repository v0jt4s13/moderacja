import os
import sys
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

if os.name == "posix":
# try:
    import fcntl
elif os.name == "nt":
# except:
    import msvcrt

# === KONFIGURACJA ===
INPUT_FILE = '/home/vs/logs/moderation/ai_moderation_debug_log-bkp.jsonl'
DATE_LIMIT = datetime.now() - timedelta(days=5)
BACKUP_SUFFIX = datetime.now().strftime("%Y%m%d_%H%M%S")

def parse_timestamp(ts_str):
    """Próbuje sparsować timestamp z różnymi formatami ISO."""
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f",  # pełna precyzja (z mikrosekundami)
        "%Y-%m-%dT%H:%M:%S",     # bez mikrosekund
        "%Y-%m-%dT%H:%M"         # bez sekund
    ]
    for fmt in formats:
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None

def is_recent(entry):
    ts_str = entry.get("timestamp", "")
    dt = parse_timestamp(ts_str)
    if dt:
        return dt >= DATE_LIMIT
    return False

def clean_file(input_path):
    cleaned = []

    with open(input_path, "r", encoding="utf-8") as infile:
        try:
            if os.name == "posix":
                fcntl.flock(infile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == "nt":
                msvcrt.locking(infile.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, OSError):
            print("Plik jest używany przez inny proces.")
            return

        for line in infile:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if is_recent(entry):
                    cleaned.append(entry)
            except json.JSONDecodeError as e:
                print(f"Błąd JSON: {e} w linii: {line}")

        # Unlock
        try:
            if os.name == "posix":
                fcntl.flock(infile.fileno(), fcntl.LOCK_UN)
            elif os.name == "nt":
                infile.seek(0)
                msvcrt.locking(infile.fileno(), msvcrt.LK_UNLCK, 1)
        except:
            pass

    shutil.copy2(input_path, f"{input_path}.bak_{BACKUP_SUFFIX}")
    # Po zapisaniu kopii nadpisujemy plik źródłowy świeżymi rekordami
    with open(input_path, "w", encoding="utf-8") as original:
        for entry in cleaned:
            original.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return 12

    
    # return f"Zaktualizowano {len(cleaned)} rekordów w pliku: {input_path.name}"

# if __name__ == "__main__":
#     input_path = Path(INPUT_FILE)
#     if not input_path.exists():
#         print(f"Błąd: plik {INPUT_FILE} nie istnieje.")
#     else:
#         clean_file(input_path)


