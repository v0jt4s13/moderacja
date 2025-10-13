# news_to_video/render_video.py
import os
import re
import json
from pydub import AudioSegment
from typing import Any, List, Dict, Optional, Tuple
import time
import requests
from urllib.parse import urlparse
from news_to_video.main import (
    segment_text,
    synthesize_tts,
    profile_for,
    prepare_media_segments,
    generate_srt, 
    generate_ass_from_timeline,
    update_manifest,
    _ffprobe_duration,
    _write_srt_by_chunks, 
    _ensure_remote_url,
    _encode_asset_url,
    _effective_visual_duration,
    _make_color_segment,
    TTSSettings,
    TransitionsConfig,
    RenderProfile,
    BrandConfig,
    MediaItem
)
from loggers import news_to_video_logger
from news_to_video.render_video import FORMAT_PRESETS
from news_to_video.renders_engines.s3_proc import (
    save_json,
    load_json
)
from news_to_video.renders_engines.helpers_proc import (
    detect_media_type
)

# news_to_video/main.py — mapowanie do dozwolonych przejść Shotstack
SHOTSTACK_ALLOWED_TRANSITIONS = {
    "none","fade","fadeSlow","fadeFast","reveal","revealSlow","revealFast",
    "wipeLeft","wipeLeftSlow","wipeLeftFast","wipeRight","wipeRightSlow","wipeRightFast",
    "slideLeft","slideLeftSlow","slideLeftFast","slideRight","slideRightSlow","slideRightFast",
    "slideUp","slideUpSlow","slideUpFast","slideDown","slideDownSlow","slideDownFast",
    "carouselLeft","carouselLeftSlow","carouselLeftFast","carouselRight","carouselRightSlow","carouselRightFast",
    "carouselUp","carouselUpSlow","carouselUpFast","carouselDown","carouselDownSlow","carouselDownFast",
    "shuffleTopRight","shuffleTopRightSlow","shuffleTopRightFast","shuffleRightTop","shuffleRightTopSlow","shuffleRightTopFast",
    "shuffleRightBottom","shuffleRightBottomSlow","shuffleRightBottomFast","shuffleBottomRight","shuffleBottomRightSlow","shuffleBottomRightFast",
    "shuffleBottomLeft","shuffleBottomLeftSlow","shuffleBottomLeftFast","shuffleLeftBottom","shuffleLeftBottomSlow","shuffleLeftBottomFast",
    "shuffleLeftTop","shuffleLeftTopSlow","shuffleLeftTopFast","shuffleTopLeft","shuffleTopLeftSlow","shuffleTopLeftFast",
    "zoom"
}

SHOTSTACK_DEFAULT_FONT_SRC = "https://assets-static.londynek.net/assets/fonts/googleapi/RobotoCondensed/static/RobotoCondensed-Bold.ttf"
SHOTSTACK_DEFAULT_FONT_FAMILY = "Roboto Condensed"
# SHOTSTACK_FALLBACK_OVERLAY = "https://shotstack-ingest-api-v1-sources.s3.ap-southeast-2.amazonaws.com/iz2r9ced09/zzz01k6j-rnbmm-0pf9r-1srmn-zjprqj/source.png"
SHOTSTACK_FALLBACK_OVERLAY = "https://jdblayer-assets-static.s3.eu-west-2.amazonaws.com/londynek/assets/video_template/overlay_foreground.png"
# SHOTSTACK_FALLBACK_LUMA = "https://templates.shotstack.io/basic/asset/video/luma/double-arrow/double-arrow-down.mp4"
SHOTSTACK_FALLBACK_LUMA = "https://jdblayer-assets-static.s3.eu-west-2.amazonaws.com/londynek/assets/video_template/transition-double-arrow-down.mp4"

# aliasy z naszego UI/ffmpeg → dozwolone nazwy Shotstack
SHOTSTACK_TRANSITION_ALIAS = {
    "fade": "fade",
    "smoothleft": "slideLeft",
    "wipeleft": "wipeLeft",
    "wiperight": "wipeRight",
    "slideleft": "slideLeft",
    "slideright": "slideRight",
    "circleopen": "zoom",    # brak w Shotstack; najbliższy efekt
    "squeezeh": "reveal",    # brak w Shotstack; najbliższy efekt
    "reveal": "reveal",
}

SHOTSTACK_HOSTS = {
    # env -> region -> host
    'stage': {'eu1': 'api.shotstack.io/stage', 'us1': 'api.shotstack.io/stage', 'au1': 'api.shotstack.io/stage'},
    'v1':    {'eu1': 'api.shotstack.io/v1',    'us1': 'api.shotstack.io/v1',    'au1': 'api.shotstack.io/v1'},
}

SHOTSTACK_API_KEY = os.getenv("SANDBOX_SHOTSTACK_API_KEY") or os.getenv("PROD_SHOTSTACK_API_KEY") or ""
SHOTSTACK_ENV = (os.getenv("SHOTSTACK_ENV") or "v1").strip()  # "v1" (prod) lub "stage" (sandbox)
BASE_EDIT_URL = f"https://api.shotstack.io/edit/{SHOTSTACK_ENV}"
BASE_EDIT_URL = f"https://api.shotstack.io/stage"


# --- 8) Render dla formatów ---
fmt_map = {
    "16x9": {"aspectRatio": "16:9", "resolution": "1080"},
    "1x1":  {"aspectRatio": "1:1",  "resolution": "1080"},
    "9x16": {"aspectRatio": "9:16", "resolution": "1080"},
}
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "x-api-key": SHOTSTACK_API_KEY,
}

def validate_shotstack_form(form):
    news_to_video_logger.info(f'\n\t\t✅ START ==> validate_shotstack_form({form})\n')
    api_key = (form.get('shotstack_api_key') or SHOTSTACK_API_KEY or '').strip()
    env = (form.get('shotstack_env') or SHOTSTACK_ENV or '').strip().lower()
    region = (form.get('shotstack_region') or '').strip().lower()
    preset = (form.get('shotstack_preset') or '').strip()
    webhook = (form.get('shotstack_webhook_url') or '').strip()

    errors = []
    if not api_key:
        errors.append('Brak Shotstack API Key.')
    if env not in SHOTSTACK_HOSTS:
        errors.append('Niepoprawne środowisko Shotstack (dozwolone: stage, v1).')
    if env in SHOTSTACK_HOSTS and region not in SHOTSTACK_HOSTS[env]:
        errors.append('Niepoprawny region Shotstack (eu1/us1/au1).')

    if webhook:
        try:
            u = urlparse(webhook)
            if u.scheme not in ('http', 'https') or not u.netloc:
                errors.append('Webhook URL ma nieprawidłowy format.')
        except Exception:
            errors.append('❌ [validate_shotstack_form] Webhook URL ma nieprawidłowy format.')

    allowed_presets = {
        '9x16_vertical',
        '16x9_horizontal',
        '1x1_square',
        '720p_30',
        '1080p_30',
        '1080p_60',
        '4k_30',
    }
    if preset not in allowed_presets:
        errors.append('Niepoprawny preset Shotstack.')

    host = None
    if not errors:
        host = SHOTSTACK_HOSTS[env][region]

    return errors, {
        'api_key': api_key,
        'env': env,
        'region': region,
        'preset': preset,
        'webhook': webhook or None,
        'host': host,
    }


