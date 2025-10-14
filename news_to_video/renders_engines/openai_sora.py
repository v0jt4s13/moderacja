"""
OpenAI Sora renderer integration for the News → Video pipeline.

This module delegates video generation to OpenAI's `/videos` endpoint (Sora models).
It composes the prompt from the article content stored in the project manifest and
optionally sends a reference image to guide the generation.
"""
from __future__ import annotations

import os
import re
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests
try:  # Optional, used to adapt reference image to requested size
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore
from openai import OpenAI, OpenAIError

from loggers import news_to_video_logger
from news_to_video.renders_engines.s3_proc import load_json, save_json
from news_to_video.main import (
    update_manifest,
    _s3_key_for_local,
    _s3_upload_file,
    segment_text,
    synthesize_tts,
    TTSSettings,
    RenderProfile,
    _mux_video_audio,
)


DEFAULT_PROMPT_TEMPLATE = textwrap.dedent(
    """\
    Create a concise Polish-language news explainer video based on the article "{title}".
    Summarise the key facts so the visuals can guide viewers even without audio.
    Reference points:
    - Summary: {summary}
    - Key facts:\n{key_points}
    Maintain a professional news tone, dynamic camera movement and readable on-screen focal points.
    Include subtle branding placeholders for londynek.net.
    Article source: {article_url}
    """
)

ALLOWED_MODELS = {"sora-2", "sora-2-pro"}
ALLOWED_SECONDS = {"4", "8", "12"}
ALLOWED_SIZES = {"1280x720", "720x1280", "1792x1024", "1024x1792", "1024x1024"}


def _shorten_text(text: str, max_chars: int = 900) -> str:
    body = (text or "").strip()
    if len(body) <= max_chars:
        return body
    clipped = body[: max_chars - 1].rsplit(" ", 1)[0]
    return clipped.rstrip() + "…"


def _extract_key_points(text: str, limit: int = 3) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    selected = sentences[:limit]
    if not selected and text:
        selected = [_shorten_text(text, 120)]
    if not selected:
        return "  • brak danych"
    return "\n".join(f"  • {s}" for s in selected)


def _build_prompt(manifest: Dict[str, Any], config: Dict[str, Any]) -> str:
    payload = manifest.get("payload") or {}
    template = (config.get("prompt_template") or DEFAULT_PROMPT_TEMPLATE).strip()

    summary = _shorten_text(payload.get("text", ""))
    prompt_context = {
        "title": payload.get("title") or manifest.get("title") or "",
        "summary": summary,
        "key_points": _extract_key_points(payload.get("text", "")),
        "article_url": manifest.get("source_url") or payload.get("source_url") or "",
    }

    try:
        base_prompt = template.format(**prompt_context).strip()
    except KeyError as exc:
        available = ", ".join(sorted(prompt_context.keys()))
        raise RuntimeError(
            f"Prompt template contains unknown placeholder {exc}. "
            f"Available placeholders: {available}"
        ) from exc

    style_notes = (config.get("style_notes") or "").strip()
    extra = (config.get("extra_instructions") or "").strip()
    avoid = (config.get("avoid_list") or "").strip()

    additions = []
    if style_notes:
        additions.append(f"Desired visual style: {style_notes}")
    if extra:
        additions.append(extra)
    if avoid:
        additions.append(f"Avoid showing: {avoid}")

    if additions:
        base_prompt = base_prompt + "\n\n" + "\n".join(additions)

    return base_prompt


def _pick_reference_image(config: Dict[str, Any], payload: Dict[str, Any]) -> Optional[str]:
    manual_url = (config.get("reference_image_url") or "").strip()
    if manual_url:
        return manual_url

    if not config.get("use_article_cover"):
        return None

    media = payload.get("media") or []
    for item in media:
        if isinstance(item, dict) and item.get("type") == "image" and item.get("src"):
            return str(item["src"])
    return None


