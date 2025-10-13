from flask import (
    Blueprint, 
    request,
    current_app, 
    jsonify, 
    render_template, 
    redirect, 
    url_for, 
    abort, 
    send_from_directory, 
    flash
)
# from __future__ import annotations
import os
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from werkzeug.utils import secure_filename

from loggers import news_to_video_logger
from auth import login_required

# Import logiki z main.py
from news_to_video.main import (
    create_project,
    update_manifest,
    update_manifest_payload,
    list_voices,
    normalize_voice_id,
    detect_media_type,
    delete_project,
    scrap_page,
    delete_project_local_only,
    sync_project_to_s3
)
from news_to_video.renders_engines.s3_proc import (
    load_json, 
    save_json,
    s3_media_tree,
    _safe_load_manifest
)
from news_to_video.config import BASE_DIR, PROJECTS_DIR, test_data
# Import logiki z render_video.py
from news_to_video.render_video import (
    rerender_project,
    render_video,
    start_render_async
)
from news_to_video.renders_engines.shotstack import validate_shotstack_form, build_shotstack_timeline, SHOTSTACK_FALLBACK_LUMA, SHOTSTACK_FALLBACK_OVERLAY

news_to_video_bp = Blueprint(
    "news_to_video",
    __name__,
    url_prefix="/news-to-video",
    template_folder="templates/news_to_video",
    static_folder="static/news_to_video",
)

# -----------------------------
# Helpers
# -----------------------------
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}