# główna funkcja renderowania przez Shotstack
def render_via_shotstack(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict[str, Any]:
    """
    Render przez Shotstack (patrz opis w wersji użytkownika).
    """
    print(f'\n\t\tSTART ==> render_via_shotstack({project_dir})')
    profile = profile or RenderProfile()

    # --- Bezpieczne I/O + małe utilsy ---
    def _read_manifest(pdir: str) -> dict:
        mpath = os.path.join(pdir, "manifest.json")
        m = load_json(mpath) or {}
        return m

    def _save_manifest(pdir: str, m: dict) -> None:
        mpath = os.path.join(pdir, "manifest.json")
        save_json(mpath, m)

    def _http_post_json(url: str, js: dict, hdrs: dict, timeout: int = 30) -> dict:
        r = requests.post(url, json=js, headers=hdrs, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Shotstack POST {url} -> {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"Shotstack POST {url} -> invalid JSON")

    def _http_get_json(url: str, hdrs: dict, timeout: int = 20) -> dict:
        r = requests.get(url, headers=hdrs, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Shotstack GET {url} -> {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"Shotstack GET {url} -> invalid JSON")

    def _download_file(url: str, dest_path: str, timeout: int = 120) -> None:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            os.makedirs(os.path.dirname(dest_path), exist_ok=True
                        )
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)

    # --- 1) Manifest/payload ---
    manifest = _read_manifest(project_dir)
    print(manifest)
    # {
    #     'created_at': '2025-09-29T07:02:30.283ZZ', 
    #     'error': None, 'logs': [], 'outputs': {}, 
    #     'payload': {
    #         'title': 'TEST - Brytyjska sieć energetyczna zabezpieczona przed awariami. "Nie ma szans na blackout"', 
    #         'text': 'TEST TEST TEST\nBrytyjska sieć energetyczna jest bezpieczna i odporna na awarie. Poznaj szczegóły zabezpieczeń na londynek.net!', 
    #         'media': [
    #             {'type': 'image', 'src': 'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433495-202509151412-lg2.jpg'}, 
    #             {'type': 'image', 'src': 'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433497-202509151416-lg.jpg'}, 
    #             {'type': 'image', 'src': 'https://assets.aws.londynek.net/images/jdnews-lite/2719994/433496-202509151415-lg.jpg'}, 
    #             {'type': 'image', 'src': 'https://assets.aws.londynek.net/images/jdnews-agency/2191248/434223-202509231020-lg2.jpg'}
    #         ], 
    #         'formats': ['16x9'], 
    #         'renderer': {
    #             'type': 'shotstack', 
    #             'config': {
    #                 'output': {
    #                     'format': 'mp4', 
    #                     'size': {'width': 1920, 'height': 1080}, 
    #                     'fps': 25
    #                 }, 
    #                 'callback': None, 
    #                 'captions_style': {
    #                     'font': {'family': None, 'size': 20, 'color': '#ffff00', 'stroke': None, 'strokeWidth': 0.5}, 
    #                     'background': {'color': '#008000', 'opacity': 0.6}
    #                 }
    #             }
    #         }, 
    #         'subtitles': {'burn_in': True}, 
    #         'transitions': {'use_xfade': True, 'transition': 'fade', 'duration': 1.5}, 
    #         'tts': {'language': 'pl', 'provider': 'google', 'speed': 1, 'voice': 'pl-PL-Chirp3-HD-Achernar'}, 
    #         'brand': {'logo_path': '', 'opacity': 0.85, 'position': 'top-right', 'scale': 0.15}
    #     }, 
    #     'project_id': 'proj-20250929-070232', 
    #     'status': 'processing', 
    #     'title': 'TEST - Brytyjska sieć energetyczna zabezpieczona przed awariami. "Nie ma szans na blackout"'
    # }


    payload = manifest.get("payload", {}) or {}
    renderer_cfg = (payload.get("renderer") or {}).get("config") or {}

    api_key = (renderer_cfg.get("shotstack_api_key") or SHOTSTACK_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("Shotstack: brak API key w konfiguracji ani w zmiennych środowiskowych.")

    env_cfg = (renderer_cfg.get("env") or SHOTSTACK_ENV or "v1").strip().lower()
    region_cfg = (renderer_cfg.get("region") or "").strip().lower()
    host_cfg = (renderer_cfg.get("host") or "").strip()

    if host_cfg:
        if host_cfg.startswith(("http://", "https://")):
            base_api = host_cfg.rstrip("/")
        else:
            base_api = f"https://{host_cfg}".rstrip("/")
    else:
        resolved_host = None
        env_hosts = SHOTSTACK_HOSTS.get(env_cfg)
        if env_hosts and region_cfg in env_hosts:
            resolved_host = env_hosts[region_cfg]
        elif env_hosts:
            # fallback: pierwszy dostępny region dla środowiska
            resolved_host = next(iter(env_hosts.values()))
        if not resolved_host:
            resolved_host = f"api.shotstack.io/{env_cfg}"
        base_api = f"https://{resolved_host}".rstrip("/")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-api-key": api_key,
    }

    # Efekty/brand/tts tak jak w lokalnym rendererze
    tts_cfg = payload.get("tts", {}) or {}
    tts = TTSSettings(**tts_cfg) if isinstance(tts_cfg, dict) else TTSSettings()
    burn_subs = bool(payload.get("subtitles", {}).get("burn_in", True))

    formats = payload.get("formats") or ["16x9"]
    if isinstance(formats, str):
        formats = [formats]
    formats = [f for f in formats if f in fmt_map.keys()] or ["16x9"]


    # print('formats 4')
    # print(formats)
    # print('\n\n\n\t\t\t STOP / PAUZA \n\n\n')
    # exit()


    # --- 2) TTS + SRT/ASS (lokalnie) ---
    segments = segment_text(payload.get("text") or "")
    audio_dir = os.path.join(project_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    audio_path, tts_timeline = synthesize_tts(segments, tts, audio_dir)
    audio_duration = _ffprobe_duration(audio_path) or 0.0

    out_dir = os.path.join(project_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    srt_path = os.path.join(out_dir, "captions.srt")
    ass_path = os.path.join(out_dir, "captions.ass")
    generate_srt(tts_timeline, srt_path)
    generate_ass_from_timeline(tts_timeline, profile, ass_path, max_words=5, min_chunk_dur=0.7)

    # publiczny URL do audio (soundtrack.src)
    tts_url = _ensure_remote_url(audio_path)

    # --- 3) "Form" dla build_shotstack_timeline na bazie manifestu ---
    try:
        caption_url = _ensure_remote_url(srt_path)
    except Exception as e:
        caption_url = None
        news_to_video_logger.warning("Shotstack: captions upload failed (%s): %s", srt_path, e)

    def _resolve_asset_url(value: Any) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                resolved_item = _resolve_asset_url(item)
                if resolved_item:
                    return resolved_item
            return None
        if not isinstance(value, str):
            value = str(value or "").strip()
        else:
            value = value.strip()
        if not value:
            return None
        if re.match(r"^https?://", value, re.I):
            return value
        try:
            return _ensure_remote_url(value)
        except Exception as exc:
            news_to_video_logger.warning("Shotstack: asset upload failed for %s (%s)", value, exc)
            return None

    template_cfg = renderer_cfg.get("template") or {}

    caption_cfg = template_cfg.get("caption") or {}
    caption_context = {
        "src": caption_url,
        "font_family": caption_cfg.get("font_family") or caption_cfg.get("family"),
        "font_size": caption_cfg.get("font_size"),
        "line_height": caption_cfg.get("line_height"),
        "color": caption_cfg.get("color"),
        "background": caption_cfg.get("background") or {},
        "stroke": caption_cfg.get("stroke") or {},
        "position": caption_cfg.get("position", "center")
    }

    fonts_raw = template_cfg.get("font_src")
    if isinstance(fonts_raw, str):
        font_sources = [fonts_raw.strip()]
    elif isinstance(fonts_raw, list):
        font_sources = [str(f).strip() for f in fonts_raw if str(f).strip()]
    else:
        font_sources = []
    if not font_sources:
        font_sources = [SHOTSTACK_DEFAULT_FONT_SRC]

    brand = payload.get("brand") or {}
    logo_cfg = template_cfg.get("logo") or {}
    logo_src = _resolve_asset_url(logo_cfg.get("src") or brand.get("logo_path"))
    logo_context = None
    if logo_src:
        offset_cfg = logo_cfg.get("offset") or {}
        offset_x = offset_cfg.get("x")
        offset_y = offset_cfg.get("y")
        if offset_x is None:
            offset_x = logo_cfg.get("offset_x")
        if offset_y is None:
            offset_y = logo_cfg.get("offset_y")
        logo_context = {
            "src": logo_src,
            "scale": logo_cfg.get("scale") or brand.get("scale") or 0.05,
            "position": logo_cfg.get("position") or "center",
            "offset": {k: v for k, v in {"x": offset_x, "y": offset_y}.items() if v is not None}
        }

    overlays_context: List[Dict[str, Any]] = []
    overlays_cfg = template_cfg.get("overlays") or {}
    foreground_cfg = overlays_cfg.get("foreground") or {}
    fg_src_input = foreground_cfg.get("src")
    foreground_src = _resolve_asset_url(fg_src_input)
    if fg_src_input and not foreground_src:
        news_to_video_logger.warning("Shotstack overlay unreachable (%s); using fallback", fg_src_input)
        foreground_src = SHOTSTACK_FALLBACK_OVERLAY
    if foreground_src:
        fg_offset_cfg = foreground_cfg.get("offset") or {}
        overlays_context.append({
            "src": foreground_src,
            "opacity": foreground_cfg.get("opacity"),
            "scale": foreground_cfg.get("scale"),
            "position": foreground_cfg.get("position", "center"),
            "offset": {k: v for k, v in {"x": fg_offset_cfg.get("x"), "y": fg_offset_cfg.get("y")}.items() if v is not None}
        })

    gallery_override = template_cfg.get("gallery")
    gallery_sources: List[str] = []
    if isinstance(gallery_override, list) and gallery_override:
        for item in gallery_override:
            resolved = _resolve_asset_url(item)
            if resolved:
                gallery_sources.append(resolved)
    else:
        for media_item in (payload.get("media") or []):
            src = media_item.get("src")
            if not src:
                continue
            media_type = str(media_item.get("type") or "").lower()
            if media_type and media_type not in ("image", "video"):
                continue
            resolved = _resolve_asset_url(src)
            if resolved:
                gallery_sources.append(resolved)
    if not gallery_sources and logo_src:
        gallery_sources.append(logo_src)

    slide_cfg = template_cfg.get("slide") or {}
    luma_input_src = template_cfg.get("luma_src")
    luma_src = _resolve_asset_url(luma_input_src)
    if luma_input_src and not luma_src:
        news_to_video_logger.warning("Shotstack luma unreachable (%s); using fallback", luma_input_src)
        luma_src = SHOTSTACK_FALLBACK_LUMA
    luma_length = template_cfg.get("luma_length", 2.0)

    title_cfg = template_cfg.get("title") or {}
    subtitle_cfg = template_cfg.get("subtitle") or {}
    subtitle_text = subtitle_cfg.get("text") or template_cfg.get("subtitle_text") or ""

    placeholders = {}
    title_text = title_cfg.get("text") or payload.get("title") or manifest.get("title")
    if title_text:
        placeholders["TITLE"] = title_text
    if subtitle_text:
        placeholders["SUBTITLE"] = subtitle_text

    soundtrack_cfg = template_cfg.get("soundtrack") or {}
    soundtrack_context = None
    if tts_url:
        soundtrack_context = {
            "src": tts_url,
            "effect": soundtrack_cfg.get("effect", "fadeInFadeOut")
        }

    template_context = {
        "background": template_cfg.get("background", "#000000"),
        "fonts": font_sources,
        "caption": caption_context,
        "logo": logo_context,
        "overlays": overlays_context,
        "title": {
            "font_family": title_cfg.get("font_family") or title_cfg.get("family") or "Roboto Condensed",
            "font_size": title_cfg.get("font_size") or 64,
            "color": title_cfg.get("color") or "#ffffff",
            "weight": title_cfg.get("weight") or 700,
            "width": title_cfg.get("width") or 960,
            "height": title_cfg.get("height") or 300,
            "offset": title_cfg.get("offset") or {"x": 0, "y": -0.343},
            "position": title_cfg.get("position") or "center",
            "alignment": title_cfg.get("alignment") or {"horizontal": "left", "vertical": "bottom"}
        },
        "subtitle": {
            "font_family": subtitle_cfg.get("font_family") or subtitle_cfg.get("family") or title_cfg.get("font_family") or "Roboto Condensed",
            "font_size": subtitle_cfg.get("font_size") or 42,
            "color": subtitle_cfg.get("color") or "#ffffff",
            "width": subtitle_cfg.get("width") or 960,
            "height": subtitle_cfg.get("height") or 100,
            "offset": subtitle_cfg.get("offset") or {"x": 0, "y": -0.447},
            "position": subtitle_cfg.get("position") or "center",
            "alignment": subtitle_cfg.get("alignment") or {"horizontal": "left", "vertical": "center"},
            "text": subtitle_text
        },
        "gallery": gallery_sources[:3] if gallery_sources else [],
        "slide": {
            "length": slide_cfg.get("length"),
            "overlap": slide_cfg.get("overlap"),
            "offset_from": slide_cfg.get("offset_from"),
            "offset_to": slide_cfg.get("offset_to")
        },
        "luma": {"src": luma_src, "length": luma_length},
        "soundtrack": soundtrack_context,
        "placeholders": placeholders
    }

    base_output_cfg = dict(renderer_cfg.get("output") or {})
    desired_fps = int(base_output_cfg.get("fps") or profile.fps or 25)
    base_output_cfg.setdefault("fps", desired_fps)
    base_output_cfg.setdefault("format", base_output_cfg.get("format", "mp4"))

    outputs_map: Dict[str, str] = {}
    job_records = []
    aspect_map = {"16x9": "16:9", "1x1": "1:1", "9x16": "9:16"}

    render_url = f"{base_api}/render"

    def _validate_timeline_strict(tl: dict) -> None:
        """Minimalna walidacja przed POST do Shotstack."""
        if not isinstance(tl, dict):
            raise RuntimeError("timeline must be a dict")
        tracks = tl.get("tracks") or []
        if not tracks:
            raise RuntimeError("timeline.tracks is empty")
        for tr in tracks:
            for clip in (tr.get("clips") or []):
                asset = clip.get("asset") or {}
                if not isinstance(asset, dict):
                    raise RuntimeError("clip.asset invalid")
                a_type = asset.get("type")
                if not a_type:
                    raise RuntimeError("clip.asset.type missing")
                if a_type in ("image", "video"):
                    src = asset.get("src")
                    if not (isinstance(src, str) and src.startswith("http")):
                        raise RuntimeError(f"asset.src must be http(s) for {a_type}")
                if "start" in clip and (not isinstance(clip["start"], (int, float))):
                    raise RuntimeError("clip.start must be number")
                if "length" in clip:
                    length_val = clip["length"]
                    if isinstance(length_val, str):
                        if length_val != "end":
                            raise RuntimeError("clip.length must be number or 'end'")
                    elif not isinstance(length_val, (int, float)):
                        raise RuntimeError("clip.length must be number")

    for fmt in formats:
        fmt_key = fmt.replace(":", "x").replace("/", "x")
        aspect_ratio = aspect_map.get(fmt, "16:9")
        format_output_cfg = dict(base_output_cfg)
        if "size" not in format_output_cfg:
            if fmt == "9x16":
                format_output_cfg["size"] = {"width": 1080, "height": 1920}
            elif fmt == "1x1":
                format_output_cfg["size"] = {"width": 1080, "height": 1080}
            else:
                format_output_cfg["size"] = {"width": 1920, "height": 1080}

        timeline, output, merge = build_shotstack_timeline(
            template_context,
            output_cfg=format_output_cfg,
            aspect_ratio=aspect_ratio,
            audio_duration=audio_duration
        )

        _validate_timeline_strict(timeline)

        payload_json: Dict[str, Any] = {"timeline": timeline, "output": output}
        if merge:
            payload_json["merge"] = merge

        cb = renderer_cfg.get("callback") or renderer_cfg.get("webhook") or renderer_cfg.get("callback_url")
        if cb:
            payload_json["callback"] = cb

        # persist exact payload sent to Shotstack for easier debugging
        try:
            timeline_path = os.path.join(project_dir, f"timeline_{fmt_key}.json")
            save_json(timeline_path, payload_json)
            save_json(os.path.join(project_dir, "timeline.json"), payload_json)
        except Exception as exc:
            news_to_video_logger.warning("[shotstack] failed to save timeline %s: %s", fmt_key, exc)
#         cleaned_payload_json = {
#   "timeline": {
#     "background": "#FFFFFF",
#     "tracks": [
#       {
#         "clips": [
#           {
#             "asset": {
#               "type": "text",
#               "text": "{{SUBHEADING}}",
#               "alignment": {
#                 "horizontal": "left",
#                 "vertical": "center"
#               },
#               "font": {
#                 "color": "{{ FONT_COLOR_2 }}",
#                 "family": "Montserrat SemiBold",
#                 "size": "30",
#                 "lineHeight": 1
#               },
#               "width": 1300,
#               "height": 110
#             },
#             "start": 0.6,
#             "length": "end",
#             "offset": {
#               "x": 0.087,
#               "y": -0.404
#             },
#             "position": "center",
#             "transition": {
#               "in": "slideRight"
#             }
#           }
#         ]
#       },
#       {
#         "clips": [
#           {
#             "asset": {
#               "type": "text",
#               "text": "{{HEADING}}",
#               "alignment": {
#                 "horizontal": "left",
#                 "vertical": "center"
#               },
#               "font": {
#                 "color": "{{ FONT_COLOR_1 }}",
#                 "family": "Montserrat ExtraBold",
#                 "size": "45",
#                 "lineHeight": 1
#               },
#               "width": 1300,
#               "height": 110
#             },
#             "start": 0.4,
#             "length": "end",
#             "offset": {
#               "x": 0.087,
#               "y": -0.321
#             },
#             "position": "center",
#             "transition": {
#               "in": "slideRight"
#             }
#           }
#         ]
#       },
#       {
#         "clips": [
#           {
#             "asset": {
#               "type": "text",
#               "text": "",
#               "alignment": {
#                 "horizontal": "center",
#                 "vertical": "center"
#               },
#               "font": {
#                 "color": "#000000",
#                 "family": "Montserrat SemiBold",
#                 "size": 24,
#                 "lineHeight": 1
#               },
#               "width": 100,
#               "height": 12,
#               "background": {
#                 "color": "#e1dbd7"
#               }
#             },
#             "start": 0.6,
#             "length": "end",
#             "offset": {
#               "x": -0.301,
#               "y": -0.315
#             },
#             "position": "center"
#           }
#         ]
#       },
#       {
#         "clips": [
#           {
#             "asset": {
#               "type": "text",
#               "text": "{{BULLETIN}}",
#               "alignment": {
#                 "horizontal": "left",
#                 "vertical": "center"
#               },
#               "font": {
#                 "color": "{{ FONT_COLOR_1 }}",
#                 "family": "Montserrat ExtraBold",
#                 "size": 72,
#                 "lineHeight": 1
#               },
#               "width": 400,
#               "height": 200
#             },
#             "start": 0.4,
#             "length": "end",
#             "offset": {
#               "x": -0.356,
#               "y": -0.353
#             },
#             "position": "center",
#             "transition": {
#               "in": "slideRight"
#             }
#           }
#         ]
#       },
#       {
#         "clips": [
#           {
#             "asset": {
#               "type": "text",
#               "text": "",
#               "alignment": {
#                 "horizontal": "center",
#                 "vertical": "center"
#               },
#               "font": {
#                 "color": "#000000",
#                 "family": "Montserrat SemiBold",
#                 "size": 24,
#                 "lineHeight": 1
#               },
#               "width": 1370,
#               "height": 110,
#               "stroke": {
#                 "color": "#000000"
#               },
#               "background": {
#                 "color": "{{ FONT_COLOR_2 }}"
#               }
#             },
#             "start": 0.2,
#             "length": "end",
#             "offset": {
#               "x": 0.096,
#               "y": -0.321
#             },
#             "position": "center",
#             "transition": {
#               "in": "slideRight"
#             }
#           }
#         ]
#       },
#       {
#         "clips": [
#           {
#             "asset": {
#               "type": "text",
#               "text": "",
#               "alignment": {
#                 "horizontal": "center",
#                 "vertical": "center"
#               },
#               "font": {
#                 "color": "#000000",
#                 "family": "Montserrat SemiBold",
#                 "size": 24,
#                 "lineHeight": 1
#               },
#               "width": 1300,
#               "height": 110,
#               "stroke": {
#                 "color": "#000000"
#               },
#               "background": {
#                 "color": "{{ FONT_COLOR_1 }}"
#               }
#             },
#             "start": 0.4,
#             "length": "end",
#             "offset": {
#               "x": 0.078,
#               "y": -0.387
#             },
#             "position": "center",
#             "transition": {
#               "in": "slideRight"
#             }
#           }
#         ]
#       },
#       {
#         "clips": [
#           {
#             "length": 15,
#             "asset": {
#               "type": "video",
#               "src": "{{ VIDEO }}",
#               "volume": 1
#             },
#             "start": 0,
#             "scale": 1
#           }
#         ]
#       }
#     ]
#   },
#   "output": {
#     "format": "mp4",
#     "fps": 25,
#     "size": {
#       "width": 1920,
#       "height": 1080
#     }
#   },
#   "merge": [
#     {
#       "find": "HEADING",
#       "replace": "TEST HEADING TEST"
#     },
#     {
#       "find": "SUBHEADING",
#       "replace": manifest.title
#     },
#     {
#       "find": "BULLETIN",
#       "replace": "NEWS UPDATE"
#     },
#     {
#       "find": "VIDEO",
#       "replace": "https://templates.shotstack.io/news-update-template-broadcast-breaking-news-live/9d899be9-185f-4ead-afce-0cfc1c946eea/source.mp4"
#     },
#     {
#       "find": "FONT_COLOR_1",
#       "replace": "#e1dbd7"
#     },
#     {
#       "find": "FONT_COLOR_2",
#       "replace": "#008000"
#     }
#   ]
# }

        # --- SEND ---
        # news_to_video_logger.info(
        #     "\n\n\n\t\t 🚜👷🚧🏗️ _http_post_json(render_url=%s \n\n\t\tpayload_json=\n%s \n\n\t\theaders=\n%s \n\ntimeout=45\n\n\n",
        #     render_url,
        #     json.dumps(payload_json, ensure_ascii=False, indent=2),
        #     headers,
        # )
        news_to_video_logger.info(
            "\n\n\n\t\t 🚜👷🚧🏗️ _http_post_json(render_url=%s \n\n\t\tpayload_json=\n%s \n\n\t\theaders=\n%s \n\ntimeout=45\n\n\n",
            render_url,
            payload_json,
            headers,
        )

        job = _http_post_json(render_url, payload_json, headers, timeout=45)
        job_id = (
            (job.get("response") or {}).get("id")
            or job.get("id")
            or (job.get("data") or {}).get("id")
        )
        if not job_id:
            raise RuntimeError(f"Shotstack: brak ID joba w odpowiedzi: {job}")

        job_records.append({"fmt": fmt_key, "id": job_id})

    # --- 6) Polling: wszystkie joby do skutku ---
    def _poll_and_fetch(job_id: str, fmt_key: str) -> str:
        status_url = f"{base_api}/render/{job_id}"
        max_wait_s = int(os.getenv("SHOTSTACK_POLL_MAX_SEC", "600"))
        interval = 2.5
        waited = 0.0
        last_status = "queued"

        while waited < max_wait_s:
            info = _http_get_json(status_url, headers, timeout=15)
            resp = info.get("response") or info.get("data") or info
            status = (resp.get("status") or info.get("status") or "").lower()
            if status and status != last_status:
                print(f"[shotstack] {job_id} -> {status}")
                last_status = status

            if status == "done":
                url = (
                    (resp.get("output", {}) or {}).get("url")
                    or (resp.get("url"))
                    or (resp.get("assets") or [{}])[0].get("url")
                )
                if not url:
                    raise RuntimeError(f"Shotstack: status done, ale brak URL w odpowiedzi: {info}")

                local = os.path.join(out_dir, f"output_{fmt_key}.mp4")
                _download_file(url, local, timeout=300)
                return local

            if status in ("failed", "error"):
                msg = resp.get("message") or info.get("message") or "Shotstack job failed"
                errors = resp.get("errors") or info.get("errors")
                if errors:
                    news_to_video_logger.error(
                        "[shotstack] job %s errors: %s", job_id, errors
                    )
                else:
                    news_to_video_logger.error(
                        "[shotstack] job %s failed response: %s", job_id, resp
                    )
                raise RuntimeError(f"Shotstack job {job_id} failed: {msg}")

            time.sleep(interval)
            waited += interval
            if interval < 6.0:
                interval = min(6.0, interval + 0.5)

        raise TimeoutError(f"Shotstack job {job_id} timeout after {max_wait_s}s")

    for rec in job_records:
        local_mp4 = _poll_and_fetch(rec["id"], rec["fmt"])
        if rec["fmt"] == "16x9":
            outputs_map["mp4_16x9"] = local_mp4
        elif rec["fmt"] == "1x1":
            outputs_map["mp4_1x1"] = local_mp4
        elif rec["fmt"] == "9x16":
            outputs_map["mp4_9x16"] = local_mp4
        else:
            outputs_map[f"mp4_{rec['fmt']}"] = local_mp4

    # --- 7) Uzupełnij outputs + manifest ---
    durations = [audio_duration]
    for k, v in outputs_map.items():
        if k.startswith("mp4_") and os.path.isfile(v):
            d = _ffprobe_duration(v) or 0.0
            if d > 0:
                durations.append(d)

    manifest.setdefault("outputs", {})
    manifest["outputs"].update(outputs_map)
    manifest["outputs"]["srt"] = srt_path
    manifest["outputs"]["ass"] = ass_path
    manifest["outputs"]["audio"] = audio_path
    manifest["outputs"]["duration_sec"] = round(min(durations), 2)
    manifest["outputs"]["shotstack_jobs"] = job_records

    _save_manifest(project_dir, manifest)
    print(f"[render_via_shotstack] DONE => outputs: {list(outputs_map.keys())}")

    return manifest["outputs"]





def map_shotstack_transition(name: str) -> str:
    n = (name or "").strip().replace("-", "").replace("_", "").lower()
    mapped = SHOTSTACK_TRANSITION_ALIAS.get(n, "fade")
    return mapped if mapped in SHOTSTACK_ALLOWED_TRANSITIONS else "fade"

# --- TIMELINE BUILDER ---
def _parse_media_urls(form):
    raw = (form.get('media_urls') or '').strip()
    if not raw:
        return []
    urls = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # tylko absolutne URL-e (Shotstack musi je pobrać)
    return [u for u in urls if re.match(r'^https?://', u)]

def _is_video(url: str):
    return bool(re.search(r'\.(mp4|mov|mpe?g|webm|mkv)(\?.*)?$', url, re.I))

def _map_logo_position(pos: str) -> str:
    mapping = {
        'top-left':'topLeft', 'top-right':'topRight',
        'bottom-left':'bottomLeft', 'bottom-right':'bottomRight'
    }
    return mapping.get((pos or 'top-right').lower(), 'topRight')

def _normalize_transition(name: str) -> str:
    n = (name or 'fade').strip()
    return n if n in SHOTSTACK_ALLOWED_TRANSITIONS else 'fade'

def _overlap_seconds_for_transition(name: str) -> float:
    """Szacowany overlap pod crossfade (zgodny z *Slow/*Fast heurystyką)."""
    n = _normalize_transition(name)
    if n.endswith('Slow'):
        return 2.0
    if n.endswith('Fast'):
        return 0.5
    if n == 'none':
        return 0.0
    return 1.0

def _split_text_to_subs(text: str, wpm: int = 170, speed: float = 1.0):
    """
    Dzieli tekst na krótkie frazy i wylicza przybliżony czas (sekundy)
    przy założeniu ~wpm i mnożnika prędkości lektora.
    """
    if not text:
        return []
    # prosty podział: zdania/kropki/nowe linie
    parts = re.split(r'(?<=[\.\!\?])\s+|\n+', text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    wps = (wpm / 60.0) * float(speed)  # słowa na sekundę (przyspiesza gdy speed>1)
    seq = []
    for p in parts:
        words = max(1, len(p.split()))
        length = max(1.5, round(words / wps, 2))  # nie krócej niż 1.5s
        seq.append({"text": p, "length": length})
    return seq

def _is_public_http(url: str) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))

def _validate_timeline(tl: dict):
    # soundtrack
    st = tl.get("soundtrack")
    if st and not _is_public_http(st.get("src", "")):
        raise ValueError("timeline.soundtrack.src must be public http(s) URL")

    # asset.src w każdym klipie
    for ti, tr in enumerate(tl.get("tracks", [])):
        for ci, clip in enumerate(tr.get("clips", [])):
            asset = clip.get("asset", {})
            if "src" in asset and not _is_public_http(asset["src"]):
                raise ValueError(f"tracks[{ti}].clips[{ci}].asset.src must be http(s)")
            # długości i starty muszą być liczbami
            for k in ("start", "length"):
                if k in clip and not isinstance(clip[k], (int, float)):
                    raise ValueError(f"tracks[{ti}].clips[{ci}].{k} must be a number")

def build_shotstack_timeline(
    template_ctx: Dict[str, Any],
    output_cfg: Dict[str, Any],
    aspect_ratio: str = "16:9",
    audio_duration: float = 0.0,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """
    Buduje timeline/output/merge na bazie przygotowanego kontekstu szablonu.
    """
    tracks: List[Dict[str, Any]] = []
    timeline: Dict[str, Any] = {
        "background": template_ctx.get("background", "#000000"),
        "tracks": tracks,
    }

    caption_ctx = template_ctx.get("caption") or {}
    fonts = []
    for src in template_ctx.get("fonts") or []:
        if src:
            fonts.append({
                "src": src
            })
    if not fonts and caption_ctx.get("src"):
        fonts.append({
            "src": SHOTSTACK_DEFAULT_FONT_SRC
        })
    if fonts:
        timeline["fonts"] = fonts

    # Caption track
    caption_src = caption_ctx.get("src")
    if caption_src:
        asset = {"type": "caption", "src": caption_src}
        font_block = {}
        if caption_ctx.get("font_family"):
            font_block["family"] = caption_ctx["font_family"]
        if caption_ctx.get("font_size"):
            font_block["size"] = str(caption_ctx["font_size"])
        if caption_ctx.get("line_height"):
            font_block["lineHeight"] = caption_ctx["line_height"]
        if caption_ctx.get("color"):
            font_block["color"] = caption_ctx["color"]
        if font_block:
            asset["font"] = font_block
        background_cfg = caption_ctx.get("background") or {}
        bg_block = {}
        if background_cfg.get("color"):
            bg_block["color"] = background_cfg["color"]
        if background_cfg.get("opacity") is not None:
            bg_block["opacity"] = background_cfg["opacity"]
        if background_cfg.get("borderRadius") is not None:
            bg_block["borderRadius"] = background_cfg["borderRadius"]
        if background_cfg.get("padding") is not None:
            bg_block["padding"] = background_cfg["padding"]
        if bg_block:
            asset["background"] = bg_block
        stroke_cfg = caption_ctx.get("stroke") or {}
        stroke_block = {}
        if stroke_cfg.get("color"):
            stroke_block["color"] = stroke_cfg["color"]
        if stroke_cfg.get("width") is not None:
            stroke_block["width"] = stroke_cfg["width"]
        if stroke_block:
            asset["stroke"] = stroke_block
        clip = {
            "asset": asset,
            "start": 0,
            "length": round(audio_duration, 2) if audio_duration else "end",
            "position": caption_ctx.get("position", "center"),
        }
        tracks.append({"clips": [clip]})

    # Logo overlay
    logo_ctx = template_ctx.get("logo")
    if isinstance(logo_ctx, dict) and logo_ctx.get("src"):
        clip = {
            "asset": {"type": "image", "src": logo_ctx["src"]},
            "start": 0,
            "length": "end",
        }
        if logo_ctx.get("scale") is not None:
            clip["scale"] = logo_ctx["scale"]
        offset_block = {k: v for k, v in (logo_ctx.get("offset") or {}).items() if v is not None}
        if offset_block:
            clip["offset"] = offset_block
        if logo_ctx.get("position"):
            clip["position"] = logo_ctx["position"]
        tracks.append({"clips": [clip]})

    # Additional overlays
    for overlay_ctx in template_ctx.get("overlays") or []:
        if not isinstance(overlay_ctx, dict) or not overlay_ctx.get("src"):
            continue
        clip = {
            "asset": {"type": "image", "src": overlay_ctx["src"]},
            "start": 0,
            "length": "end",
        }
        if overlay_ctx.get("opacity") is not None:
            clip["opacity"] = overlay_ctx["opacity"]
        if overlay_ctx.get("scale") is not None:
            clip["scale"] = overlay_ctx["scale"]
        offset_block = {k: v for k, v in (overlay_ctx.get("offset") or {}).items() if v is not None}
        if offset_block:
            clip["offset"] = offset_block
        if overlay_ctx.get("position"):
            clip["position"] = overlay_ctx["position"]
        tracks.append({"clips": [clip]})

    def _text_clip(ctx: Dict[str, Any], placeholder: str) -> Optional[Dict[str, Any]]:
        if not isinstance(ctx, dict):
            return None
        asset = {"type": "text", "text": placeholder}
        alignment = ctx.get("alignment")
        if alignment:
            asset["alignment"] = alignment
        font_block = {}
        if ctx.get("font_family"):
            font_block["family"] = ctx["font_family"]
        if ctx.get("font_size"):
            font_block["size"] = str(ctx["font_size"])
        if ctx.get("weight"):
            font_block["weight"] = ctx["weight"]
        if ctx.get("color"):
            font_block["color"] = ctx["color"]
        if font_block:
            asset["font"] = font_block
        if ctx.get("width"):
            asset["width"] = ctx["width"]
        if ctx.get("height"):
            asset["height"] = ctx["height"]
        clip = {
            "asset": asset,
            "start": 0,
            "length": "end",
        }
        offset_block = {k: v for k, v in (ctx.get("offset") or {}).items() if v is not None}
        if offset_block:
            clip["offset"] = offset_block
        if ctx.get("position"):
            clip["position"] = ctx["position"]
        return clip

    title_clip = _text_clip(template_ctx.get("title") or {}, "{{TITLE}}")
    if title_clip:
        tracks.append({"clips": [title_clip]})

    subtitle_clip = _text_clip(template_ctx.get("subtitle") or {}, "{{SUBTITLE}}")
    if subtitle_clip:
        tracks.append({"clips": [subtitle_clip]})

    # Gallery slides
    gallery = template_ctx.get("gallery") or []
    slide_cfg = template_ctx.get("slide") or {}
    gallery_length = len(gallery)
    if gallery_length:
        slide_length = slide_cfg.get("length")
        if not slide_length or slide_length <= 0:
            slide_length = max(audio_duration / gallery_length if audio_duration else 6.0, 4.0)
        slide_overlap = slide_cfg.get("overlap")
        if slide_overlap is None or slide_overlap < 0:
            slide_overlap = min(2.0, slide_length / 3)
        offset_from = slide_cfg.get("offset_from")
        if offset_from is None:
            offset_from = 0.75
        offset_to = slide_cfg.get("offset_to")
        if offset_to is None:
            offset_to = -0.75

        luma_ctx = template_ctx.get("luma") or {}
        luma_src = luma_ctx.get("src")
        luma_length = luma_ctx.get("length") or 2.0

        start_time = 0.0
        for idx, src in enumerate(gallery):
            if not src:
                continue
            clip_length = float(slide_length)
            last_clip = idx == gallery_length - 1
            if audio_duration and last_clip:
                remaining = max(audio_duration - start_time, 0.5)
                clip_length = max(remaining, clip_length)

            clip = {
                "asset": {"type": "image", "src": src},
                "start": round(start_time, 3),
                "length": round(clip_length, 3),
                "position": "center",
                "effect": "slideLeft",
                "offset": {
                    "x": [{
                        "start": 0,
                        "length": round(clip_length, 3),
                        "from": offset_from,
                        "to": offset_to
                    }],
                    "y": 0
                }
            }
            transitions = {}
            if idx > 0:
                transitions["in"] = "fade"
            if not last_clip:
                transitions["out"] = "fade"
            if transitions:
                clip["transition"] = transitions

            track_clips = [clip]
            if luma_src and not last_clip:
                luma_start = max(start_time + clip_length - luma_length, start_time)
                track_clips.append({
                    "asset": {"type": "luma", "src": luma_src},
                    "start": round(luma_start, 3),
                    "length": round(luma_length, 3)
                })

            tracks.append({"clips": track_clips})
            if clip_length > slide_overlap:
                start_time += clip_length - slide_overlap
            else:
                start_time += clip_length

    # Soundtrack
    soundtrack_ctx = template_ctx.get("soundtrack")
    if soundtrack_ctx and soundtrack_ctx.get("src"):
        timeline["soundtrack"] = soundtrack_ctx

    # Output section
    output: Dict[str, Any] = {}
    output["format"] = output_cfg.get("format", "mp4")
    if output_cfg.get("fps"):
        output["fps"] = int(output_cfg["fps"])
    if output_cfg.get("size"):
        output["size"] = {
            "width": int(output_cfg["size"]["width"]),
            "height": int(output_cfg["size"]["height"])
        }
    else:
        if output_cfg.get("resolution"):
            output["resolution"] = output_cfg["resolution"]
        output["aspectRatio"] = aspect_ratio

    merge: List[Dict[str, Any]] = []
    for key, value in (template_ctx.get("placeholders") or {}).items():
        if value:
            merge.append({"find": key, "replace": value})

    return timeline, output, merge


def render_via_shotstack_depr(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict[str, Any]:
    """
    Render przez Shotstack:
     1) Czyta manifest i payload.
     2) Generuje TTS + SRT/ASS lokalnie (jak backend lokalny), żeby mieć spójny audio/captions.
     3) Dla każdego formatu buduje timeline/output (build_shotstack_timeline) i wysyła render.
     4) Polluje status jobów do zakończenia, pobiera MP4 do outputs/.
     5) Aktualizuje manifest i zwraca mapę outputs jak przy lokalnym renderze.

    Zależności (już obecne w module):
      - SHOTSTACK_API_KEY, SHOTSTACK_ENV, headers, fmt_map, build_shotstack_timeline, _split_text_to_subs, itp.
      - z news_to_video.main: load_json, save_json, segment_text, synthesize_tts, TTSSettings,
        generate_srt, generate_ass_from_timeline, profile_for, _ffprobe_duration, _ensure_remote_url
    """
    print(f'\n\t\tSTART ==> render_via_shotstack({project_dir})')
    profile = profile or RenderProfile()

    # --- Bezpieczne I/O + małe utilsy ---
    def _read_manifest(pdir: str) -> dict:
        mpath = os.path.join(pdir, "manifest.json")
        m = load_json(mpath) or {}
        return m

    def _save_manifest(pdir: str, m: dict) -> None:
        mpath = os.path.join(pdir, "manifest.json")
        save_json(mpath, m)

    def _http_post_json(url: str, js: dict, hdrs: dict, timeout: int = 30) -> dict:
        r = requests.post(url, json=js, headers=hdrs, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Shotstack POST {url} -> {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"Shotstack POST {url} -> invalid JSON")

    def _http_get_json(url: str, hdrs: dict, timeout: int = 20) -> dict:
        r = requests.get(url, headers=hdrs, timeout=timeout)
        if r.status_code >= 300:
            raise RuntimeError(f"Shotstack GET {url} -> {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"Shotstack GET {url} -> invalid JSON")

    def _download_file(url: str, dest_path: str, timeout: int = 120) -> None:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)

    # --- 1) Manifest/payload ---
    manifest = _read_manifest(project_dir)
    payload = manifest.get("payload", {}) or {}

    # Efekty/brand/tts tak jak w lokalnym rendererze
    tts_cfg = payload.get("tts", {}) or {}
    tts = TTSSettings(**tts_cfg) if isinstance(tts_cfg, dict) else TTSSettings()
    burn_subs = bool(payload.get("subtitles", {}).get("burn_in", True))
    formats = payload.get("formats") or ["16x9"]
    if isinstance(formats, str):
        formats = [formats]
    formats = [f for f in formats if f in fmt_map.keys()] or ["16x9"]

    # --- 2) TTS + SRT/ASS (lokalnie) ---
    # Zgodnie z lokalnym flow (segmentacja → synthesize_tts → generate_srt/ass) :contentReference[oaicite:4]{index=4}
    segments = segment_text(payload.get("text") or "")
    audio_dir = os.path.join(project_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    audio_path, tts_timeline = synthesize_tts(segments, tts, audio_dir)
    audio_duration = _ffprobe_duration(audio_path) or 0.0


    out_dir = os.path.join(project_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    srt_path = os.path.join(out_dir, "captions.srt")
    ass_path = os.path.join(out_dir, "captions.ass")
    # Te same generatory co lokalnie (ASS używamy do burn-in, SRT — dla archiwizacji) :contentReference[oaicite:5]{index=5}
    generate_srt(tts_timeline, srt_path)
    generate_ass_from_timeline(tts_timeline, profile, ass_path, max_words=5, min_chunk_dur=0.7)

    # Zapewnij publiczny URL do audio dla Shotstack (soundtrack.src)
    # _ensure_remote_url: potrafi wrzucić lokalny plik na S3 i zwrócić URL, gdy potrzeba. :contentReference[oaicite:6]{index=6}
    tts_url = _ensure_remote_url(audio_path)

    # --- 3) Zbuduj "form" pod build_shotstack_timeline na bazie manifestu ---
    # build_shotstack_timeline oczekuje pól jak w formularzu; mapujemy z manifestu. :contentReference[oaicite:7]{index=7} :contentReference[oaicite:8]{index=8}
    def _manifest_to_form(p: dict) -> dict:
        media_urls = []
        for m in (p.get("media") or []):
            src = m.get("src")
            if not src:
                continue
            # Zapewnij, że będzie HTTP(S) dla Shotstack (jeśli local path → podnieś na S3) :contentReference[oaicite:9]{index=9}
            if not re.match(r"^https?://", src or "", re.I):
                src = _ensure_remote_url(src)
            if src:
                media_urls.append(src)

        brand = p.get("brand", {}) or {}
        transitions = p.get("transitions", {}) or {}
        # Minimalny zestaw pól rozumianych przez builder
        return {
            "text": p.get("text") or "",
            "burn_subtitles": burn_subs,
            "use_xfade": bool(transitions.get("use_xfade")),
            "xfade_transition": transitions.get("transition") or "fade",
            "image_length": 4,
            "video_length": 6,
            "logo_url": brand.get("logo_path") or "",
            "logo_position": brand.get("position") or "top-right",
            "logo_opacity": brand.get("opacity") or 0.85,
            "logo_scale": brand.get("scale") or 0.15,
            "media_urls": "\n".join(media_urls),
            # Dodatkowe stylistyczne klucze (opcjonalne pola UI) pomijamy — builder i tak daje czytelny default.
        }

    form_like = _manifest_to_form(payload)

    # --- 4) Ustal output parametry (fps/size) z payload.renderer.config (UI → manifest) ---
    r_cfg = (payload.get("renderer") or {}).get("config") or {}
    out_cfg = (r_cfg.get("output") or {})
    desired_fps = int(out_cfg.get("fps") or payload.get("fps") or 25)
    # Shotstack może dostać resolution/aspectRatio albo size width/height;
    # Bierzemy width/height z UI, jeśli podano — inaczej użyjemy predef. rozdzielczości z aspektu. :contentReference[oaicite:10]{index=10}
    user_size = {
        "width": int(out_cfg.get("size", {}).get("width") or out_cfg.get("width") or 0),
        "height": int(out_cfg.get("size", {}).get("height") or out_cfg.get("height") or 0),
    }

    # --- 5) Pętla po formatach: render → poll → download ---
    outputs_map: Dict[str, str] = {}
    job_records = []
    aspect_map = {"16x9": "16:9", "1x1": "1:1", "9x16": "9:16"}

    base_api = BASE_EDIT_URL.rstrip("/")  # np. https://api.shotstack.io/edit/v1
    render_url = f"{base_api}/render"

    for fmt in formats:
        fmt_key = fmt.replace(":", "x").replace("/", "x")  # 16x9 | 1x1 | 9x16
        # fps/size na poziomie tego aspektu
        video_params = {"fps": desired_fps}
        # jeśli użytkownik podał rozmiar ręcznie – użyj go zamiast "resolution"
        if user_size["width"] > 0 and user_size["height"] > 0:
            # builder ustawia "resolution", ale my podmienimy w output na size width/height
            use_custom_size = True
        else:
            use_custom_size = False
            # resolution: dopasuj (hd/fhd) z prostą heurystyką
            video_params["resolution"] = "fhd"

        aspect_ratio = aspect_map.get(fmt, "16:9")
        # zbuduj timeline/output przez naszego buildera (zapewnia poprawny porządek tracków i tytułowe napisy)
        timeline, output = build_shotstack_timeline(
            form=form_like,
            video_params=video_params,
            aspect_ratio=aspect_ratio,
            tts_url=tts_url
        )
        # podmień fps i ewent. rozmiar
        if desired_fps:
            output["fps"] = desired_fps
        if use_custom_size:
            output.pop("resolution", None)
            output.pop("aspectRatio", None)
            output["size"] = {"width": user_size["width"], "height": user_size["height"]}
        else:
            # zostaw resolution + aspectRatio z buildera
            pass

        # sanity validation — upewnij się, że asset.src to HTTP(S) i start/length to liczby
        _validate_timeline(timeline)

        payload_json = {"timeline": timeline, "output": output}
        # webhook z UI (opcjonalny)
        cb = r_cfg.get("callback") or r_cfg.get("webhook") or r_cfg.get("callback_url")
        if cb:
            payload_json["callback"] = cb

        # --- SEND ---

        # print(f'_http_post_json(render_url={render_url} \n\n\t\tpayload_json=\n{payload_json} \n\n\t\theaders=\n{headers} \n\ntimeout=45')
        # print('\n\n\n\t\t\t STOP / PAUZA \n\n\n')
        # exit()

        job = _http_post_json(render_url, payload_json, headers, timeout=45)
        # struktura Shotstack standardowo ma klucze 'response'/'success' – bądźmy odporni
        job_id = (
            (job.get("response") or {}).get("id")
            or job.get("id")
            or (job.get("data") or {}).get("id")
        )
        if not job_id:
            raise RuntimeError(f"Shotstack: brak ID joba w odpowiedzi: {job}")

        job_records.append({"fmt": fmt_key, "id": job_id})

    # --- 6) Polling: wszystkie joby do skutku ---
    # Poll sekwencyjnie (prosto i stabilnie) – jeśli chcesz, można zrównoleglić.
    def _poll_and_fetch(job_id: str, fmt_key: str) -> str:
        """
        Zwraca lokalną ścieżkę do pobranego MP4 (outputs/output_<fmt_key>.mp4).
        """
        status_url = f"{base_api}/render/{job_id}"
        max_wait_s = int(os.getenv("SHOTSTACK_POLL_MAX_SEC", "600"))
        interval = 2.5
        waited = 0.0
        last_status = "queued"

        while waited < max_wait_s:
            info = _http_get_json(status_url, headers, timeout=15)
            # status: queued | processing | done | failed
            resp = info.get("response") or info.get("data") or info
            status = (resp.get("status") or info.get("status") or "").lower()
            if status and status != last_status:
                print(f"[shotstack] {job_id} -> {status}")
                last_status = status

            if status == "done":
                # Lokalizacja assetu zależnie od planu – spróbujmy kilku pól
                url = (
                    (resp.get("output", {}) or {}).get("url")
                    or (resp.get("url"))
                    or (resp.get("assets") or [{}])[0].get("url")
                )
                if not url:
                    raise RuntimeError(f"Shotstack: status done, ale brak URL w odpowiedzi: {info}")

                # Pobierz do outputs/
                local = os.path.join(out_dir, f"output_{fmt_key}.mp4")
                _download_file(url, local, timeout=300)
                return local

            if status == "failed" or status == "error":
                msg = resp.get("message") or info.get("message") or "Shotstack job failed"
                raise RuntimeError(f"Shotstack job {job_id} failed: {msg}")

            time.sleep(interval)
            waited += interval
            # delikatny backoff (bez przesady, żeby UI szybko widział progres)
            if interval < 6.0:
                interval = min(6.0, interval + 0.5)

        raise TimeoutError(f"Shotstack job {job_id} timeout after {max_wait_s}s")

    for rec in job_records:
        local_mp4 = _poll_and_fetch(rec["id"], rec["fmt"])
        # mapuj tak jak w lokalnym rendererze (mp4_16x9/mp4_1x1/mp4_9x16) :contentReference[oaicite:11]{index=11}
        if rec["fmt"] == "16x9":
            outputs_map["mp4_16x9"] = local_mp4
        elif rec["fmt"] == "1x1":
            outputs_map["mp4_1x1"] = local_mp4
        elif rec["fmt"] == "9x16":
            outputs_map["mp4_9x16"] = local_mp4
        else:
            outputs_map[f"mp4_{rec['fmt']}"] = local_mp4

    # --- 7) Uzupełnij outputs (audio/srt/ass + duration) i manifest (status zostanie ustawiony przez _run_render_job) ---
    # Ustal minimalne duration tak jak lokalnie (min z audio oraz realnych plików wideo) :contentReference[oaicite:12]{index=12}
    # Na shotstack nie dziala duration
    # durations = [audio_duration]
    # for k, v in outputs_map.items():
    #     if k.startswith("mp4_") and os.path.isfile(v):
    #         d = _ffprobe_duration(v) or 0.0
    #         if d > 0:
    #             durations.append(d)

    manifest.setdefault("outputs", {})
    manifest["outputs"].update(outputs_map)
    manifest["outputs"]["srt"] = srt_path
    manifest["outputs"]["ass"] = ass_path
    manifest["outputs"]["audio"] = audio_path
    manifest["outputs"]["duration_sec"] = round(min(durations), 2)

    # Dla diagnostyki zostaw joby:
    manifest["outputs"]["shotstack_jobs"] = job_records

    _save_manifest(project_dir, manifest)
    print(f"[render_via_shotstack] DONE => outputs: {list(outputs_map.keys())}")

    return manifest["outputs"]
