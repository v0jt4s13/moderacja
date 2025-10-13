# news_to_video/render_video.py
import os
import re
from pydub import AudioSegment
from typing import Any, List, Dict, Optional, Tuple
import time
import requests
from urllib.parse import urlparse
from news_to_video.main import (
    load_json,
    segment_text,
    synthesize_tts,
    profile_for,
    prepare_media_segments,
    generate_srt, 
    generate_ass_from_timeline,
    update_manifest,
    detect_media_type,
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

    if preset not in ('720p_30', '1080p_30', '1080p_60', '4k_30'):
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

# [ADD] — główna funkcja renderowania przez Shotstack
def render_via_shotstack(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict[str, Any]:
    print(f'\n\t\tSTART ==> render_via_shotstack({project_dir}, {profile})')

    input("1. Press the button to continue ...\n")

    """
    Render przez Shotstack Edit API:
    - generuje TTS (MP3) i SRT,
    - buduje timeline z poprawnymi start/length (bez 'auto'),
    - używa timeline.soundtrack dla audio (bez osobnego audio-tracka),
    - weryfikuje URL-e i timeline przed POST,
    - kolejkuje render dla wybranych formatów i polluje status.
    """
    if not SHOTSTACK_API_KEY:
        news_to_video_logger.error("SHOTSTACK_API_KEY not configured")
        raise RuntimeError("SHOTSTACK_API_KEY not configured")
    profile = profile or RenderProfile()
    manifest_path = os.path.join(project_dir, "manifest.json")
    manifest = load_json(manifest_path)
    
    payload = manifest.get("payload", {})

    print('\n\t\t ===> payload data <===\n')
    for k,v in payload.items():

        print(f'{k}=>{v}')
    
    text = payload.get("text", "")
    tts_cfg = payload.get("tts", {}) or {}
    tts = TTSSettings(**tts_cfg) if isinstance(tts_cfg, dict) else TTSSettings()
    # Efekty
    trans_cfg = payload.get("transitions") or {}
    transitions = TransitionsConfig(**trans_cfg) if isinstance(trans_cfg, dict) else TransitionsConfig()
    xfade_transition = (trans_cfg.get("transition") or "fade").strip()

    # --- Transitions (mapowanie do dozwolonych nazw) ---
    ss_transition = map_shotstack_transition(xfade_transition) if transitions.use_xfade else None

    input(f"2. Press the button to continue ...\n{ss_transition}\n")


    brand_cfg = payload.get("brand") or {}
    branding = BrandConfig(**brand_cfg) if isinstance(brand_cfg, dict) else BrandConfig()

    # --- 5) Logo (overlay) ---
    # branding.opacity}, position:{branding.position}, scale:{branding.scale}, 
    # url:{branding.logo_path
    logo_clip = None
    logo_path = branding.logo_path
    if logo_path:
        logo_url = _ensure_remote_url(logo_path)
        logo_url = _encode_asset_url(logo_url)
        if logo_url and _is_public_http(logo_url):
            _head_logo = requests.head(logo_url, allow_redirects=True, timeout=6)
            if _head_logo.status_code >= 400:
                raise RuntimeError(f"Logo URL not reachable (status {_head_logo.status_code}): {logo_url}")

            pos_map = {
                "top-right": "topRight", "top-left": "topLeft", "bottom-right": "bottomRight",
                "bottom-left": "bottomLeft", "top": "top", "bottom": "bottom",
                "left": "left", "right": "right", "center": "center"
            }
            position = pos_map.get((branding.position or "top-right").strip().lower(), "topRight")
            scale = float(branding.scale or 0.15)
            opacity = float(branding.opacity or 0.85)
            logo_clip = {
                "asset": {"type": "image", "src": logo_url},
                "start": 0,
                "length": round(max(start_time, audio_duration, 1.0), 3),
                "scale": max(0.01, min(1.0, scale)),
                "position": position,
                "opacity": max(0.05, min(1.0, opacity))
            }


    input("3. Press the button to continue ...\n")


    
    # FORMATY
    formats = payload.get("formats") or ["16x9"]
    if isinstance(formats, str):
        formats = [formats]
    print('AAAAAAAAAAAAAAAAA')
    formats = [f for f in formats if f in FORMAT_PRESETS] or ["16x9"]
    print('BBBBBBBBBBB')

    # TTS -> audio + timeline
    out_dir = os.path.join(project_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    srt_path = os.path.join(out_dir, "captions.srt")
    
    srt_url = _ensure_remote_url(srt_path, content_type="application/x-subrip") if srt_path else None
    if srt_url:
        srt_url = _encode_asset_url(srt_url)

    burn_subs = bool(payload.get("subtitles", {}).get("burn_in", True))
    # --- 6) Napisy jako CaptionAsset (opcjonalnie wypalane) ---
    captions_track = None
    if burn_subs and srt_url:
        # Wymuszenie CSS: color: white; background-color: yellow
        captions_track = {
            "clips": [{
                "asset": {
                    "type": "caption",
                    "src": srt_url,
                    "font": {
                        "family": "Open Sans",
                        "color": "#1a1919",
                        "size": 36
                    },
                    "background": {
                        "color": "#ffff00",    # background-color: yellow
                        "opacity": 1.0,          # pełne krycie tła
                        "padding": 12,
                        # "padding": "0 0.1em",    # lekki padding jak w typowych subtitle
                        "borderRadius": 0        # brak zaokrągleń, jak "czyste" tło
                    },
                    "margin": { "top": 0.8, "left": 0.08, "right": 0.08 }
                },
                "start": 0,
                "length": round(audio_duration, 3)
            }]}

    news_to_video_logger.info(f"[render_via_shotstack] Formats={formats} burn_in={burn_subs} "
                              f"xfade={{enable:{transitions.use_xfade}, dur:{transitions.duration}, type:'{transitions.transition}'}} "
                              f"branding={{opacity:{branding.opacity}, position:{branding.position}, scale:{branding.scale}, url:{branding.logo_path}}} ")



    # 1) Segmentacja tekstu
    segments = segment_text(text)
    news_to_video_logger.info(f"[render_via_shotstack] Segmentation done: segments={len(segments)} chars={len(text)}")

    input("4. Press the button to continue ...\n")


    audio_dir = os.path.join(project_dir, "audio")
    audio_path, timeline = synthesize_tts(segments, tts, audio_dir)
    audio_duration = _ffprobe_duration(audio_path) or 0.0
    news_to_video_logger.info(f"[render_video_local] TTS synthesized ==> duration:{audio_duration:.2f}s, voice:'{tts.voice}', provider:'{tts.provider}'\n\t {audio_path}")

    input("5. Press the button to continue ...\n")

    # --- 3) Publiczne URL-e dla assetów (S3/CDN) ---
    audio_url = _ensure_remote_url(audio_path, content_type="audio/mpeg")
    audio_url = _encode_asset_url(audio_url)


    # Walidacja audio URL + preflight
    if not _is_public_http(audio_url):
        raise RuntimeError(f"❌ [render_via_shotstack] Audio URL is not a public http(s) URL: {audio_url!r}")
    try:
        _head = requests.head(audio_url, allow_redirects=True, timeout=6)
        if _head.status_code >= 400:
            raise RuntimeError(f"❌ [render_via_shotstack] Audio URL not reachable (status {_head.status_code}): {audio_url}")
    except Exception as _e:
        raise RuntimeError(f"❌ [render_via_shotstack] Audio URL preflight failed: {audio_url} | {_e}")

    input("6. Press the button to continue ...\n")

    ass_path = os.path.join(out_dir, "captions.ass")
    generate_srt(timeline, srt_path)
    generate_ass_from_timeline(timeline, profile, ass_path, max_words=5, min_chunk_dur=0.7)
    news_to_video_logger.info(f"[render_video_local] Captions generated ==> srt:{srt_path}, ass:{ass_path}")


    


    outputs_map: Dict[str, str] = {}
    durations_for_min = []

    input("7. Press the button to continue ...\n")

    # # print(json.dumps(payload, ensure_ascii=False, indent=2))
    # print('\n\n\n\t\t\t KONIEC \n\n\n')
    # exit()

    for fmt in formats:
        fmt_key = fmt.replace(":", "x").replace("/", "x")
        p = profile_for(fmt, profile)
        # 4) Media -> segmenty (bez powielania)
        media_items = [MediaItem(**m) for m in payload.get("media", [])]
        seg_dir = os.path.join(project_dir, f"segments_{fmt_key}")

        news_to_video_logger.info(f"[render_video_local] ===> Prepare media segments ==> prepare_media_segments({media_items}, {audio_duration}, {p}, {seg_dir})")
        seg_paths, durations, total = prepare_media_segments(media_items, audio_duration, p, seg_dir)
        
        news_to_video_logger.info(f"[render_video_local] Segments encoded ==> count: {len(seg_paths)} "
                                    f"total: {total}s, sumDur: {sum(durations):.2f}s")


    # print('\t\t========= media_items ===========================')
    # print(media_items)
    # print('\t\t========= seg_paths ===========================')
    # print(seg_paths)
    # print('\t\t========= durations ===========================')
    # print(durations)


    # 5) Upewnij się, że efektywny czas wizualiów ≥ audio (uwzględnij overlap przejść)
    news_to_video_logger.info(f"[render_video_local] _effective_visual_duration({durations}, {transitions.use_xfade}, {float(transitions.duration)})")
    eff = _effective_visual_duration(durations, transitions.use_xfade, float(transitions.duration))
    news_to_video_logger.info(f"[render_video_local] Effective visual duration (pre-pad)={eff:.2f}s vs audio={audio_duration:.2f}s")

    if not seg_paths:
        visuals_raw = os.path.join(out_dir, f"video_concat_{fmt_key}.mp4")
        _make_color_segment(max(1.0, audio_duration or 3.0), p, visuals_raw, color="black")
        news_to_video_logger.info(f"[render_video_local] No visuals; generated filler={visuals_raw}")

    else:
        last_seg = seg_paths[-1]
        last_dur = durations[-1] if durations else 0.0
        safety_iter = 0

        news_to_video_logger.info(f'[render_video_local] last_dur: {last_dur}\n\ttransitions.duration: {transitions.duration}\n'
            f' [{eff + 1e-3} < {audio_duration}] ==> while {eff} + 1e-3 < {audio_duration} and {safety_iter} < 200\n'
            f' if {last_dur} <= ({transitions.duration} if {transitions.use_xfade} else 0.0): \n')

    news_to_video_logger.info(f"[render_via_shotstack] TTS synthesized ==> duration:{audio_duration:.2f}s, voice:'{tts.voice}', provider:'{tts.provider}'\n\t {audio_path}")

    # --- 4) Klipy wizualne z poprawnymi start/length (bez 'auto') ---
    visual_clips: List[Dict] = []
    start_time = 0.0

    # heurystyka: jeśli mamy wideo/obrazy – daj rozsądne length
    images_count = sum(1 for it in media_items if (it.get("type") or detect_media_type(it.get("src")) or "").lower() == "image")
    default_img_len = max(3.5, min(6.0, audio_duration / max(1, images_count)))
    default_vid_len = max(4.0, min(10.0, audio_duration / max(1, len(media_items))))

    print('media_items media_items media_items media_items')
    for i, item in enumerate(media_items):
        print(f'{i} media_items: {item}')
        src = item.get("src")
        typ = (item.get("type") or detect_media_type(src) or "").lower()
        url = _ensure_remote_url(src, content_type=None)
        url = _encode_asset_url(url)
        if not url:
            continue

        is_image = (typ == "image")
        length = default_img_len if is_image else default_vid_len

        clip: Dict[str, Any] = {
            "asset": {"type": "image" if is_image else "video", "src": url},
            "start": round(start_time, 3),
            "length": round(length, 3),
            "fit": "contain" if is_image else "crop"
        }

        # Ustaw transition only-in dla bieżącego i only-out dla poprzedniego (Shotstack preferuje jasne deklaracje)
        if ss_transition:
            if i > 0:
                clip["transition"] = {"in": ss_transition}
                prev = visual_clips[-1]
                prev.setdefault("transition", {})
                prev["transition"]["out"] = ss_transition

        visual_clips.append(clip)
        start_time += length  # zawsze rośnie – także dla wideo

    print('visual_clips visual_clips visual_clips visual_clips ')
    # Jeśli nie ma wizuali — czarna plansza na cały czas audio
    if not visual_clips:
        visual_clips.append({
            "asset": {"type": "shape", "shape": "rectangle", "fill": {"color": "#000000", "opacity": 1.0}},
            "start": 0,
            "length": round(audio_duration, 3),
            "fit": "cover"
        })


    # --- 7) Tracks & z-index: wizuale (spód) -> logo (nad) -> napisy (na wierzchu) ---
    # Oblicz całkowity czas wideo: suma długości wizuali vs długość audio
    total_duration = round(max(start_time, audio_duration, 1.0), 3)

    # Dopnij długość logo na cały materiał
    if logo_clip:
        logo_clip["length"] = total_duration

    # Jeżeli chcesz, aby kontener napisów istniał przez cały materiał (linie i tak renderują wg SRT):
    if captions_track:
        captions_track["clips"][0]["length"] = total_duration

    # KOLEJNOŚĆ WARSTW MA ZNACZENIE:
    # 0) wizuale (spód)
    base_tracks = [{"clips": visual_clips}]

    # 1) logo (overlay) – nad wizualami
    if logo_clip:
        base_tracks.append({"clips": [logo_clip]})

    # 2) napisy (top) – nad wszystkim
    if captions_track:
        base_tracks.append(captions_track)
        
    soundtrack = {"src": audio_url, "effect": "fadeInFadeOut", "volume": 1.0}

    outputs: Dict[str, Any] = {
        "audio": audio_path,
        "audio_url": audio_url,
        "srt": srt_path,
        "srt_url": srt_url,
    }

 
    for fmt in formats:
        spec = fmt_map.get(fmt, fmt_map["16x9"])

        edit_payload = {
            "timeline": {
                "background": "#000000",
                "tracks": base_tracks,
                "soundtrack": soundtrack
            },
            "output": {
                "format": "mp4",
                "resolution": spec["resolution"],
                "aspectRatio": spec["aspectRatio"],
                "fps": 25
            }
        }

        # Walidacja lokalna timeline'u – złapie błędy wcześniej
        _validate_timeline(edit_payload["timeline"])

        print(f'requests.post(f"{BASE_EDIT_URL}/render", headers={headers}, json={edit_payload}, timeout=30)')

        # Kolejkuj render
        try:
            resp = requests.post(f"{BASE_EDIT_URL}/render", headers=headers, json=edit_payload, timeout=30)
            data = resp.json()
        except Exception as err3:
            news_to_video_logger.info("❌ [shotstack] queue error (%s): %s", fmt, str(err3))
            raise

        if not resp.ok:
            news_to_video_logger.info("❌ Shotstack queue failed (%s): %s", fmt, data)
            raise RuntimeError(f"❌ Shotstack queue failed ({fmt}): {data}")

        render_id = (data.get("response") or {}).get("id")
        if not render_id:
            raise RuntimeError(f"❌ Shotstack queue response missing id for format {fmt}")

        # Zapamiętaj job_id w manifeście (opcjonalnie)
        try:
            update_manifest(project_dir, {"vendor": {"shotstack": {"job_id": render_id, "status": "queued"}}})
        except Exception:
            pass

        # Poll status
        poll_timeout = int(os.getenv("SHOTSTACK_POLL_TIMEOUT", "600"))
        poll_every = float(os.getenv("SHOTSTACK_POLL_EVERY", "3.0"))
        started = time.time()
        extra_done_waits = 10
        last_status = None
        url_ready = None

        while True:
            try:
                print(f'requests.get(f"{BASE_EDIT_URL}/render/{render_id}", headers={headers}, timeout=20)')
                s = requests.get(f"{BASE_EDIT_URL}/render/{render_id}", headers=headers, timeout=20)
                s.raise_for_status()
                sdata = s.json()
            except Exception as err_poll:
                news_to_video_logger.info("❌ [shotstack] poll error (%s): %s", fmt, str(err_poll))
                time.sleep(min(5.0, poll_every))
                if time.time() - started > poll_timeout:
                    raise RuntimeError(f"❌ Shotstack poll timeout for {fmt}")
                continue

            resp_js = (sdata.get("response") or {})
            status = (resp_js.get("status") or "").lower()
            url_ready = resp_js.get("url") or None
            progress = resp_js.get("progress")

            if status != last_status:
                try:
                    update_manifest(project_dir, {"vendor": {"shotstack": {"job_id": render_id, "status": status, "progress": progress}}})
                except Exception:
                    pass
                last_status = status

            if status in ("failed", "error"):
                err = resp_js.get("error") or "unknown"
                raise RuntimeError(f"❌ Shotstack render failed ({fmt}): {err}")

            if status == "done":
                if url_ready:
                    break
                if extra_done_waits <= 0 or time.time() - started > poll_timeout:
                    raise RuntimeError(f"❌ Shotstack returned DONE without URL for {fmt}")
                extra_done_waits -= 1
                time.sleep(max(1.0, poll_every))
                continue

            if status in ("queued", "fetching", "rendering", "exporting", "saving", ""):
                if time.time() - started > poll_timeout:
                    raise RuntimeError(f"❌ Shotstack poll timeout for {fmt}")
                time.sleep(poll_every)
                continue

            # unknown status -> czekaj do timeoutu
            if time.time() - started > poll_timeout:
                raise RuntimeError(f"❌ Shotstack poll timeout (unknown status '{status}') for {fmt}")
            time.sleep(poll_every)

        key_out = {
            "16:9": ("mp4_16x9", "mp4_16x9_url"),
            "1:1":  ("mp4_1x1",  "mp4_1x1_url"),
            "9:16": ("mp4_9x16", "mp4_9x16_url"),
        } [spec["aspectRatio"]]

        outputs[key_out[1]] = url_ready  # zapisujemy finalny URL hostowany przez Shotstack

    # Finalnie zaktualizuj manifest i zwróć
    update_manifest(project_dir, {"outputs": outputs})
    return outputs

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

def build_shotstack_timeline(form, video_params: dict, aspect_ratio: str = '16:9', tts_url: str | None = None):
    """
    Buduje obiekt (timeline, output) pod Shotstack na bazie pól formularza.
    - media_urls -> klipy image/video na TR1
    - napisy (opcjonalnie) -> TR3 (Title/HTML)
    - logo (opcjonalnie) -> TR2 (overlay)
    - przejścia -> transition in/out + overlap startów
    - soundtrack -> jeśli podasz tts_url
    """
    text = (form.get('text') or '').strip()
    use_subs = bool(form.get('burn_subtitles'))
    use_xfade = bool(form.get('use_xfade'))
    transition_name = _normalize_transition(form.get('xfade_transition'))
    transition_overlap = _overlap_seconds_for_transition(transition_name) if use_xfade else 0.0

    # domyślne długości (gdy nie znamy metadanych)
    default_img_len = float(form.get('image_length', 4) or 4)
    default_vid_len = float(form.get('video_length', 6) or 6)

    # LOGO
    logo_url = (form.get('logo_url') or '').strip()
    logo_pos = _map_logo_position(form.get('logo_position'))
    logo_opacity = float(form.get('logo_opacity') or 0.85)
    logo_scale = float(form.get('logo_scale') or 0.15)

    # MEDIA
    media_urls = _parse_media_urls(form)
    media_clips = []
    t = 0.0
    for i, url in enumerate(media_urls):
        is_vid = _is_video(url)
        length = default_vid_len if is_vid else default_img_len

        # OVERLAP dla crossfade – start bieżącego klipu cofamy o overlap
        if i > 0 and transition_overlap > 0:
            start = max(0.0, t - transition_overlap)
        else:
            start = t

        clip = {
            "asset": {"type": "video" if is_vid else "image", "src": url},
            "start": round(start, 3),
            "length": round(length, 3),
            "transition": {"in": transition_name, "out": transition_name} if use_xfade else None,
            # delikatny zoom na fotach, żeby nie stały: (opcjonalnie)
            # "effect": "zoomIn" if not is_vid else None,
            # dopasowanie w kadrze możliwe fit/scale – zostaw domyślne
        }
        # usuń None, żeby JSON był czysty
        clip = {k: v for k, v in clip.items() if v is not None}
        media_clips.append(clip)

        # zaktualizuj t o pełną długość (bez overlapu)
        t = start + length

    tracks = []
    if media_clips:
        tracks.append({"clips": media_clips})

    # LOGO – overlay na osobnym tracku
    logo_clips = []
    if logo_url:
        logo_clips.append({
            "asset": {"type": "image", "src": logo_url},
            "start": 0,
            "length": round(max(t, 1.0), 3),
            "position": logo_pos,     # np. topRight
            "opacity": logo_opacity,  # 0..1
            "scale": logo_scale       # 0.05..0.3 (jak w Twoim formularzu)
        })
    if logo_clips:
        tracks.append({"clips": logo_clips})

    # SUBTYTUŁY – prosty auto-timing z WPM (tytułowe klipy)
    if use_subs and text:
        speed = float(form.get('speed') or 1.0)
        seq = _split_text_to_subs(text, wpm=170, speed=speed)
        subs = []
        cur = 0.0
        for frag in seq:
            if cur >= t:  # nie wyświetlaj poza długością materiału
                break
            length = min(frag["length"], max(0.5, t - cur))
            subs.append({
                "asset": {
                    "type": "title",
                    "text": frag["text"],
                    "style": "subtitle",   # wygodny, czytelny styl
                },
                "start": round(cur, 3),
                "length": round(length, 3),
                # lekki fade na napisach
                "transition": {"in": "fade", "out": "fade"}
            })
            cur += length
        if subs:
            # UWAGA: Shotstack renderuje tracki w kolejności – napisy powinny być NA GÓRZE
            tracks.append({"clips": subs})

    # SOUNDTRACK – TTS jak jest dostępny
    soundtrack = None
    if tts_url:
        soundtrack = {"src": tts_url, "effect": "fadeInFadeOut"}  # efekt na audio (dokumentowane w guide’ach)

    timeline = {
        "background": "#000000",
        "tracks": tracks,
        **({"soundtrack": soundtrack} if soundtrack else {})
    }

    # OUTPUT – preset rozdzielczości + aspekt
    output = {
        "format": "mp4",
        "resolution": video_params.get("resolution", "fhd"),
        "aspectRatio": aspect_ratio,          # np. "16:9" | "1:1" | "9:16"
        **({"fps": video_params["fps"]} if video_params.get("fps") else {}),
        # alternatywnie: custom size (gdy chcesz 4K prawdziwe) – wtedy pomiń resolution/aspectRatio i podaj width/height
        # "size": {"width": 3840, "height": 2160}
    }

    return timeline, output