def _allowed_file(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return (ext in ALLOWED_IMAGE_EXT) or (ext in ALLOWED_VIDEO_EXT)

def _as_relpath(abs_path: str) -> str:
    # Return a path relative to BASE_DIR for safe serving
    abs_path = os.path.abspath(abs_path)
    base = os.path.abspath(BASE_DIR)
    if not abs_path.startswith(base):
        raise ValueError("Path outside base directory")
    return os.path.relpath(abs_path, base)

# -----------------------------
# HTML views
# -----------------------------
@news_to_video_bp.get("/")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def index_html():
    news_to_video_logger.info(f'\n\t\tSTART ====> index_html()')
    """HTML index with projects list."""
    projects = []
    for root, dirs, files in os.walk(PROJECTS_DIR):
        print(f'[index_html] r:{root}, d:{dirs}, f:{files}')
        if "manifest.json" in files:
            m = load_json(os.path.join(root, "manifest.json"))
            # print(m)
            # exit()
            outputs = m.get("outputs", {}) or {}
            payload = m.get("payload", {}) or {}
            rel16 = rel11 = rel916 = ""
            try:
                if outputs.get("mp4_16x9"):
                    rel16 = _as_relpath(outputs["mp4_16x9"])
            except Exception:
                pass
            try:
                if outputs.get("mp4_1x1"):
                    rel11 = _as_relpath(outputs["mp4_1x1"])
            except Exception:
                pass
            try:
                if outputs.get("mp4_9x16"):
                    rel916 = _as_relpath(outputs["mp4_9x16"])
            except Exception:
                pass
            # wybierz pierwszy dostępny do mini-podglądu
            preview_rel = rel16 or rel11 or rel916


            # print(m)

            data_string = m.get("created_at").replace('ZZ', 'Z').replace('Z', '+00:00')
            data_string = m.get("created_at").strip('Z').split('+')[0]
            # data_string = data_string.replace('Z', '+00:00')
            # Tworzenie obiektu datetime
            data_objekt = datetime.fromisoformat(data_string)
            # Formatowanie do pożądanego formatu
            sformatowana_data = data_objekt.strftime('%Y-%m-%d %H:%M')

            projects.append({
                "project_id": m.get("project_id"),
                "title": m.get("title"),
                "status": m.get("status"),
                "created_at": sformatowana_data,
                "mp4_16x9_rel": rel16,
                "mp4_1x1_rel": rel11,
                "mp4_9x16_rel": rel916,
                "preview_rel": preview_rel,
                "payload": payload,
            })
    projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    s3_mt = s3_media_tree()
    return render_template(
        "news_to_video/video-news-list.html", 
        projects=projects,
        s3_mt=s3_mt
    )

@news_to_video_bp.get("/video-news-list")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def index_list_html():
    """HTML index with projects list."""
    projects = []
    for root, dirs, files in os.walk(PROJECTS_DIR):
        print(f'[index_html] r:{root}, d:{dirs}, f:{files}')
        if "manifest.json" in files:
            m = load_json(os.path.join(root, "manifest.json"))
            outputs = m.get("outputs", {}) or {}
            payload = m.get("payload", {}) or {}
            rel16 = rel11 = rel916 = ""
            try:
                if outputs.get("mp4_16x9"):
                    rel16 = _as_relpath(outputs["mp4_16x9"])
            except Exception:
                pass
            try:
                if outputs.get("mp4_1x1"):
                    rel11 = _as_relpath(outputs["mp4_1x1"])
            except Exception:
                pass
            try:
                if outputs.get("mp4_9x16"):
                    rel916 = _as_relpath(outputs["mp4_9x16"])
            except Exception:
                pass
            # wybierz pierwszy dostępny do mini-podglądu
            preview_rel = rel16 or rel11 or rel916
            # data_string = m.get("created_at").split('+')[0]
            # print(m.get("created_at"))
            data_string = m.get("created_at").replace('ZZ', 'Z').replace('Z', '+00:00')
            data_string = m.get("created_at").strip('Z').split('+')[0]
            # Tworzenie obiektu datetime
            data_objekt = datetime.fromisoformat(data_string)
            # Formatowanie do pożądanego formatu
            sformatowana_data = data_objekt.strftime('%Y-%m-%d %H:%M')

            projects.append({
                "project_id": m.get("project_id"),
                "title": m.get("title"),
                "status": m.get("status"),
                "created_at": sformatowana_data,
                "mp4_16x9_rel": rel16,
                "mp4_1x1_rel": rel11,
                "mp4_9x16_rel": rel916,
                "preview_rel": preview_rel,
                "payload": payload,
            })
    projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    s3_mt = s3_media_tree()
    return render_template(
        "news_to_video/video-news-list.html", 
        projects=projects,
        s3_mt=s3_mt
    )

@news_to_video_bp.get("/create")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def create_form():
    print(f'\n\t\tSTART ====> create_form()')

    import random
    klucze = list(test_data.keys())
    # Wylosuj jeden klucz z listy
    losowy_klucz = random.choice(klucze)
    # Użyj wylosowanego klucza, aby pobrać losowy rekord (wartość)
    losowy_rekord = test_data[losowy_klucz]
    test_data1, test_data2, test_data3, test_data4 = losowy_rekord.get('title'), losowy_rekord.get('description'), losowy_rekord.get('images'), losowy_rekord.get('main_image')
    test_data3 = '\n'.join(test_data3)
    print(test_data3)
    # test_data1, test_data2, test_data3, test_data4 = '', '', '', ''

    default_provider = request.args.get("provider", "google")
    voices = list_voices(default_provider)
    return render_template("news_to_video/create.html", 
        voices=voices, 
        default_provider=default_provider,
        test_data1=test_data1,
        test_data2=test_data2,
        test_data3=test_data3,
        test_data4=test_data4
    )


# [MODIFY] talk_to/news_to_video/routes.py — /create: wywołuj render asynchronicznie (zamiast blokować request)
@news_to_video_bp.post("/create")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def create_submit():
    print(f'\n\t\tSTART ====> create_submit()')

    # 1) Odczyt pól formularza
    form = request.form
    title = request.form.get("title", "").strip() or "News video"
    text = request.form.get("text", "").strip()
    provider = request.form.get("provider", "google").strip()
    voice_id = request.form.get("voice", "").strip()
    voice_id = normalize_voice_id(voice_id)
    speed = float(request.form.get("speed", "1.0") or 1.0)

    # Efekty
    burn_in = (request.form.get("burn_subtitles") == "on") or (request.form.get("burn_subtitles") == "true")
    use_xfade = (request.form.get("use_xfade") == "on") or (request.form.get("use_xfade") == "true")
    xfade_duration = float(request.form.get("xfade_duration", "0.5") or 0.5)
    xfade_transition = (request.form.get("xfade_transition") or "fade").strip()

    # FORMATY
    selected_formats = request.form.getlist("formats")
    if not selected_formats:
        selected_formats = ["16x9"]
    selected_formats = [f for f in selected_formats if f in ("16x9", "1x1", "9x16")] or ["16x9"]

    result_tts_url = None # URL do lektora

    # Renderer
    renderer_type = (request.form.get("renderer_type") or "local").strip().lower()

    if renderer_type not in ("local", "shotstack", "json2video", "mediaconvert", "openshot"):
        renderer_type = "local"
    renderer_cfg = {}
    if renderer_type == "shotstack":
        errs, shotstack_cfg = validate_shotstack_form(request.form)
        if errs:
            for e in errs:
                flash(e, 'error')
            return redirect(url_for('news_to_video.create_view'))
        preset_map = {
            '9x16_vertical':    {'width': 1080, 'height': 1920, 'fps': 25},
            '16x9_horizontal':  {'width': 1920, 'height': 1080, 'fps': 25},
            '1x1_square':       {'width': 1080, 'height': 1080, 'fps': 25},
            '720p_30':          {'width': 1280, 'height': 720,  'fps': 30, 'resolution': 'hd'},
            '1080p_30':         {'width': 1920, 'height': 1080, 'fps': 30, 'resolution': 'fhd'},
            '1080p_60':         {'width': 1920, 'height': 1080, 'fps': 60, 'resolution': 'fhd'},
            '4k_30':            {'width': 3840, 'height': 2160, 'fps': 30, 'resolution': 'uhd'},
        }
        video_params = preset_map.get(shotstack_cfg['preset'], preset_map['9x16_vertical'])

        renderer_cfg.update({
            "api_key": shotstack_cfg["api_key"],
            "env": shotstack_cfg["env"],
            "region": shotstack_cfg["region"],
            "preset": shotstack_cfg["preset"],
        })
        if shotstack_cfg.get("host"):
            renderer_cfg["host"] = shotstack_cfg["host"]
        if shotstack_cfg.get("webhook"):
            renderer_cfg["webhook"] = shotstack_cfg["webhook"]

        output_cfg = dict(renderer_cfg.get("output") or {})
        output_cfg["fps"] = video_params["fps"]
        if video_params.get("width") and video_params.get("height"):
            output_cfg["size"] = {
                "width": video_params["width"],
                "height": video_params["height"],
            }
        if video_params.get("resolution"):
            output_cfg["resolution"] = video_params["resolution"]
        renderer_cfg["output"] = output_cfg


    elif renderer_type == "json2video":
        renderer_cfg["api_key"] = (request.form.get("json2video_api_key") or "").strip()
    elif renderer_type == "mediaconvert":
        renderer_cfg["region"] = (request.form.get("mediaconvert_region") or "").strip()
        renderer_cfg["role_arn"] = (request.form.get("mediaconvert_role_arn") or "").strip()
        renderer_cfg["queue_arn"] = (request.form.get("mediaconvert_queue_arn") or "").strip()
        renderer_cfg["s3_output"] = (request.form.get("mediaconvert_s3_output") or "").strip()
    elif renderer_type == "openshot":
        renderer_cfg["api_url"] = (request.form.get("openshot_api_url") or "").strip()
        renderer_cfg["api_key"] = (request.form.get("openshot_api_key") or "").strip()

    # 2) Wstępny payload
    payload = {
        "title": title,
        "text": text,
        "media": [],
        "tts": {"provider": provider, "voice": voice_id, "speed": speed, "language": "pl"},
        "subtitles": {"burn_in": burn_in},
        "transitions": {"use_xfade": use_xfade, "duration": xfade_duration, "transition": xfade_transition},
        "brand": {"logo_path": None, "position": (request.form.get("logo_position") or "top-right").strip(),
                  "opacity": float(request.form.get("logo_opacity", "0.85") or 0.85),
                  "scale": float(request.form.get("logo_scale", "0.15") or 0.15)},
        "formats": selected_formats,
        "renderer": {"type": renderer_type, "config": renderer_cfg},
    }
    print(f'payload ====> {payload}')

    # 3) Utwórz projekt (powstaje manifest.json)
    manifest_tmp = create_project(payload)
    project_dir = manifest_tmp["project_dir"]

    # 4) Logo (plik/URL)
    logo_file = request.files.get("logo_file")
    logo_url = (request.form.get("logo_url") or "").strip()
    if logo_file and logo_file.filename:
        from werkzeug.utils import secure_filename
        brand_dir = os.path.join(project_dir, "brand")
        os.makedirs(brand_dir, exist_ok=True)
        lf = secure_filename(logo_file.filename)
        lpath = os.path.join(brand_dir, lf)
        logo_file.save(lpath)
        payload["brand"]["logo_path"] = lpath
    elif logo_url:
        payload["brand"]["logo_path"] = logo_url

    # 5) Zapis payloadu do manifestu
    update_manifest_payload(project_dir, payload)

    # 6) Zapis plików mediów + URL-e
    media_dir = os.path.join(project_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    saved_paths: List[str] = []
    for f in request.files.getlist("media_files"):
        if not f or not f.filename:
            continue
        if not _allowed_file(f.filename):
            flash(f"Pomijam plik: {f.filename} (niedozwolone rozszerzenie)")
            continue
        fname = secure_filename(f.filename)
        dest = os.path.join(media_dir, fname)
        f.save(dest)
        saved_paths.append(dest)

    media_urls_text = request.form.get("media_urls", "").strip()
    url_lines = [u.strip() for u in media_urls_text.splitlines() if u.strip()]
    saved_paths.extend(url_lines)

    media_items = []
    for path in saved_paths:
        mtype = detect_media_type(path)
        if mtype:
            media_items.append({"type": mtype, "src": path})
    update_manifest_payload(project_dir, {"media": media_items})

    for key, val in manifest_tmp.items():
        if isinstance(val, str):
            print(f'{key} ==>(str) {val}')
        elif isinstance(val, list):
            print(f'{key} ==>(list) {val}')
        else:
            if isinstance(val, dict):
                print(f'{key} ==>(dict)')
                for k,v in val.items():
                    print(f'\t{k} ==> {v}')
            else:
                print(f'{key} ==>(NaN) {val}')


    news_to_video_logger.info('# 7) START asynchronicznego renderu')
    print('\n\t\t\t 🚀 start_render_async ==> create_submit')
    start_render_async(project_dir)

    # 8) Przekierowanie od razu do szczegółów (tam będzie polling statusu)
    return redirect(url_for("news_to_video.detail_html", project_id=manifest_tmp["project_id"]))

# [MODIFY] talk_to/news_to_video/routes.py — detail_html: popraw przypisania ścieżek relatywnych (podmień całą funkcję)
@news_to_video_bp.get("/<project_id>")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def detail_html(project_id: str):
    manifest = None
    for root, dirs, files in os.walk(PROJECTS_DIR):
        if "manifest.json" in files:
            mpath = os.path.join(root, "manifest.json")
            m = load_json(mpath)
            if m.get("project_id") == project_id:
                manifest = m
                break
    if not manifest:
        abort(404)

    mp4_rel = mp4_1x1_rel = mp4_9x16_rel = srt_rel = audio_rel = ""

    rel_count = 0
    outs = manifest.get("outputs", {}) or {}
    # 16:9
    if outs.get("mp4_16x9"):
        try:
            mp4_rel = _as_relpath(outs["mp4_16x9"])
            rel_count+=1
        except Exception:
            mp4_rel = ""
    # 1:1
    if outs.get("mp4_1x1"):
        try:
            mp4_1x1_rel = _as_relpath(outs["mp4_1x1"])
            rel_count+=1
        except Exception:
            mp4_1x1_rel = ""
    # 9:16
    if outs.get("mp4_9x16"):
        try:
            mp4_9x16_rel = _as_relpath(outs["mp4_9x16"])
            rel_count+=1
        except Exception:
            mp4_9x16_rel = ""

    if outs.get("srt"):
        try:
            srt_rel = _as_relpath(outs["srt"])
        except Exception:
            srt_rel = ""
    if outs.get("audio"):
        try:
            audio_rel = _as_relpath(outs["audio"])
        except Exception:
            audio_rel = ""

    return render_template(
        "news_to_video/detail.html",
        manifest=manifest,
        rel_count=rel_count,
        mp4_rel=mp4_rel,
        mp4_1x1_rel=mp4_1x1_rel,
        mp4_9x16_rel=mp4_9x16_rel,
        srt_rel=srt_rel,
        audio_rel=audio_rel,
    )

# -----------------------------
# JSON API (kept for programmatic access)
# -----------------------------
@news_to_video_bp.get("/api/projects")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def api_projects():
    print(f'\n\t\tSTART ====> api_projects()')
    results = []
    for root, dirs, files in os.walk(PROJECTS_DIR):
        if "manifest.json" in files:
            m = load_json(os.path.join(root, "manifest.json"))
            results.append({
                "project_id": m.get("project_id"),
                "title": m.get("title"),
                "status": m.get("status"),
                "created_at": m.get("created_at"),
                "outputs": m.get("outputs", {}),
            })
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return jsonify({"projects": results})

@news_to_video_bp.post("/api/")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def api_create_json():
    print(f'\n\t\tSTART ====> api_create_json()')
    """Create a project from JSON and render immediately (MVP)."""
    payload = request.get_json(force=True, silent=False) or {}
    tts = payload.get("tts") or {}
    if isinstance(tts, dict) and "voice" in tts:
        tts["voice"] = normalize_voice_id(tts["voice"])
        payload["tts"] = tts
    manifest_tmp = create_project(payload)
    outputs = render_video(manifest_tmp["project_dir"])
    return jsonify({
        "project_id": manifest_tmp["project_id"],
        "outputs": outputs,
    })

@news_to_video_bp.get("/api/<project_id>")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def api_detail(project_id: str):
    print(f'\n\t\tSTART ====> api_details({project_id})')
    for root, dirs, files in os.walk(PROJECTS_DIR):
        if "manifest.json" in files:
            m = load_json(os.path.join(root, "manifest.json"))
            # if m.get("project_id") == project_id:
            #     return jsonify(m)
            if not isinstance(m, dict):
                continue
            if m.get("project_id") == project_id:
                return jsonify(m)
    return jsonify({"error": "project not found"}), 404

# -----------------------------
# Voices endpoint (AJAX for form)
# -----------------------------
@news_to_video_bp.get("/voices")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def voices():
    print(f'\n\t\tSTART ====> voices()')
    """
    Zwraca listę głosów dla wybranego providera TTS (JSON),
    wywoływane przez front: /voices?provider=google|microsoft
    """
    provider = (request.args.get('provider') or 'google').strip().lower()
    return jsonify({"voices": list_voices(provider)})

# -----------------------------
# Safe file serving (relative to BASE_DIR)
# -----------------------------
@news_to_video_bp.get("/file/<path:relpath>")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def serve_file(relpath: str):
    print(f'\n\t\tSTART ====> serve_file({relpath})')
    abs_path = os.path.abspath(os.path.join(BASE_DIR, relpath))
    if not abs_path.startswith(os.path.abspath(BASE_DIR)):
        abort(403)
    if not os.path.isfile(abs_path):
        abort(404)
    directory = os.path.dirname(abs_path)
    filename = os.path.basename(abs_path)
    return send_from_directory(directory, filename, as_attachment=False)

# [MODIFY] — trasa kasowania: korzysta z usuwania lokalnego z zabezpieczeniem S3
@news_to_video_bp.post("/delete/<project_id>")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def delete_project_view(project_id: str):
    ok = delete_project_local_only(project_id, ensure_s3=True)
    # JSON?
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"project_id": project_id, "deleted_local": ok, "preserved_s3": True}), (200 if ok else 404)
    return redirect(url_for("news_to_video.index_html"))

