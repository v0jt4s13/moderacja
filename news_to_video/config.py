import os
import concurrent.futures
from typing import Optional
# import threading
# import time
from apps_utils.s3_utils import (
    s3_session
)

BASE_DIR = os.path.dirname(__file__)
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
os.makedirs(PROJECTS_DIR, exist_ok=True)
# print(f'PROJECTS_DIR====PROJECTS_DIR=====>{PROJECTS_DIR}')

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VID_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

FORMAT_PRESETS = {
    "16x9": (1920, 1080),
    "1x1":  (1080, 1080),
    "9x16": (1080, 1920),
}

SUPPORTED_RENDERERS = {"local", "shotstack", "json2video", "mediaconvert", "openshot", "openai_sora"}

VIDEO_S3_BUCKET = os.getenv("VIDEO_S3_BUCKET", "").strip()
VIDEO_S3_PREFIX = os.getenv("VIDEO_S3_PREFIX", "").strip().strip("/")  # np. "news_to_video"
VIDEO_S3_BASE_URL = os.getenv("VIDEO_S3_BASE_URL", "").rstrip("/")     # np. "https://cdn.example.com"
S3_ENABLED = bool(VIDEO_S3_BUCKET)

RENDER_MAX_WORKERS = int(os.getenv("RENDER_MAX_WORKERS", "2"))
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=RENDER_MAX_WORKERS)
_ACTIVE_JOBS = {}  # project_id -> Future



import json
test_data = dict()
test_data = {
    'entry1': {
      'title': 'Brytyjska sieć energetyczna zabezpieczona przed awariami. "Nie ma szans na blackout"',
      'description': 'Brytyjska sieć energetyczna jest bezpieczna i odporna na awarie. Poznaj szczegóły zabezpieczeń na londynek.net!',
      'images': [
        'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433496-202509151415-lg.jpg',
        'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433495-202509151412-lg2.jpg',
        'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433497-202509151416-lg.jpg',
        'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433496-202509151415-lg.jpg'
      ],
      'main_image': 'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433496-202509151415-lg.jpg'
    },
    'entry2': {
      'title': 'Bliźniacy z hrabstwa Hampshire w Anglii wyhodowali najcięższą i największą dynię na świecie',
      'description': 'Bliźniacy z hrabstwa Hampshire w Anglii wyhodowali największą i najcięższą dynię na świecie podczas szóstej edycji oficjalnego ważenia gigantycznych dyń w Reading, Berkshire. Ich dynia, rosnąca około 130 dni, przebiła rekord poprzednio należący do Stanów Zjednoczonych. Kluczem do sukcesu były dobre geny oraz system nawadniania kropelkowego, który dostarczał do 500 litrów wody dziennie, mimo obowiązującego zakazu podlewania spowodowanego suszą. Uprawa dyni stała się rodzinną tradycją – braciom pomagały ich wnuczki, które z entuzjazmem rozmawiały nawet z roślinami. Ten sukces pokazuje, że brytyjscy hodowcy mogą rywalizować z najlepszymi na świecie w dziedzinie gigantycznych warzyw.',
      'images': [
        'https://assets.aws.londynek.net/images/jdnews-agency/2191248/434223-202509231020-lg2.jpg',
        'https://assets.aws.londynek.net/images/jdnews-agency/2191248/435423-202510071050-lg2.jpg',
        'https://assets.aws.londynek.net/images/jdnews-agency/2191248/435424-202510071106-lg.jpg',
        'https://assets.aws.londynek.net/images/jdnews-agency/2191248/435394-202510070207-lg2.jpg'
      ],
      'main_image': 'https://assets.aws.londynek.net/images/jdnews-agency/2191248/435394-202510070207-lg2.jpg'
    },
    'entry3': {
      'title': 'Polka uzyskuje prawie pół miliona funtów odszkodowania',
      'description': 'Polka, która doznała poważnych obrażeń w wypadku, otrzymała prawie pół miliona funtów odszkodowania dzięki wsparciu kancelarii Sintons. Sprawa pokazuje, jak ważny jest wybór odpowiedniego prawnika i kompleksowa pomoc. Poznaj szczegóły tej niezwykłej historii na londynek.net!',
      'images': [
        'https://assets.aws.londynek.net/images/jdnews/2251908/434749-202509282110-lg.jpg',
        'https://assets.aws.londynek.net/images/jdnews/2251908/434748-202509282108-m.jpg',
        'https://assets.aws.londynek.net/images/jdnews/2251908/434750-202509282119-m.jpg',
        'https://assets.aws.londynek.net/images/jdnews/logos/354764-202306111054-sm.jpg'
      ],
      'main_image': 'https://assets.aws.londynek.net/images/jdnews/2251908/434748-202509282108-m.jpg'
    },
    'entry4': {
      'title': 'Teorie spiskowe o ISS i lądowaniu na Księżycu nie znikają mimo dowodów naukowych',
      'description': 'Teorie spiskowe wokół ISS i lądowania na Księżycu nie ustają, mimo licznych dowodów naukowych. Czy zaawansowana technologia satelitarna wyjaśnia tajemnicze przerwy w łączności? Sprawdź, jak eksperci obalają mity i dlaczego sceptycy wciąż kwestionują fakty. Przeczytaj więcej na londynek.net!',
      'images': [
        'https://assets.aws.londynek.net/images/jdnews/2251908/428299-202507141257-lg.jpg',
        'https://assets.aws.londynek.net/images/jdnews/2251908/428300-202507141304-m.jpg',
        'https://assets.aws.londynek.net/images/jdnews/2251908/428301-202507141307-m.jpg',
        'https://assets.aws.londynek.net/images/jdnews/2251908/428302-202507141310-m.jpg',
        'https://assets.aws.londynek.net/images/jdnews/2251908/428303-202507141315-m.jpg'
      ],
      'main_image': 'https://assets.aws.londynek.net/images/jdnews/2251908/428299-202507141257-lg.jpg'
    },
}
