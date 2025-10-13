import os
from functools import wraps
from flask import redirect, session, url_for, abort, request, has_request_context
import logging
from config import LOGS_DIR_PATH
from datetime import datetime

# Uzyskanie loggera dla tego modułu, z nazwy pliku
logger = logging.getLogger(__name__)

_ALWAYS_ALLOWED_ROLES = {"fox"}


def login_required(redirect_to="login", role=None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            user = session.get("user")
            user_role = session.get("role")
            if not user:
                return redirect(url_for(redirect_to))
            
            # Sprawdzanie roli:
            if role:
                if isinstance(role, list):
                    allowed_roles = set(role) | _ALWAYS_ALLOWED_ROLES
                    if user_role not in allowed_roles:
                        abort(403)
                else:
                    if user_role not in ({role} | _ALWAYS_ALLOWED_ROLES):
                        abort(403)

            # Logowanie wejścia
            log_entry_access()

            return view_func(*args, **kwargs)
        return wrapper
    return decorator

def log_entry_access(page=None):
    """
    Loguje wejście na chronioną stronę.
    """
    if has_request_context():
        try:
            ip = request.remote_addr or 'unknown_ip'
            user = session.get("user", "guest")
            page = request.path
            browser_header = request.headers.get('User-Agent', 'unknown_agent')
            
            log_message = f'Access | {ip} | {page} | {user} | {browser_header}'
            logger.info(log_message)
        except RuntimeError:
            # W przypadku braku kontekstu (np. skrypt crona)
            logger.info('Access | crontab | unknown_path | system | crontab')

