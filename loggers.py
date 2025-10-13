# loggers.py
import sys
import json
import datetime
import logging
from logging_config import setup_logger

_LEVELS = {
    "debug":    logging.DEBUG,
    "info":     logging.INFO,
    "warning":  logging.WARNING,
    "error":    logging.ERROR,
    "critical": logging.CRITICAL,
}

def logger(msg: str, level: str = "info", **fields):
    """
    Log strukturalny:
      - wypisuje JSON na stdout/stderr,
      - zapisuje ten sam JSON do pliku przez audiototext_logger.
    Użycie:
        logger("Job done", level="info", job_id=jid, result_path=path)
        logger("Job failed", level="error", job_id=jid, error=str(e))
    """
    level = (level or "info").lower()
    lvl = _LEVELS.get(level, logging.INFO)
    lvl_err = _LEVELS.get(level, logging.ERROR)

    rec = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "level": level,
        "msg": msg,
    }
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False)

    # 1) stdout/stderr (wygodne do podglądu i docker logs)
    stream = sys.stderr if level in ("error", "critical") else sys.stdout
    print(line, file=stream)

    # 2) plik przez standardowy logger
    # audiototext_logger.log(lvl, line)
    errors_logger.log(lvl_err, line)

    return rec

# Główne loggery modułów
errors_logger = setup_logger(
    'errors',
    'log-errors.log'
)

ads_moderation_logger = setup_logger(
    'ads_moderation', 
    'log-ads_moderation.log'
)

ai_moderation_auto_moderate_logger = setup_logger(
    'ai_moderation', 
    'log-crontab-e-ai_moderation_auto.log'
)

ai_moderation_logger = setup_logger(
    'ai_moderation', 
    'log-crontab-e-ai_moderation.log'
)

ai_moderation_rules_logger = setup_logger(
    'ai_moderation', 
    'log-ai_moderation_rules.log'
)

webutils_messages_logger = setup_logger(
    'webutils', 
    'log-webutils_messages.log'
)

auto_moderate_logger = setup_logger(
    'auto_moderate', 
    'log-crontab-e-forum.log'
)

news_reader_project_logger = setup_logger(
    'news_reader_project', 
    'log-news-reader-project.log'
)

webutils_routes_logger = setup_logger(
    'webutils', 
    'log-webutils_routes.log'
)

audiototext_routes_logger = setup_logger(
    'audiototext',
    'log-audiototext_routes.log'
)

audiototext_logger = setup_logger(
    'audiototext',
    'log-audiototext.log'
)

news_to_video_logger = setup_logger(
    'news_to_video',
    'log-news_to_video.log'
)