# [MODIFY] talk_to/news_to_video/routes.py — /update/<id> przerenderuj ASYNC + dodaj endpoint status
@news_to_video_bp.post("/update/<project_id>")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def update_project_view(project_id: str):
    # znajdź katalog projektu
    pdir = None
    for root, dirs, files in os.walk(PROJECTS_DIR):
        if "manifest.json" in files:
            m = load_json(os.path.join(root, "manifest.json"))
            if m.get("project_id") == project_id:
                pdir = root
                break
    if not pdir:
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({"error": "project not found"}), 404
        abort(404)

    print('\n\t\t\t 🚀 start_render_async ==> update_project_view')
    start_render_async(pdir)

    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"project_id": project_id, "queued": True})
    return redirect(url_for("news_to_video.detail_html", project_id=project_id))


@news_to_video_bp.get("/api/status/<project_id>")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def api_status(project_id: str):
    for root, dirs, files in os.walk(PROJECTS_DIR):
        if "manifest.json" in files:
            mpath = os.path.join(root, "manifest.json")
            # m = load_json(mpath)
            # if m.get("project_id") == project_id:
            m = _safe_load_manifest(mpath)
            if not m:
                continue
            if m.get("project_id") == project_id:
                # >>> DODAJ — auto-sync do S3 po zakończeniu
                if (m.get("status") == "done") and (not m.get("s3_synced", False)):
                    try:
                        sync_project_to_s3(root)   # dopchnij outputs do S3
                        m["s3_synced"] = True
                        save_json(mpath, m)          # zapis + mirror manifestu
                    except Exception as e:
                        m["error"] = f"S3 sync error: {e}"
                        save_json(mpath, m)

                outs = m.get("outputs", {}) or {}
                return jsonify({
                    "project_id": project_id,
                    "status": m.get("status", "unknown"),
                    "error": m.get("error"),
                    "outputs": outs,
                })
    return jsonify({"error": "project not found"}), 404

