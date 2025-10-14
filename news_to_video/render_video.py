# news_to_video/render_video.py
import os
from pydub import AudioSegment
import shlex
from typing import Any, List, Dict, Optional, Tuple
import concurrent.futures
import threading
import time
import requests
import json
from pathlib import Path

from news_to_video.config import (
     _ACTIVE_JOBS,
     _EXECUTOR,
     FORMAT_PRESETS, 
     RENDER_MAX_WORKERS
)
from news_to_video.renders_engines.s3_proc import (
    load_json,
    save_json
)
from loggers import news_to_video_logger
from news_to_video.main import (
    segment_text,
    synthesize_tts,
    profile_for,
    prepare_media_segments,
    find_project_dir,
    generate_srt, 
    generate_ass_from_timeline,
    update_manifest,
    _xfade_concat,
    _concat_videos,
    _run,
    _make_image_segment,
    _ffprobe_duration,
    _apply_branding_and_subtitles,
    _mux_video_audio,
    _get_renderer,
    _effective_visual_duration,
    _make_color_segment,
    _s3_key_for_local, 
    _s3_upload_file,
    _write_srt_by_chunks, 
    _ensure_remote_url,
    TTSSettings,
    RenderProfile,
    TransitionsConfig,
    BrandConfig,
    MediaItem
)
from news_to_video.renders_engines.shotstack import render_via_shotstack
from news_to_video.renders_engines.json2video import render_via_json2video
# from news_to_video.renders_engines.mediaconvert import 
from news_to_video.renders_engines.openshot import render_via_openshot 
from news_to_video.renders_engines.openai_sora import render_via_openai_sora


# -------------------------
# Manifest: walidacja
# -------------------------
def validate_manifest(manifest: dict) -> bool:
    """
    Minimalna walidacja manifestu:
      - manifest to dict
      - posiada project_id (str, niepuste)
      - posiada payload (dict)
      - jest JSON-serializowalny
    """
    try:
        if not isinstance(manifest, dict):
            news_to_video_logger.error("[manifest] not a dict")
            return False
        pid = manifest.get("project_id")
        if not isinstance(pid, str) or not pid.strip():
            news_to_video_logger.error("[manifest] missing/invalid project_id")
            return False
        if not isinstance(manifest.get("payload"), dict):
            news_to_video_logger.error("[manifest] missing/invalid payload")
            return False
        # test serializacji
        import json as _json
        _json.dumps(manifest, ensure_ascii=False)
        return True
    except Exception as e:
        news_to_video_logger.error("[manifest] JSON validation error: %s", e)
        return False

def ensure_valid_or_raise(manifest: dict, where: str = "") -> None:
    """
    Rzuć wyjątek gdy manifest jest niepoprawny — zatrzymuje pipeline.
    """
    if not validate_manifest(manifest):
        msg = f"Invalid manifest{(' at ' + where) if where else ''}"
        raise RuntimeError(msg)

