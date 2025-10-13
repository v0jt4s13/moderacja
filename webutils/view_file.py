import os
from config import ALLOWED_EXTENSIONS, ALLOWED_LOGS_DIR

def is_path_allowed(path):
    path = os.path.abspath(path)
    return any(path.startswith(os.path.abspath(base)) for base in ALLOWED_LOGS_DIR)

def is_allowed_file(path):
    _, ext = os.path.splitext(path)
    return ext in ALLOWED_EXTENSIONS