@news_to_video_bp.post("/scrap_page")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def scrap_page_view():
    print(f'\n\t\tSTART ====> scrap_page_view()')
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Brak pola 'url'"}), 400
    try:
        result = scrap_page(url, language="pl")
        # print(f'[scrap_page_view]',result)
        # Zwróć tylko to, co potrzebne do formularza
        return jsonify({
            "ok": True,
            "payload": {
                "title": result.get("title", ""),
                "text": result.get("text", ""),
                "media": result.get("media", []),
                "source_url": result.get("source_url", url),
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    
# endpointy: JSON drzewka + HTML lista
@news_to_video_bp.get("/api/s3_media_tree")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def api_s3_media_tree():
    try:
        data = s3_media_tree()
        return jsonify({"ok": True, "tree": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# przekażemy gotową strukturę entries z podglądami
@news_to_video_bp.get("/s3_media")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def s3_media_html():
    print(f"\n\t\tSTART ==> s3_media_html()") 
    err = None
    data = {}
    try:
        data = s3_media_tree()
        # print(data)
    except Exception as e:
        err = str(e)
    return render_template("news_to_video/s3_media.html", data=data, error=err)


@news_to_video_bp.route('/renderer-form')
@login_required(role=["admin", "redakcja", "moderator","tester"])
def renderer_form():
    print(f'\n\t\tSTART ==> renderer_form()')
    rtype = (request.args.get('type') or 'local').strip().lower()
    # mapowanie nazwa -> plik partiala
    template_map = {
        'shotstack': 'news_to_video/renderer/shotstack.html',
        'json2video': 'news_to_video/renderer/json2video.html',
        'mediaconvert': 'news_to_video/renderer/mediaconvert.html',
        'openshot': 'news_to_video/renderer/openshot.html',
        'openai_sora': 'news_to_video/renderer/openai-sora.html',
        # 'local' nie wymaga pól – możesz zwrócić pusty string:
        'local': None,
    }
    tpl = template_map.get(rtype)
    # SUBTITLE = 'Wielka Brytania • #Policja'
    print(tpl)
    # exit()

    if not tpl:
        return ''  # dla local lub nieznanych – nic nie wstrzykujemy
    try:
        return render_template(
            tpl,
            fallback_overlay=SHOTSTACK_FALLBACK_OVERLAY,
            fallback_luma=SHOTSTACK_FALLBACK_LUMA)
    except Exception:
        abort(404)





# --- 1) CREATE VIEW (GET) TEST ---
@news_to_video_bp.route('/create_get', methods=['GET'])
@login_required(role=["admin", "redakcja", "moderator","tester"])
def create_view_get():
    print(f'\n\t\tSTART ==> create_view_get()')
    """
    Widok formularza tworzenia projektu.
    Dostarcza default_provider i listę głosów pod provider (używane w <select>).
    """
    # Domyślny provider TTS z configu lub 'google'
    default_provider = getattr(current_app.config, 'DEFAULT_TTS_PROVIDER', 'google')
    voices = list_voices(default_provider)
    return render_template(
        'news_to_video/create.html',
        default_provider=default_provider,
        voices=voices,
        submit_path='/news-to-video/create_get/submit'
    )

# --- (fragment) CREATE SUBMIT – jak użyć Shotstack (POST) ---
@news_to_video_bp.route('/create_get/submit', methods=['POST','GET'])
@login_required(role=["admin", "redakcja", "moderator","tester"])
def create_submit_get():
    print(f'\n\t\tSTART ==> create_submit_get()')
    # print(request.form)
    from news_to_video.renders_engines.shotstack import SHOTSTACK_API_KEY
    form = request.form
    renderer_type = form.get('renderer_type', 'local')

    # …tu Twoja logika TTS… np. result_tts_url = synthesize_tts_and_get_url(...)
    result_tts_url = None  # jeśli masz URL lektora, podaj go tutaj

    if renderer_type == 'shotstack':
        # 1) Walidacja pól Shotstack
        errs, shot_cfg = validate_shotstack_form(form)



        print(shot_cfg)
        print('\n\n\n\t\t\t STOP / PAUZA \n\n\n')
        # exit()


        if errs:
            for e in errs:
                flash(e, 'error')
            return redirect(url_for('news_to_video.create_view_get'))

        # 2) Ustalenie parametrów wyjścia na bazie presetu
        preset_map = {
            '720p_30':  {'resolution': 'hd',  'fps': 30, 'w': 1280, 'h': 720},
            '1080p_30': {'resolution': 'fhd', 'fps': 30, 'w': 1920, 'h': 1080},
            '1080p_60': {'resolution': 'fhd', 'fps': 60, 'w': 1920, 'h': 1080},
            '4k_30':    {'resolution': 'fhd', 'fps': 30, 'w': 3840, 'h': 2160},  # jeśli chcesz prawdziwe 4k, rozważ custom size
        }
        video_params = preset_map[shot_cfg['preset']]

        # 3) Aspekt – z checkboxów "Formaty wideo" możesz wywołać build_shotstack_timeline kilka razy
        requested_aspects = form.getlist('formats') or ['16x9']  # np. ['16x9','9x16','1x1']
        aspect_map = {'16x9': '16:9', '9x16': '9:16', '1x1': '1:1'}

        # 4) Zbuduj timeline (tu wariant dla pierwszego aspektu; w praktyce pętlą po wszystkich)
        aspect_ratio = aspect_map.get(requested_aspects[0], '16:9')
        timeline, output = build_shotstack_timeline(
            form=form,
            video_params=video_params,
            aspect_ratio=aspect_ratio,
            tts_url=result_tts_url
        )

        print('# 5) Wyślij render do Shotstack')
        import requests

        api_host = f"https://{shot_cfg['host']}"  # np. api.shotstack.io/stage albo /v1
        payload = {"timeline": timeline, "output": output}
        # headers = {"x-api-key": shot_cfg['api_key'], "Content-Type": "application/json"}
        headers = {"x-api-key": SHOTSTACK_API_KEY, "Content-Type": "application/json"}

        if shot_cfg.get('webhook'):
            payload["callback"] = shot_cfg['webhook']

        print(f'requests.post(f"{api_host}/render", json={payload}, headers={headers}, timeout=30)')

        r = requests.post(f"{api_host}/render", json=payload, headers=headers, timeout=30)
        if r.status_code >= 300:
            flash(f"Shotstack render error: {r.status_code} {r.text}", "error")
            return redirect(url_for('news_to_video.create_view_get'))

        job = r.json()
        # TODO: zapisz job_id / status do DB i pokaż widok statusu
        flash("Zlecono render do Shotstack.", "success")
        return redirect(url_for('news_to_video.create_view_get'))

    # …obsługa innych backendów lub lokalnego…
    flash("Zlecono render lokalny.", "success")
    return redirect(url_for('news_to_video.create_view_get'))

@news_to_video_bp.route('/api/project/<project_id>/manifest', methods=['POST'])
@login_required(role=["admin", "redakcja", "moderator","tester"])
def api_update_manifest_field(project_id):
    print(f'\n\t\tSTART ==> api_update_manifest_field({project_id})')
    """
    Aktualizuje pole w manifeście wg kropkowanej ścieżki (np. 'payload.formats').
    Body: { "path": "payload.formats", "op": "set", "value": [...] }
    """
    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()
    value = data.get('value', None)

    if not path:
      return jsonify(ok=False, error='Brak path'), 400

    # Ustal plik manifestu (dopasuj do swojej struktury katalogów)
    base_dir = getattr(current_app.config, 'PROJECTS_DIR', './projects')
    manifest_path = os.path.join(base_dir, project_id, 'manifest.json')
    if not os.path.isfile(manifest_path):
      return jsonify(ok=False, error='Manifest not found'), 404

    # Wczytaj, ustaw, zapisz
    try:
      with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

      # Ustaw wg "dot path"
      def set_by_path(obj, dot_path, new_val):
          keys = dot_path.split('.')
          cur = obj
          for k in keys[:-1]:
              if k not in cur or not isinstance(cur[k], (dict,)):
                  cur[k] = {}
              cur = cur[k]
          cur[keys[-1]] = new_val

      set_by_path(manifest, path, value)

      with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

      return jsonify(ok=True)
    except Exception as e:
      current_app.logger.exception("Manifest update failed")
      return jsonify(ok=False, error=str(e)), 500




from llm import ask_model_openai
# Zapytania prompt do modelu
PROMPTS = [
    {
        "id": "test_summary20_pl",
        "label": "Streszczenie TEST PL (max 20 słów)",
        "system": "Jesteś asystentem, który tworzy zwięzłe zajawki do przeczytania artykułu na portalu londynek.net. Zajawka ma być w języku w jakim dostarczone zostały dane.",
        "user_prefix": "Napisz zajawkę do artykułu w ~20 słowach. Zachęć do przeczytania całego artykułu na portalu londynek.net.\n\nDANE:"
    },
    {
        "id": "test_summary50_pl",
        "label": "Streszczenie TEST PL (max 50 słów)",
        "system": "Jesteś asystentem, który tworzy zwięzłe zajawki do przeczytania artykułu na portalu londynek.net. Zajawka ma być w języku w jakim dostarczone zostały dane.",
        "user_prefix": "Napisz zajawkę do artykułu w ~50 słowach. Zachęć do przeczytania całego artykułu na portalu londynek.net.\n\nDANE:"
    },
    {
        "id": "summary_pl",
        "label": "Streszczenie PL (ok. 120 słów)",
        "system": "Jesteś asystentem, który tworzy zwięzłe streszczenia wiadomości po polsku.",
        "user_prefix": "Streść poniższy artykuł w ~120 słowach. Zachowaj neutralny ton dziennikarski. Text podpisz: 'Dla londynek.net Ferdynand Poważny'\n\nDANE:"
    },
    {
        "id": "funny_summary_pl",
        "label": "Humorystyczne streszczenie PL (ok. 120 słów)",
        "system": "Jesteś asystentem z dużym poczuciem humoru, zajmujesz się tworzeniem streszczeń wiadomości po polsku.",
        "user_prefix": "Streść poniższy artykuł w ~120 słowach. Text podpisz: 'Dla londynek.net Ferdynand Śmieszny'\n\nDANE:"
    },
    {
        "id": "radio_tone_pl",
        "label": "Przepisz w tonie radiowym (PL)",
        "system": "Jesteś lektorem-redaktorem. Uprość konstrukcje zdań, zachowaj sens.",
        "user_prefix": "Przepisz tekst w tonie radiowym do odczytu na antenie. Krótsze zdania, klarowna składnia. Text podpisz: 'Dla londynek.net Ferdynand Zradia'\n\nDANE:"
    },
    {
        "id": "titles5_pl",
        "label": "5 tytułów (PL)",
        "system": "Jesteś redaktorem tytułów prasowych.",
        "user_prefix": "Zaproponuj 5 zwięzłych, nieclickbaitowych tytułów na podstawie danych.\n\nDANE:"
    }
]

def get_prompt_by_id(pid: str):
    for p in PROMPTS:
        if p["id"] == pid:
            return p
    return None

@news_to_video_bp.route('/prompts', methods=['GET'])
def prompts():
    print(f'\n\t\tSTART ====> prompts()')
    """Zwróć listę dostępnych promptów (id, label)."""
    items = [{"id": p["id"], "label": p["label"]} for p in PROMPTS]
    return jsonify({"prompts": items})

@news_to_video_bp.route('/apply-prompt', methods=['POST'])
def scrap_url_apply_prompt():
    print(f'\n\t\tSTART ====> scrap_url_apply_prompt()')
    """
    Zastosuj wybrany prompt do danych ze scrapera i zwróć wynik.
    Body: { "prompt_id": "...", "data": { title, text, media[], source_url, language } }
    """
    j = request.get_json(silent=True) or {}
    prompt_id = (j.get("prompt_id") or "").strip()
    data      = j.get("data") or {}
        
    pr = get_prompt_by_id(prompt_id)
    if not pr:
        return jsonify(ok=False, error="Prompt not found"), 404

    # user_prompt: bierzemy stałą instrukcję i dokładamy surowe dane w JSON (czytelne dla modelu)
    try:
        user_payload_str = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        user_payload_str = str(data)

    user_prompt = f"{pr['user_prefix']}\n{user_payload_str}"

    try:
        print(f'\n\task_model_openai({pr["system"]}, user_prompt)', end=' ')
        result_text = ask_model_openai(pr["system"], user_prompt)
        print(f'====> {result_text}\n')
    except Exception as e:
        current_app.logger.exception("apply_prompt error")
        return jsonify(ok=False, error=str(e)), 500

    # Heurystyka: jeśli prosiliśmy o 5 tytułów, nie nadpisuj „text”
    res = {"ok": True, "result_text": None, "result_title": None}
    if prompt_id == "titles5_pl":
        res["result_text"] = result_text  # wstawimy do textarea, albo front może to pokazać w modalu
    else:
        res["result_text"] = result_text

    return jsonify(res)


@news_to_video_bp.post('/api/render')
def api_render():
    data = request.get_json(silent=True) or {}
    # Rozpoznaj format wejścia:
    #  - nowy: pełny manifest (ma klucz 'payload')
    #  - stary: sam payload → owiń w manifest
    if 'payload' in data and isinstance(data['payload'], dict):
        client_manifest = dict(data)  # kopia
    else:
        client_manifest = {"payload": data}

    # Project ID: preferuj to z manifestu albo nazwy projektu z legacy pola,
    # w ostateczności wygeneruj.
    project_id = (
        client_manifest.get('project_id')
        or client_manifest['payload'].get('project')
        or datetime.utcnow().strftime('proj-%Y%m%d-%H%M%S')
    )

    workdir = current_app.config.get('VIDEO_LOCAL_WORKDIR', PROJECTS_DIR)
    project_dir = str(Path(workdir) / project_id)

    # Uzupełnij/ustandaryzuj pola kontrolne (status nadpisujemy na 'queued')
    server_manifest = {
        **client_manifest,
        "project_id": project_id,
        "status": "queued",
        "created_at": client_manifest.get("created_at") or (datetime.now(timezone.utc)),
        "error": None,
        "logs": client_manifest.get("logs") or [],
        "outputs": client_manifest.get("outputs") or {},
        "title": client_manifest.get("title") or client_manifest["payload"].get("title") or ""
    }


    # print(json.dumps(server_manifest, indent=2, ensure_ascii=True))
    # exit()

    # Zapisz manifest dostarczony z frontu (z uzupełnionymi polami kontrolnymi)
    save_json(os.path.join(project_dir, 'manifest.json'), server_manifest)

    print('\n\t\t\t 🚀 start_render_async ==> api_render')
    # Kolejka renderu
    job_id = start_render_async(project_dir)
    return jsonify(ok=True, status="queued", project_id=project_id, job_id=job_id)


def api_render_depr():
    data = request.get_json(silent=True) or {}
    # wygeneruj project_id jeśli nie podano
    project_id = (data.get('project') or
                  datetime.utcnow().strftime('proj-%Y%m%d-%H%M%S'))
    workdir = current_app.config.get('VIDEO_LOCAL_WORKDIR', PROJECTS_DIR)
    project_dir = str(Path(workdir) / project_id)
    print(f'workdir===>{workdir}')
    print(data)

    manifest = {
        "project_id": project_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc),
        "payload": data,                # zachowaj, jeśli potrzebne do renderu
    }
    save_json(os.path.join(project_dir, 'manifest.json'), manifest)

    print('\n\t\t\t 🚀 start_render_async ==> api_render_depr')
    job_id = start_render_async(project_dir)

    return jsonify(ok=True, status="queued", project_id=project_id, job_id=job_id)


# --- Provider Proxy: formularz + wywołanie API dostawcy ---
@news_to_video_bp.get("/provider-proxy")
@login_required(role=["admin", "redakcja", "moderator", "tester"])
def provider_proxy_form():
    """
    Prosty formularz do testowania wywołań API zewnętrznych dostawców (np. Shotstack).
    """
    # Przykładowe predefiniowane endpointy (możesz rozbudować / wczytać z configu)
    presets = [
        {"label": "Shotstack /render (prod eu1)", "url": "https://api.shotstack.io/v1/render"},
        {"label": "Shotstack /render (stage eu1)", "url": "https://api.shotstack.io/stage/render"},
    ]
    return render_template("news_to_video/provider_proxy.html", presets=presets)


@news_to_video_bp.post("/api/provider-proxy")
@login_required(role=["admin", "redakcja", "moderator","tester"])
def provider_proxy_api():
    """
    Przyjmuje { render_url, method?, headers?, payload? } i wywołuje zewnętrzne API.
    Zwraca status + treść odpowiedzi (JSON jeśli da się sparsować).
    """
    import requests
    data = request.get_json(silent=True) or {}
    render_url = (data.get("render_url") or "").strip()
    method = (data.get("method") or "POST").strip().upper()
    raw_headers = data.get("headers") or {}
    payload = data.get("payload")

    # Bezpieczeństwo: prosta allowlista hostów (rozszerz wg potrzeb)
    ALLOW_HOSTS = (
        "api.shotstack.io",
    )
    try:
        from urllib.parse import urlparse
        u = urlparse(render_url)
        if u.scheme not in ("http", "https") or not u.netloc:
            return jsonify(ok=False, error="Nieprawidłowy adres URL."), 400
        if not any(u.netloc.endswith(h) for h in ALLOW_HOSTS):
            return jsonify(ok=False, error=f"Host {u.netloc} nie jest dozwolony."), 400
    except Exception:
        return jsonify(ok=False, error="Nie udało się zweryfikować URL."), 400

    # Nagłówki: akceptuj dict albo JSON-string
    headers = {}
    if isinstance(raw_headers, dict):
        headers = raw_headers
    elif isinstance(raw_headers, str):
        try:
            headers = json.loads(raw_headers)
        except Exception:
            return jsonify(ok=False, error="Pole headers nie jest poprawnym JSON-em."), 400

    # Domyślne nagłówki, jeśli nie podano
    headers.setdefault("Accept", "application/json")
    if method in ("POST", "PUT", "PATCH"):
        headers.setdefault("Content-Type", "application/json")

    try:
        kwargs = {"headers": headers, "timeout": 60}
        if method in ("POST", "PUT", "PATCH"):
            kwargs["data"] = json.dumps(payload) if not isinstance(payload, (str, bytes)) else payload

        resp = requests.request(method, render_url, **kwargs)
        # Próbuj zwrócić JSON; jeśli się nie da, oddaj tekst
        try:
            body = resp.json()
        except Exception:
            body = {"_raw": resp.text[:2000]}  # krótki zrzut treści

        return jsonify({
            "ok": resp.ok,
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "response": body
        }), (200 if resp.ok else 502)

    except requests.Timeout:
        return jsonify(ok=False, error="Timeout przy wywołaniu dostawcy."), 504
    except Exception as e:
        return jsonify(ok=False, error=f"Błąd wywołania: {e}"), 502