# GŁÓWNY punkt wejścia: render_video → dyspozytor
def render_video(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict:
    print(f'\n\t\tSTART ===> render_video()')
    news_to_video_logger.info("[render_video] START project_dir=%s", project_dir)
    """
    Dispatcher: wybiera backend renderujący zgodnie z payload['renderer']['type'].
    Domyślnie używa backendu lokalnego (ffmpeg).
    """
    manifest_path = os.path.join(project_dir, "manifest.json")
    manifest = load_json(manifest_path)

    news_to_video_logger.info(f'🔔 1. manifest_path: {manifest_path}\nmanifest.keys(): {manifest.keys()}')
    # test1 = {"created_at": "2025-09-24T11:00:32.549ZZ", "error": None, "logs": [], "outputs": {}, "payload": {"title": "Sondaż: Niemal połowa Polaków nie zgłosiłaby się do obrony kraju w przypadku zagrożenia wojną", "text": "Niemal połowa Polaków nie zgłosiłaby się do obrony kraju w razie wojny – tak wynika z najnowszego sondażu IBRiS. Sprawdź szczegóły i analizę na londynek.net!", "media": [{"type": "image", "src": "https://assets.aws.londynek.net/images/jdnews-agency/2191248/434080-202509221115-lg2.jpg?t=1758543367.000000"}, {"type": "image", "src": "https://assets.aws.londynek.net/images/infographics/434083-202509221123-xl.jpg.webp?t=1758543851.000000?t=1758540273168"}], "formats": ["16x9"], "renderer": {"type": "local", "config": {"output": {"filename": "output.mp4"}, "fps": 25, "burn_captions": False}}, "subtitles": {"burn_in": True}, "transitions": {"use_xfade": True, "transition": "fade", "duration": 1.5}, "tts": {"language": "pl", "provider": "google", "speed": 1, "voice": "pl-PL-Chirp3-HD-Autonoe"}, "brand": {"logo_path": "", "opacity": 0.85, "position": "top-right", "scale": 0.15}}, "project_id": None, "status": "draft", "title": "Sondaż: Niemal połowa Polaków nie zgłosiłaby się do obrony kraju w przypadku zagrożenia wojną"}
    # print(manifest)
    # test2 = {'created_at': '2025-09-24T11:00:32.549ZZ', 'error': None, 'logs': [], 'outputs': {}, 'payload': {'title': 'Sondaż: Niemal połowa Polaków nie zgłosiłaby się do obrony kraju w przypadku zagrożenia wojną', 'text': 'Niemal połowa Polaków nie zgłosiłaby się do obrony kraju w razie wojny – tak wynika z najnowszego sondażu IBRiS. Sprawdź szczegóły i analizę na londynek.net!', 'media': [{'type': 'image', 'src': 'https://assets.aws.londynek.net/images/jdnews-agency/2191248/434080-202509221115-lg2.jpg?t=1758543367.000000'}, {'type': 'image', 'src': 'https://assets.aws.londynek.net/images/infographics/434083-202509221123-xl.jpg.webp?t=1758543851.000000?t=1758540273168'}], 'formats': ['16x9'], 'renderer': {'type': 'local', 'config': {'output': {'filename': 'output.mp4'}, 'fps': 25, 'burn_captions': False}}, 'subtitles': {'burn_in': True}, 'transitions': {'use_xfade': True, 'transition': 'fade', 'duration': 1.5}, 'tts': {'language': 'pl', 'provider': 'google', 'speed': 1, 'voice': 'pl-PL-Chirp3-HD-Autonoe'}, 'brand': {'logo_path': '', 'opacity': 0.85, 'position': 'top-right', 'scale': 0.15}}, 'project_id': 'proj-20250924-110045', 'status': 'processing', 'title': 'Sondaż: Niemal połowa Polaków nie zgłosiłaby się do obrony kraju w przypadku zagrożenia wojną'}

    payload = manifest.get("payload", {}) or {}
    rtype = _get_renderer(payload)
    news_to_video_logger.info("[render_video] Renderer type resolved: %s", rtype)

    print(f'\n','* *'*30, f'\n\t\trender_via_{rtype}\n',payload.keys(),'\n','* *'*30)

    result = {}
    if rtype == "local":
        news_to_video_logger.info("[render_video] Dispatch → render_video_local")
        result = render_video_local(project_dir, profile)
        news_to_video_logger.info("[render_video] render_video_local completed; outputs=%s", list(result.keys()) if isinstance(result, dict) else type(result))
        return result
    elif rtype == "shotstack":
        news_to_video_logger.info("[render_video] Dispatch → render_via_shotstack")
        result = render_via_shotstack(project_dir, profile)
        news_to_video_logger.info("[render_video] render_via_shotstack returned status=%s", result.get("status") if isinstance(result, dict) else type(result))
        return result
    elif rtype == "json2video":
        news_to_video_logger.info("[render_video] Dispatch → render_via_json2video")
        result = render_via_json2video(project_dir, profile)

        if result["status"] == "done":
            print("OK:", result["outputs"])
        else:
            print("Błąd:", result.get("error"), result.get("warnings"))
            
        news_to_video_logger.info("[render_video] render_via_json2video returned status=%s", result.get("status"))
        return result
    elif rtype == "mediaconvert":
        news_to_video_logger.info("[render_video] Dispatch → render_via_mediaconvert")
        result = render_via_mediaconvert(project_dir, profile)
        # obsluga zadania json2video powinna byc wywolywana z aplikacji za pomocą funkcji 'render_via_mediaconvert(project_dir, profile)'
        news_to_video_logger.info("[render_video] render_via_mediaconvert returned status=%s", result.get("status") if isinstance(result, dict) else type(result))
        return result
    elif rtype == "openshot":
        news_to_video_logger.info("[render_video] Dispatch → render_via_openshot")
        result = render_via_openshot(project_dir, profile)
        news_to_video_logger.info("[render_video] render_via_openshot returned status=%s", result.get("status") if isinstance(result, dict) else type(result))
        return result
    elif rtype == "openai_sora":
        news_to_video_logger.info("[render_video] Dispatch → render_via_openai_sora")
        result = render_via_openai_sora(project_dir, profile)
        news_to_video_logger.info("[render_video] render_via_openai_sora completed; outputs=%s", list(result.keys()) if isinstance(result, dict) else type(result))
        return result
    else:
        news_to_video_logger.warning("[render_video] Unknown renderer '%s' – falling back to local", rtype)
        result = render_video_local(project_dir, profile)
        # Fallback defensywny
        news_to_video_logger.info("[render_video] Fallback render_video_local completed; outputs=%s", list(result.keys()) if isinstance(result, dict) else type(result))
        return result
# rerender_project → dyspozytor
def rerender_project(project_id: str) -> Optional[Dict]:
    """Ponownie renderuje istniejący projekt na podstawie aktualnego manifestu."""
    pdir = find_project_dir(project_id)
    if not pdir:
        return None
    return render_video(pdir)

# render_video_local and API: integracja napisów, logo, przejść
# [MODIFY] talk_to/news_to_video/main.py — funkcja z logowaniem: render_video_local (podmień całą definicję)
def render_video_local(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict:
    print(f"\n\t\t START ==> render_video_local({project_dir})\n")
    profile = profile or RenderProfile()
    manifest_path = os.path.join(project_dir, "manifest.json")
    manifest = load_json(manifest_path)
    news_to_video_logger.info(f'🔔 2. manifest_path: {manifest_path}\nmanifest.keys(): {manifest.keys()}')

    payload = manifest.get("payload", {})

    # Prefer explicit narration script when provided to keep VO and visuals aligned
    text = payload.get("narration_script") or payload.get("text", "")
    tts_cfg = payload.get("tts", {}) or {}
    tts = TTSSettings(**tts_cfg) if isinstance(tts_cfg, dict) else TTSSettings()
    if not tts.voice:
        raise RuntimeError("Brak skonfigurowanego głosu TTS (payload['tts']['voice']).")

    # Efekty
    trans_cfg = payload.get("transitions") or {}
    transitions = TransitionsConfig(**trans_cfg) if isinstance(trans_cfg, dict) else TransitionsConfig()

    brand_cfg = payload.get("brand") or {}
    branding = BrandConfig(**brand_cfg) if isinstance(brand_cfg, dict) else BrandConfig()

    burn_subs = bool(payload.get("subtitles", {}).get("burn_in", True))

    # FORMATY
    formats = payload.get("formats") or ["16x9"]
    if isinstance(formats, str):
        formats = [formats]
    formats = [f for f in formats if f in FORMAT_PRESETS] or ["16x9"]

    # news_to_video_logger.info(f"[render_video_local] Formats={formats} burn_in={burn_subs} "
    #                           f"xfade={{enable:{transitions.use_xfade}, dur:{transitions.duration}, type:'{transitions.transition}'}} "
    #                           f"branding={{opacity:{branding.opacity}, position:{branding.position}, scale:{branding.scale}, url:{branding.logo_path}}} ")

    # 1) Segmentacja tekstu
    segments = segment_text(text)
    # news_to_video_logger.info(f"[render_video_local] Segmentation done: segments={len(segments)} chars={len(text)}")

    # 2) TTS -> audio + timeline
    audio_dir = os.path.join(project_dir, "audio")
    audio_path, timeline = synthesize_tts(segments, tts, audio_dir)
    audio_duration = _ffprobe_duration(audio_path) or 0.0
    news_to_video_logger.info(f"[render_video_local] TTS synthesized ==> duration:{audio_duration:.2f}s, voice:'{tts.voice}', provider:'{tts.provider}'\n\t {audio_path}")

    # 3) Napisy (SRT + ASS z limitem 5 słów)
    out_dir = os.path.join(project_dir, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    srt_path = os.path.join(out_dir, "captions.srt")
    ass_path = os.path.join(out_dir, "captions.ass")
    generate_srt(timeline, srt_path)
    generate_ass_from_timeline(timeline, profile, ass_path, max_words=5, min_chunk_dur=0.7)
    news_to_video_logger.info(f"[render_video_local] Captions generated ==> srt:{srt_path}, ass:{ass_path}")

    outputs_map: Dict[str, str] = {}
    durations_for_min = []


    # print(json.dumps(payload, ensure_ascii=False, indent=2))
    # print('\n\n\n\t\t\t STOP / PAUZA \n\n\n')
    # exit()

    for fmt in formats:
        fmt_key = fmt.replace(":", "x").replace("/", "x")
        p = profile_for(fmt, profile)

        # 4) Media -> segmenty (bez powielania)
        media_items = [MediaItem(**m) for m in payload.get("media", [])]
        seg_dir = os.path.join(project_dir, f"segments_{fmt_key}")

        # news_to_video_logger.info(f"[render_video_local] ===> Prepare media segments ==> prepare_media_segments({media_items}, {audio_duration}, {p}, {seg_dir})")
        seg_paths, durations, total = prepare_media_segments(media_items, audio_duration, p, seg_dir)
        
        # news_to_video_logger.info(f"[render_video_local] Segments encoded ==> count: {len(seg_paths)} "
        #                           f"total: {total}s, sumDur: {sum(durations):.2f}s")


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

            while eff + 1e-3 < audio_duration and safety_iter < 200:
                if last_dur <= (transitions.duration if transitions.use_xfade else 0.0):
                # if transitions.use_xfade:
                    filler = os.path.join(seg_dir, f"filler_{len(seg_paths)+1:03d}.mp4")
                    print('filler:', filler)

                    _make_color_segment(max(1.0, (transitions.duration or 0.5) + 0.2), p, filler)
                    seg_paths.append(filler)
                    print('seg_paths:', seg_paths)
                    
                    d_new = _ffprobe_duration(filler) or 1.0
                    durations.append(d_new)
                    print('durations:', durations)

                    last_dur = d_new
                    # news_to_video_logger.info(f"[render_video_local] Added filler segment dur={d_new:.2f}s path={filler}")
                else:
                    news_to_video_logger.info(f"[render_video_local] ===> {last_dur} ==> {last_seg}")
                    seg_paths.append(last_seg)
                    durations.append(last_dur)
                    # news_to_video_logger.info(f"[render_video_local] Reused last segment dur={last_dur:.2f}s path={last_seg}")

                eff = _effective_visual_duration(durations, transitions.use_xfade, float(transitions.duration))
                safety_iter += 1

                news_to_video_logger.info(f'[render_video_local] [{eff + 1e-3} < {audio_duration}] ==> while {eff} + 1e-3 < {audio_duration} and {safety_iter} < 200')
            

            visuals_raw = os.path.join(out_dir, f"video_concat_{fmt_key}.mp4")
            if transitions.use_xfade and len(seg_paths) > 1:
                _xfade_concat(seg_paths, durations, visuals_raw,
                              transition=transitions.transition, duration=transitions.duration, profile=p)
                news_to_video_logger.info(f"[render_video_local] Concatenated with xfade -> {visuals_raw}")
            else:
                _concat_videos(seg_paths, visuals_raw)
                news_to_video_logger.info(f"[render_video_local] Concatenated (cut) -> {visuals_raw}")

        # 6) Branding + (opcjonalny) burn-in napisów — użyj ASS (limit 5 słów)
        visuals_fx = visuals_raw
        if branding.logo_path or burn_subs:
            fx_path = os.path.join(out_dir, f"video_visuals_fx_{fmt_key}.mp4")
            applied = _apply_branding_and_subtitles(
                visuals_raw,
                ass_path if burn_subs else None,
                branding if (branding.logo_path or burn_subs) else None,
                p,
                fx_path
            )
            if applied:
                visuals_fx = fx_path
                news_to_video_logger.info(f"[render_video_local] Branding/subtitles applied -> {fx_path} (burn_in={burn_subs})")
            else:
                news_to_video_logger.warning(
                    "[render_video_local] Branding/subtitles failed for %s (burn_in=%s); falling back to raw visuals",
                    fmt_key, burn_subs
                )

        print('\n\n\t\t\* * * * * 👍 START make a video from components * * * * * ')
        print('\t\t\* * * * * 👍 START make a video from components * * * * * ')
        print('\t\t\* * * * * 👍 START make a video from components * * * * * \n\n')
        # 7) Mux (scalanie klipow) audio (bez -shortest; video nie krótsze od audio)
        mp4_path = os.path.join(out_dir, f"output_{fmt_key}.mp4")
        if not _mux_video_audio(visuals_fx, audio_path, p, mp4_path):
            msg = f"mux failed for {fmt_key} (expected {mp4_path})"
            news_to_video_logger.error("[render_video_local] %s", msg)
            raise RuntimeError(msg)
        vdur = _ffprobe_duration(mp4_path) or audio_duration
        durations_for_min.append(vdur)
        news_to_video_logger.info(f"[render_video_local] MUX done -> {mp4_path} duration={vdur:.2f}s")
        # mp4_path = os.path.join(out_dir, f"output_{fmt_key}.mp4")
        # _mux_video_audio(visuals_fx, audio_path, p, mp4_path)
        # vdur = _ffprobe_duration(mp4_path) or audio_duration
        # durations_for_min.append(vdur)
        # news_to_video_logger.info(f"[render_video_local] MUX done -> {mp4_path} duration={vdur:.2f}s")
        news_to_video_logger.info(f"[render_video_local] vdur: {vdur}, durations_for_min: {durations_for_min}")

        # --- S3 upload dla wideo tego formatu ---
        mp4_key = _s3_key_for_local(mp4_path)

        try:
            print(f'audio_path: {audio_path} \nfiller: {filler} \nvisuals_raw: {visuals_raw} \nvisuals_fx: {visuals_fx} \nmp4_path: {mp4_path} \nmp4_key: {mp4_key}')
        except:
            print(f'audio_path: {audio_path} \nvisuals_raw: {visuals_raw} \nvisuals_fx: {visuals_fx} \nmp4_path: {mp4_path} \nmp4_key: {mp4_key}')




        mp4_url = None
        if mp4_key:
            mp4_url = _s3_upload_file(mp4_path, mp4_key, content_type="video/mp4")
            news_to_video_logger.info(f"[render_video_local] Uploaded MP4 to S3 -> {mp4_url}")
            
        if fmt_key == "16x9":
            outputs_map["mp4_16x9"] = mp4_path
            if mp4_url: outputs_map["mp4_16x9_url"] = mp4_url
        elif fmt_key == "1x1":
            outputs_map["mp4_1x1"] = mp4_path
            if mp4_url: outputs_map["mp4_1x1_url"] = mp4_url
        elif fmt_key == "9x16":
            outputs_map["mp4_9x16"] = mp4_path
            if mp4_url: outputs_map["mp4_9x16_url"] = mp4_url
        else:
            outputs_map[f"mp4_{fmt_key}"] = mp4_path
            if mp4_url: outputs_map[f"mp4_{fmt_key}_url"] = mp4_url





    # Upload captions/audio
    srt_url = ass_url = audio_url = None
    for local, ctype, key_name in [(srt_path, "application/x-subrip", "srt_url"),
                                   (ass_path, "text/plain", "ass_url"),
                                   (audio_path, "audio/mpeg", "audio_url")]:
        
        k = _s3_key_for_local(local)
        print(f'[render_video_local] _s3_key_for_local ==> k: {k}\n\t\t{local}, {ctype}, {key_name}')
        if k:
            u = _s3_upload_file(local, k, content_type=ctype)
            print(f'[render_video_local] _s3_upload_file ==> u: {u}\n\t\t{local}, {ctype}')
            if u:
                if key_name == "srt_url": srt_url = u
                elif key_name == "ass_url": ass_url = u
                elif key_name == "audio_url": audio_url = u

    # 8) Manifest
    manifest.setdefault("outputs", {})
    manifest["outputs"].update(outputs_map)
    manifest["outputs"]["srt"] = srt_path
    manifest["outputs"]["ass"] = ass_path
    manifest["outputs"]["audio"] = audio_path
    if srt_url: manifest["outputs"]["srt_url"] = srt_url
    if ass_url: manifest["outputs"]["ass_url"] = ass_url
    if audio_url: manifest["outputs"]["audio_url"] = audio_url
    manifest["outputs"]["duration_sec"] = round(min([audio_duration] + durations_for_min), 2)
    manifest["status"] = "done"

    manifest_snapshot = json.dumps(manifest, ensure_ascii=False, indent=2)
    news_to_video_logger.info(
        f'🔔 3. manifest_path: {manifest_path}\nmanifest.keys(): {manifest.keys()}\n{manifest_snapshot}'
    )
    # save_json(manifest_path, manifest)

    # Walidacja – twarde zatrzymanie przed zapisem, jeśli coś nie gra
    ensure_valid_or_raise(manifest, "render_video_local:before-save")
    save_json(manifest_path, manifest)

    # Upload manifest.json do S3
    # m_key = _s3_key_for_local(manifest_path)
    # if m_key:
    #     print(f'[render_video_local] _s3_upload_file ==> manifest_path: {manifest_path}\n\t\tm_key:{m_key}')
    #     _s3_upload_file(manifest_path, m_key, content_type="application/json")
    # (dodatkowa gardą — jeśli ktoś zmieni kolejność w przyszłości)
    if validate_manifest(manifest):
        m_key = _s3_key_for_local(manifest_path)
        if m_key:
            print(f'[render_video_local] _s3_upload_file ==> manifest_path: {manifest_path}\n\t\tm_key:{m_key}')
            _s3_upload_file(manifest_path, m_key, content_type="application/json")
    else:
        news_to_video_logger.error("[render_video_local] manifest invalid, S3 upload skipped")
 
    news_to_video_logger.info(f"[render_video_local] DONE outputs={list(outputs_map.keys())} "
                              f"minDuration={manifest['outputs']['duration_sec']:.2f}s")

    print('\n\n\n\t\t\t[render_video_local] DONE\n',manifest["outputs"],'\n\n\n')

    return manifest["outputs"]

    # news_to_video_logger.info(f"[render_video_local] DONE outputs={list(outputs_map.keys())} "
    #                           f"minDuration={manifest['outputs']['duration_sec']:.2f}s")
    # return manifest["outputs"]

def render_via_json2video(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict:
    """
    TODO: Implementacja integracji z JSON2Video API.
    """
    raise RuntimeError("Renderer 'json2video' nie jest jeszcze skonfigurowany. Ustaw renderer.type='local' albo skonfiguruj JSON2Video.")

def render_via_mediaconvert(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict:
    """
    TODO: Implementacja pipeline'u: lokalna kompozycja → upload do S3 → transkodowanie wariantów w MediaConvert.
    """
    raise RuntimeError("Renderer 'mediaconvert' nie jest jeszcze skonfigurowany. Ustaw renderer.type='local' albo skonfiguruj MediaConvert (role, queue, S3).")

def render_via_openshot(project_dir: str, profile: Optional[RenderProfile] = None) -> Dict:
    """
    TODO: Implementacja integracji z OpenShot Cloud API (self-hosted).
    """
    raise RuntimeError("Renderer 'openshot' nie jest jeszcze skonfigurowany. Ustaw renderer.type='local' albo skonfiguruj OpenShot Cloud API.")

def _fallback_generate_timeline(project_dir: str, default_duration: float = 3.0) -> Path:
    """
    Minimalny generator timeline.json gdy json2video nie udostępnia build_timeline/generate_timeline/make_timeline.
    Zbiera podstawowe media z katalogu projektu (z pominięciem outputs/.openshot/.build) i układa je sekwencyjnie.
    """
    pdir = Path(project_dir)
    def _skip(p: Path) -> bool:
        parts = set(p.parts)
        return any(skip in parts for skip in ("outputs", ".openshot", ".build"))

    exts = (".mp4", ".mov", ".mkv", ".webm", ".jpg", ".jpeg", ".png")
    media = []
    for ext in exts:
        for p in pdir.rglob(f"*{ext}"):
            if p.is_file() and not _skip(p):
                media.append(p)

    media = sorted(set(media))
    clips, t = [], 0.0
    for m in media:
        is_image = m.suffix.lower() in (".jpg", ".jpeg", ".png")
        start, end = t, t + default_duration  # brak ffprobe → stała długość
        clips.append({
            "type": "image" if is_image else "video",
            "path": str(m),
            "start": start,
            "end": end,
        })
        t = end

    timeline = {"tracks": [{"clips": clips}], "audio": []}
    timeline_path = pdir / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    return timeline_path

def _ensure_timeline_exists(project_dir: str) -> Path:
    print(f'\n\t\tSTART ==> 1. _ensure_timeline_exists({project_dir})')
    pdir = Path(project_dir)
    candidates = [
        pdir / "timeline.json",
        pdir / "outputs" / "timeline.json",
        pdir / ".build" / "timeline.json",
        pdir / "data" / "timeline.json",
    ]
    for c in candidates:
        if c.exists():
            return c

    # print(f'candidates len==>{len(candidates)}')
    # Spróbuj zbudować timeline przez json2video (jeśli dostępne)
    j2v = None
    try:
        # print('\n\n+++++ try import +++++')
        import news_to_video.renders_engines.json2video as j2v
    except Exception as e:
        # print('\n\n------ try import ------')
        print(f'❌ [_ensure_timeline_exists] ==> Err import:{e}')
        j2v = None

    # print(j2v) # <module 'news_to_video.renders_engines.json2video' from '/home/vs/projects/ai/__ops01/news_to_video/renders_engines/json2video.py'>
    if j2v:
        for fn_name in ("build_timeline", "generate_timeline", "make_timeline"):
            
            # print(f'fn = getattr(j2v, {fn_name}, None)')
            fn = getattr(j2v, fn_name, None)
            if callable(fn):
                print(f'[_ensure_timeline_exists] {callable(fn)} fn => getattr(j2v, {fn_name}, None)')
                fn(project_dir)
                # re-check
                for c in candidates:
                    if c.exists():
                        return c
                break

    # raise FileNotFoundError(f"❌ Missing timeline.json in {project_dir}")
    return _fallback_generate_timeline(project_dir)

def _run_render_job(project_dir: str):
    print(f'\n\t\tSTART ==> _run_render_job({project_dir})')
    """Wątek roboczy: ustawia statusy i wywołuje render_video()."""
    from pathlib import Path

    try:
        project_dir = Path(project_dir)
        _ensure_timeline_exists(project_dir)

        # Ustaw 'processing' i normalizuj manifest do dict
        um_ret = update_manifest(project_dir, {"status": "processing", "error": None})
        if isinstance(um_ret, dict):
            manifest = um_ret
        else:
            # update_manifest zwrócił np. ścieżkę/str → wczytaj z pliku
            manifest = load_json(project_dir / "manifest.json") or {}

        project_id = manifest.get("project_id") or project_dir.name
        news_to_video_logger.info("[_run_render_job] processing started for %s", project_id)

        # Jeden call wystarczy (wcześniej były dwa)
        outputs = render_video(project_dir) or {}

        update_manifest(project_dir, {"status": "done", "outputs": outputs})
        news_to_video_logger.info("[_run_render_job] processing done for %s", project_id)

    except Exception as e:
        news_to_video_logger.error("❌ [_run_render_job] processing ERROR for %s: %s", project_dir, e)
        try:
            update_manifest(project_dir, {"status": "error", "error": str(e)})
        except Exception:
            pass

def start_render_async(project_dir: str) -> str:
    print(f'\n\t\tSTART ==> ☠️ start_render_async({project_dir})\n')
    """Kolejkuje render asynchronicznie i zwraca project_id (job_id)."""
    manifest_path = os.path.join(project_dir, "manifest.json")
    m = load_json(manifest_path)
    # news_to_video_logger.info(f'[start_render_async] manifest:\n{m}')
    project_id = m.get("project_id") or os.path.basename(project_dir)

    # ustaw 'queued' i zapisz
    update_manifest(project_dir, {"status": "queued"})
    # mm = load_json(manifest_path)
    # status = m.get('status')
    # news_to_video_logger.info(f'[start_render_async] m.get("status")==>{status}\nmanifest manifest manifest manifest manifest\n{m}')

    fut = _EXECUTOR.submit(_run_render_job, project_dir)
    _ACTIVE_JOBS[project_id] = fut
    # news_to_video_logger.info("[start_render_async] queued project_id=%s", project_id)
    return project_id
