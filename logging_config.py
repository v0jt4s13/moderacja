# logging_config.py
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import pwd
import grp
import stat as statmod

from config import ALLOWED_LOGS_DIR

# Kolory ANSI
GREY = "\x1b[38;20m"
YELLOW = "\x1b[33;20m"
RED = "\x1b[31;20m"
BOLD_RED = "\x1b[31;1m"
RESET = "\x1b[0m"

def setup_logger(logger_name: str, log_file: str, level=logging.INFO) -> logging.Logger:
    print(f'\n\tSTART setup_logger() ==> {logger_name}', end=' ')
    
    FORMATS = {
        logging.DEBUG: GREY + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + RESET,
        logging.INFO: GREY + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + RESET,
        logging.WARNING: YELLOW + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + RESET,
        logging.ERROR: RED + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + RESET,
        logging.CRITICAL: BOLD_RED + "%(asctime)s - %(name)s - %(levelname)s - %(message)s" + RESET
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

    # Konfiguracja loggera    
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)

    # uniknij duplikowania handlerów przy wielokrotnym imporcie
    if logger.handlers:
        return logger

    log_path = f'{ALLOWED_LOGS_DIR}/{log_file}'
    # print(f'user ==> {_user_context_str()}\nlog_path ==> {log_path}\n{_dir_status(log_path)}')
    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=5,
    )
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # opcjonalnie loguj też na stderr w dev
    if os.getenv("LOG_TO_STDERR", "0") == "1":
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    return logger