def _download_reference_image(url: str) -> Tuple[Optional[str], Optional[Any]]:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:  # pragma: no cover - network errors handled at runtime
        news_to_video_logger.warning("OpenAI Sora: failed to download reference image %s: %s", url, exc)
        return None, None

    suffix = Path(urlparse(url).path).suffix or ".jpg"
    fd, tmp_path = tempfile.mkstemp(prefix="sora-ref-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(resp.content)
        return tmp_path, open(tmp_path, "rb")
    except Exception as exc:  # pragma: no cover
        news_to_video_logger.warning("OpenAI Sora: failed to prepare reference file %s: %s", url, exc)
        try:
            os.close(fd)
        except Exception:
            pass
        return None, None


def _ensure_reference_size(local_path: str, requested_size: str, mode: str = "letterbox") -> Tuple[str, Any]:
    """Ensure the reference image matches requested_size (e.g., '1280x720').
    If Pillow is available and image size differs, create a letterboxed image
    with exact target dims and return a new temp file path and open handle.
    Falls back to the original file when Pillow is missing or errors occur.
    """
    try:
        if not Image:
            return local_path, open(local_path, "rb")
        parts = requested_size.lower().split("x")
        if len(parts) != 2:
            return local_path, open(local_path, "rb")
        tw, th = int(parts[0]), int(parts[1])
        with Image.open(local_path) as im:
            w, h = im.size
            if w == tw and h == th:
                return local_path, open(local_path, "rb")
            im_conv = im.convert("RGB")
            if (mode or "letterbox").lower() == "crop":
                # Scale to cover (no bars) then center-crop
                scale = max(tw / w, th / h)
                nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
                resized = im_conv.resize((nw, nh), Image.LANCZOS)
                # crop center
                left = max(0, (nw - tw) // 2)
                top = max(0, (nh - th) // 2)
                right = left + tw
                bottom = top + th
                canvas = resized.crop((left, top, right, bottom))
            else:
                # Letterbox to target while preserving aspect ratio
                scale = min(tw / w, th / h)
                nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
                resized = im_conv.resize((nw, nh), Image.LANCZOS)
                canvas = Image.new("RGB", (tw, th), (0, 0, 0))
                ox, oy = (tw - nw) // 2, (th - nh) // 2
                canvas.paste(resized, (ox, oy))
            fd, tmp_out = tempfile.mkstemp(prefix="sora-ref-fit-", suffix=".jpg")
            with os.fdopen(fd, "wb") as fh:
                canvas.save(fh, format="JPEG", quality=90)
            news_to_video_logger.info(
                "[openai_sora] Adjusted reference image %s from %sx%s to %sx%s -> %s",
                (mode or "letterbox").lower(), w, h, tw, th, tmp_out,
            )
            return tmp_out, open(tmp_out, "rb")
    except Exception as exc:  # pragma: no cover
        news_to_video_logger.warning("OpenAI Sora: reference resize failed: %s", exc)
    return local_path, open(local_path, "rb")


def _manifest_outputs_patch(video_path: Path, thumbnail_path: Optional[Path], video_meta: Dict[str, Any]) -> Dict[str, Any]:
    outputs = {
        "openai_sora_video": str(video_path),
        "openai_sora_meta": video_meta,
    }
    if thumbnail_path:
        outputs["openai_sora_thumbnail"] = str(thumbnail_path)
    return outputs


def render_via_openai_sora(project_dir: str, profile: Optional[Any] = None) -> Dict[str, Any]:
    """
    Generate video via OpenAI Sora and return outputs map merged into manifest.
    """
    news_to_video_logger.info("[openai_sora] START project_dir=%s", project_dir)
    project_path = Path(project_dir)
    manifest_path = project_path / "manifest.json"
    manifest = load_json(manifest_path) or {}
    payload = manifest.get("payload") or {}
    renderer_cfg = (payload.get("renderer") or {}).get("config") or {}

    api_key = (renderer_cfg.get("api_key") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Renderer 'openai_sora' requires an OpenAI API key (config.api_key or OPENAI_API_KEY).")

    model = str(renderer_cfg.get("model") or "sora-2").lower()
    if model not in ALLOWED_MODELS:
        news_to_video_logger.warning("OpenAI Sora: unsupported model '%s', falling back to 'sora-2'.", model)
        model = "sora-2"

    seconds = str(renderer_cfg.get("seconds") or "8")
    if seconds not in ALLOWED_SECONDS:
        news_to_video_logger.warning("OpenAI Sora: unsupported duration '%s', defaulting to 8s.", seconds)
        seconds = "8"

    size = str(renderer_cfg.get("size") or "1280x720")
    if size not in ALLOWED_SIZES:
        news_to_video_logger.warning("OpenAI Sora: unsupported size '%s' (allowed: %s)", size, sorted(ALLOWED_SIZES))
        # pick a sane default later based on reference image/orientation
        size = None  # type: ignore

    prompt = _build_prompt(manifest, renderer_cfg)
    news_to_video_logger.info(
        "[openai_sora] Prompt prepared (len=%d)\n%s",
        len(prompt),
        prompt if len(prompt) <= 800 else prompt[:800] + "…"
    )

    input_reference_url = _pick_reference_image(renderer_cfg, payload)
    if input_reference_url:
        news_to_video_logger.info("[openai_sora] Using reference image: %s", input_reference_url)
    else:
        news_to_video_logger.info("[openai_sora] No reference image selected.")

    tmp_path = None
    input_file = None
    if input_reference_url:
        tmp_path, input_file = _download_reference_image(input_reference_url)
        # If we have a file and a requested size, make sure the image matches
        if tmp_path and input_file:
            try:
                # Close initial handle before potential replacement
                try:
                    input_file.close()
                except Exception:
                    pass
                # If size is None or 'match_image', deduce target size from image orientation
                fit_mode = str(renderer_cfg.get("ref_image_fit") or "letterbox").lower()
                try:
                    iw = ih = None
                    if Image:
                        with Image.open(tmp_path) as im:
                            iw, ih = im.size
                    if (not size) or (fit_mode == "match_image"):
                        # choose best allowed based on orientation / near-square
                        if iw and ih:
                            ratio = iw/ih
                            if 0.95 <= ratio <= 1.05 and "1024x1024" in ALLOWED_SIZES:
                                size = "1024x1024"
                            elif ratio >= 1:
                                size = "1280x720" if "1280x720" in ALLOWED_SIZES else next(iter(ALLOWED_SIZES))
                            else:
                                size = "720x1280" if "720x1280" in ALLOWED_SIZES else next(iter(ALLOWED_SIZES))
                        else:
                            size = size or "1280x720"
                except Exception:
                    size = size or "1280x720"
                # ensure the reference image matches chosen size
                fitted_path, fitted_file = _ensure_reference_size(tmp_path, size, fit_mode)
                # If a new file was produced, prefer it; keep original for cleanup
                input_file = fitted_file
                # If a new temp was created different than tmp_path, keep both for cleanup
                if fitted_path != tmp_path:
                    # Track additional temp for removal after request
                    # We'll store it in tmp_path2 local var
                    tmp_path2 = fitted_path
                else:
                    tmp_path2 = None
            except Exception as exc:
                news_to_video_logger.warning("OpenAI Sora: reference preflight skipped: %s", exc)
                tmp_path2 = None

    client = OpenAI(api_key=api_key)
    try:
        # final guard on size
        size = size or "1280x720"
        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "model": model,
            "seconds": seconds,
            "size": size,
        }
        if input_file:
            kwargs["input_reference"] = input_file

        payload_preview = {
            "model": model,
            "seconds": seconds,
            "size": size,
            "prompt_preview": prompt if len(prompt) <= 400 else prompt[:400] + "…",
            "has_reference": bool(input_file),
        }
        news_to_video_logger.info("[openai_sora] API request payload => %s", payload_preview)
        # Prepare log-friendly payload preview (without file handles)
        video_job = client.videos.create_and_poll(**kwargs)
    except OpenAIError as exc:
        raise RuntimeError(f"OpenAI Sora generation error: {exc}") from exc
    finally:
        if input_file:
            try:
                input_file.close()
            except Exception:
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        # remove possible second tmp from fitted image
        try:
            if 'tmp_path2' in locals() and tmp_path2:
                os.unlink(tmp_path2)
        except Exception:
            pass

    if video_job.status != "completed":
        raise RuntimeError(f"OpenAI Sora job did not complete successfully (status={video_job.status}).")
    news_to_video_logger.info("[openai_sora] Job completed id=%s status=%s", video_job.id, video_job.status)

    outputs_dir = project_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    video_filename = f"{manifest.get('project_id') or project_path.name}_sora_{video_job.size}_{video_job.seconds}s.mp4"
    video_path = outputs_dir / video_filename

    binary = client.videos.download_content(video_job.id, variant="video")
    binary.write_to_file(video_path)
    news_to_video_logger.info("[openai_sora] Video downloaded -> %s", video_path)

    final_video_path = video_path
    raw_video_path = None

    # --- Narration (TTS) + subtitles ---
    narration_path = None
    srt_path = None
    ass_path = None
    try:
        text = (payload.get("text") or "").strip()
        tts_cfg = payload.get("tts") or {}
        if text and isinstance(tts_cfg, dict) and tts_cfg.get("voice"):
            segments = segment_text(text)
            if segments:
                tts_settings = TTSSettings(**tts_cfg)
                audio_dir = project_path / "audio"
                audio_dir.mkdir(parents=True, exist_ok=True)
                narration_path, timeline = synthesize_tts(segments, tts_settings, str(audio_dir))
                news_to_video_logger.info("[openai_sora] Narration synthesized -> %s", narration_path)
                # Save full timeline to project_dir/timeline.json
                try:
                    save_json(project_path / "timeline.json", {"timeline": timeline})
                except Exception as exc_tl:
                    news_to_video_logger.warning("OpenAI Sora: failed to save timeline.json: %s", exc_tl)

                # opcjonalne napisy (SRT/ASS)
                try:
                    from news_to_video.main import generate_srt, generate_ass_from_timeline  # lazy import to avoid cycles
                    profile_dims = RenderProfile()
                    try:
                        width, height = map(int, (video_job.size or "1280x720").lower().split("x"))
                        profile_dims.width = width
                        profile_dims.height = height
                    except Exception:
                        pass
                    srt_path = outputs_dir / f"{video_path.stem}.srt"
                    ass_path = outputs_dir / f"{video_path.stem}.ass"
                    generate_srt(timeline, str(srt_path))
                    generate_ass_from_timeline(timeline, profile_dims, str(ass_path), max_words=5, min_chunk_dur=0.7)
                    news_to_video_logger.info("[openai_sora] Captions generated -> %s ; %s", srt_path, ass_path)
                except Exception as exc_sub:
                    news_to_video_logger.warning("OpenAI Sora: captions generation failed: %s", exc_sub)
                    srt_path = None
                    ass_path = None

                # mux audio + video
                try:
                    mux_profile = RenderProfile()
                    try:
                        width, height = map(int, (video_job.size or "1280x720").lower().split("x"))
                        mux_profile.width = width
                        mux_profile.height = height
                    except Exception:
                        pass
                    muxed_path = outputs_dir / f"{video_path.stem}_with_audio.mp4"
                    if _mux_video_audio(str(video_path), narration_path, mux_profile, str(muxed_path)):
                        raw_video_path = video_path
                        final_video_path = muxed_path
                        news_to_video_logger.info("[openai_sora] Video muxed with narration -> %s", muxed_path)
                    else:
                        news_to_video_logger.warning("[openai_sora] Failed to mux narration; using raw Sora video.")
                except Exception as exc_mux:
                    news_to_video_logger.warning("OpenAI Sora: mux failed: %s", exc_mux)
                    raw_video_path = None
                    final_video_path = video_path
    except Exception as exc_tts:
        news_to_video_logger.warning("OpenAI Sora: narration pipeline skipped due to error: %s", exc_tts)
        narration_path = None
        srt_path = None
        ass_path = None

    thumbnail_path = None
    if renderer_cfg.get("save_thumbnail"):
        try:
            thumb_resp = client.videos.download_content(video_job.id, variant="thumbnail")
            thumb_path = outputs_dir / (video_path.stem + "_thumbnail.jpg")
            thumb_resp.write_to_file(thumb_path)
            thumbnail_path = thumb_path
            news_to_video_logger.info("[openai_sora] Thumbnail saved -> %s", thumbnail_path)
        except Exception as exc:  # pragma: no cover
            news_to_video_logger.warning("OpenAI Sora: failed to download thumbnail for %s: %s", video_job.id, exc)

    video_meta = {
        "id": video_job.id,
        "model": video_job.model,
        "seconds": video_job.seconds,
        "size": video_job.size,
        "reference_image_url": input_reference_url,
    }

    outputs_patch = _manifest_outputs_patch(final_video_path, thumbnail_path, video_meta)
    # Save prompt and renderer config used for reproducibility
    try:
        outputs_patch["openai_sora_prompt"] = prompt
        outputs_patch["openai_sora_config"] = {
            "model": model,
            "seconds": seconds,
            "size": size,
            "ref_image_fit": (renderer_cfg.get("ref_image_fit") or "letterbox"),
            "reference_image_url": input_reference_url,
        }
        # Persist minimal API request preview
        outputs_patch["openai_sora_request"] = {
            "model": video_job.model,
            "seconds": video_job.seconds,
            "size": video_job.size,
            "has_reference": bool(input_reference_url),
            "prompt_preview": prompt if len(prompt) <= 400 else (prompt[:400] + "…"),
        }
    except Exception:
        pass
    if raw_video_path and raw_video_path != final_video_path:
        outputs_patch["openai_sora_video_raw"] = str(raw_video_path)
    if narration_path:
        outputs_patch["openai_sora_audio"] = str(narration_path)
    if srt_path:
        outputs_patch["openai_sora_srt"] = str(srt_path)
    if ass_path:
        outputs_patch["openai_sora_ass"] = str(ass_path)

    # map to standard keys (mp4_16x9/mp4_9x16/...)
    try:
        width, height = map(int, (video_job.size or "1280x720").split("x"))
    except Exception:
        width, height = 1280, 720
    aspect_key = None
    if width >= height:
        if abs((width / height) - (16 / 9)) < 0.08:
            aspect_key = "mp4_16x9"
        elif abs((width / height) - 1) < 0.08:
            aspect_key = "mp4_1x1"
    else:
        if abs((height / width) - (16 / 9)) < 0.08:
            aspect_key = "mp4_9x16"
    if aspect_key:
        outputs_patch[aspect_key] = str(final_video_path)

    # Mirror to S3 if configured
    upload_candidates = []
    upload_candidates.append((final_video_path, "video/mp4", "video_url"))
    if raw_video_path and raw_video_path != final_video_path:
        upload_candidates.append((raw_video_path, "video/mp4", "raw_video_url"))
    if narration_path:
        upload_candidates.append((Path(narration_path), "audio/mpeg", "audio_url"))
    if srt_path:
        upload_candidates.append((Path(srt_path), "application/x-subrip", "srt_url"))
    if ass_path:
        upload_candidates.append((Path(ass_path), "text/plain", "ass_url"))
    if thumbnail_path:
        upload_candidates.append((thumbnail_path, "image/jpeg", "thumbnail_url"))

    for local_path, ctype, label in upload_candidates:
        if not local_path:
            continue
        key = _s3_key_for_local(str(local_path))
        if not key:
            continue
        url = _s3_upload_file(str(local_path), key, content_type=ctype)
        if url:
            news_to_video_logger.info("[openai_sora] Uploaded %s to S3 (key=%s)", local_path, key)
            if label == "video_url":
                if aspect_key:
                    outputs_patch[f"{aspect_key}_url"] = url
                outputs_patch["openai_sora_video_url"] = url
            elif label == "raw_video_url":
                outputs_patch["openai_sora_video_raw_url"] = url
            elif label == "audio_url":
                outputs_patch["openai_sora_audio_url"] = url
            elif label == "srt_url":
                outputs_patch["openai_sora_srt_url"] = url
            elif label == "ass_url":
                outputs_patch["openai_sora_ass_url"] = url
            elif label == "thumbnail_url":
                outputs_patch["openai_sora_thumbnail_url"] = url

    # Sync manifest outputs (z URL) i zapisz lokalnie
    manifest.setdefault("outputs", {})
    manifest["outputs"].update(outputs_patch)
    save_json(manifest_path, manifest)
    final_outputs = dict(manifest["outputs"])
    news_to_video_logger.info("[openai_sora] Outputs prepared: %s", list(final_outputs.keys()))

    # mirror / aktualizacja statusu
    update_manifest(project_dir, {"outputs": final_outputs})
    news_to_video_logger.info("[openai_sora] DONE project_dir=%s", project_dir)

    return final_outputs
