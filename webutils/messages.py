
import os
import sys
import requests

from loggers import webutils_messages_logger

# webutils_messages_logger.info('AAAAAAA SEND TELEGRAM MESSAGE AAAAAAAAAAAAAa')
# Możesz ustawić je jako zmienne środowiskowe albo wpisać na sztywno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or "7667182309:AAFsBPGwNIggbjk73IwO8c0BRt9agK219hs"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or -4878708567

from config import HOME_DIR, TELEGRAM_MSG_SEND

def send_telegram_message(text: str):
    webutils_messages_logger.info(f'START send_telegram_message({text})')
    """Wysyła wiadomość do grupy Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    try:
        
        if TELEGRAM_MSG_SEND:
            response = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })
            if not response.ok:
                webutils_messages_logger.info(f"Telegram error: {response.status_code} {response.text}")
                
        else:
            webutils_messages_logger.info(f"Telegram response wyłączony w pliku webutils/messages.py")
            

    except Exception as e:
        webutils_messages_logger.error(f"❌ [BŁĄD] [TELEGRAM]: {e}")

    # print(f' ✅ Wiadomość do telegram została wysłana.')
# ######################################################################
# ####################################################################
# ##################### send_telegram_message #####################
# ####################################################################
# ######################################################################
    # payload = {
    #     "chat_id": chat_id,
    #     "text": text,
    #     "parse_mode": "HTML",
    #     "disable_web_page_preview": True
    # }
    # try:
    #     requests.post(url, data=payload, timeout=10)
    # except Exception as e:
    #     print(f"❌ [BŁĄD] [TELEGRAM]: {e}")
# ######################################################################
# ####################################################################
# ##################### send_telegram_message #####################
# ####################################################################
# ######################################################################
