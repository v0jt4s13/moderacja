"""OpenShot provider for the News-to-Video pipeline.

This module follows the same provider methodology as the other renderers in the
project (e.g., json2video.py, mediaconvert.py). It exposes a small, uniform API
used by the orchestration layer:

- provider_name() -> str
- supports(config: dict) -> bool
- prepare(config: dict, workdir: str) -> dict
- start(config: dict, workdir: str) -> dict
- status(config: dict, workdir: str) -> dict
- collect_outputs(config: dict, workdir: str) -> dict
- cancel(config: dict, workdir: str) -> dict

Additionally, a helper function is provided to directly render an existing
json2video job directory via OpenShot:
- render_via_openshot(project_dir: str, profile: dict) -> dict

The OpenShot provider generates an .osp project file from a high-level timeline
(spec defined by our pipeline) and, when libopenshot (Python bindings) is
available, performs a headless export to MP4. If libopenshot is not available on
this system, the provider still creates a valid .osp file which can be rendered
manually or on a machine that has OpenShot installed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import traceback

try:
    import openshot  # type: ignore  # libopenshot Python bindings
    _LIB_OPENSHOT = True
except Exception:
    _LIB_OPENSHOT = False


# --------------------------- Public Provider API --------------------------------

def provider_name() -> str:
    return "openshot"


def supports(config: Dict[str, Any]) -> bool:
    engine = (config.get("engine") or config.get("provider") or "").lower()
    return engine == "openshot"

def prepare(config: Dict[str, Any], workdir: str) -> Dict[str, Any]:
    print(f'\n\t\tSTART ===> prepare({type(config)}, {type(workdir)})')

    job = _OpenShotJob.from_config(config, workdir)
    job.ensure_dirs()
    job.write_state({"status": "preparing", "message": "Generating OSP project"})

    try:
        project_path = job.generate_osp()
    except Exception as e:
        err = f"generate_osp failed: {e}"
        # pełny traceback do logów
        tb = traceback.format_exc()
        print("❌ [prepare]", err, "\n", tb)
        state = {
            "status": "error",
            "pid": job.pid,
            "error": str(e),
            "message": "Failed to generate OpenShot project (.osp)",
        }
        print(state)
        job.write_state(state)
        return state  # ← nie używamy project_path i kończymy

    state = {
        "status": "prepared",
        "pid": job.pid,
        "project_path": str(project_path),
        "libopenshot_available": _LIB_OPENSHOT,
        "message": (
            "Project created. libopenshot available, ready to render."
            if _LIB_OPENSHOT else
            "Project created. libopenshot NOT available: manual render required on a machine with OpenShot."
        ),
    }
    job.write_state(state)
    return state

def prepare_depr(config: Dict[str, Any], workdir: str) -> Dict[str, Any]:
    print(f'\n\t\tSTART ===> prepare({type(config)}, {type(workdir)})')

    job = _OpenShotJob.from_config(config, workdir)
    job.ensure_dirs()
    job.write_state({"status": "preparing", "message": "Generating OSP project"})
    project_path = job.generate_osp()
    state = {
        "status": "prepared",
        "pid": job.pid,
        "project_path": str(project_path),
        "libopenshot_available": _LIB_OPENSHOT,
        "message": (
            "Project created. libopenshot available, ready to render." if _LIB_OPENSHOT
            else "Project created. libopenshot NOT available: manual render required on a machine with OpenShot."
        ),
    }
    job.write_state(state)
    return state


def start(config: Dict[str, Any], workdir: str) -> Dict[str, Any]:
    job = _OpenShotJob.from_config(config, workdir)
    job.ensure_dirs()

    if not job.project_path.exists():
        if job.timeline:
            job.generate_osp()
        else:
            # Brak projektu i brak timeline -> zamiast milcząco iść dalej, zgłoś błąd
            state = job.read_state()
            state.update({"status": "error", "error": "No project. Call prepare() first or provide timeline in config."})
            job.write_state(state)
            return state
        
    if not _LIB_OPENSHOT:
        state = job.read_state()
        state.update({
            "status": state.get("status") or "prepared",
            "message": (
                "libopenshot not installed. Open the generated .osp in OpenShot Desktop and export manually."
            ),
        })
        job.write_state(state)
        return state
    export_path = job.export_output_path()
    job.write_state({
        "status": "rendering",
        "pid": job.pid,
        "project_path": str(job.project_path),
        "output": str(export_path),
        "progress": 0,
        "message": "Rendering via libopenshot",
    })
    try:
        _render_with_libopenshot(job.project_path, export_path, job)
        outs = job.list_outputs()
        state = job.read_state()
        state.update({"status": "done", "outputs": outs, "message": "Render complete"})
        job.write_state(state)
        return state
    except Exception as e:
        state = job.read_state()
        state.update({"status": "error", "error": str(e)})
        job.write_state(state)
        return state


def status(config: Dict[str, Any], workdir: str) -> Dict[str, Any]:
    job = _OpenShotJob.from_config(config, workdir)
    job.ensure_dirs()
    state = job.read_state()
    outputs = job.list_outputs()
    if outputs:
        state.setdefault("outputs", outputs)
    return state


def collect_outputs(config: Dict[str, Any], workdir: str) -> Dict[str, Any]:
    job = _OpenShotJob.from_config(config, workdir)
    outs = job.list_outputs()
    state = job.read_state()
    state.update({"outputs": outs})
    job.write_state(state)
    return {"pid": job.pid, "outputs": outs}


def cancel(config: Dict[str, Any], workdir: str) -> Dict[str, Any]:
    job = _OpenShotJob.from_config(config, workdir)
    state = job.read_state()
    state.update({"status": "canceled", "message": "Canceled by user"})
    job.write_state(state)
    return state

# --------------------------- Internal Job Model ---------------------------------

@dataclass
class _OpenShotJob:
    workdir: Path
    pid: str
    project_path: Path
    export: Dict[str, Any] = field(default_factory=dict)
    timeline: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Dict[str, Any], workdir: str) -> "_OpenShotJob":
        pid = str(config.get("pid") or uuid.uuid4())
        job_dir = Path(workdir) / str(pid)
        project_path = job_dir / "project.osp"
        export = config.get("export") or {}
        timeline = config.get("timeline") or {}
        return cls(workdir=job_dir, pid=str(pid), project_path=project_path, export=export, timeline=timeline)

    def ensure_dirs(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)

    @property
    def state_path(self) -> Path:
        return self.workdir / "state.json"

    def write_state(self, data: Dict[str, Any]) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"status": "unknown", "pid": self.pid}
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def generate_osp(self) -> Path:
        osp = _timeline_to_osp(self.timeline, self.export)
        with open(self.project_path, "w", encoding="utf-8") as f:
            json.dump(osp, f, ensure_ascii=False, indent=2)
        return self.project_path

    def export_output_path(self) -> Path:
        out_name = self.export.get("filename") or f"output_{self.pid}.mp4"
        return self.workdir / out_name

    def list_outputs(self) -> Dict[str, str]:
        outs: Dict[str, str] = {}
        for p in sorted(self.workdir.glob("*.mp4")):
            key = "mp4_{}x{}".format(self.export.get("width", "?"), self.export.get("height", "?"))
            outs[key] = str(p)
        if self.project_path.exists():
            outs["openshot_project"] = str(self.project_path)
        return outs


# --------------------------- OSP Mapping & Render -------------------------------

_DEF_PROFILE = {"fps_num": 30, "fps_den": 1, "width": 1920, "height": 1080, "sample_rate": 48000, "channels": 2}

def _timeline_to_osp(tl: Dict[str, Any], export: Dict[str, Any]) -> Dict[str, Any]:
    """Translate our timeline spec into a minimal OpenShot project structure.

    Also accepts the Shotstack/Local-style schema used in this project. If keys
    like `timelines` or `tracks[0].clips[*].transition` are present, we attempt a
    best-effort adaptation (see `_adapt_from_portal_timeline`).
    """
    # If this looks like the portal's timeline format, adapt first
    if _looks_like_portal_timeline(tl):
        tl = _adapt_from_portal_timeline(tl, export)

    prof = {
        "fps_num": int(export.get("fps", _DEF_PROFILE["fps_num"])),
        "fps_den": 1,
        "width": int(export.get("width", _DEF_PROFILE["width"])),
        "height": int(export.get("height", _DEF_PROFILE["height"])),
        "sample_rate": int(export.get("sample_rate", _DEF_PROFILE["sample_rate"])),
        "channels": int(export.get("channels", _DEF_PROFILE["channels"])),
    }

    project = {
        "files": [], "clips": [], "tracks": [], "effects": [],
        "export_path": "",
        "profile": {"fps": f"{prof['fps_num']}/{prof['fps_den']}", "width": prof["width"], "height": prof["height"]},
        "meta": {"generator": "news-to-video.openshot-provider", "generated_at": int(time.time()), "notes": []},
    }

    file_index = {}
    def add_note(msg: str) -> None: project["meta"]["notes"].append(msg)
    def add_file(path: str) -> str:
        p = os.path.abspath(path)
        if p in file_index: return file_index[p]
        file_id = str(uuid.uuid4())
        project["files"].append({"id": file_id, "path": p, "media_type": _guess_media_type(p)})
        file_index[p] = file_id
        return file_id

    # Map video/image clips
    for t_idx, t in enumerate((tl.get("tracks") or [])):
        track_id = str(uuid.uuid4())
        project["tracks"].append({"id": track_id, "number": t_idx})
        for clip in (t.get("clips") or []):
            path = clip.get("path")
            if not path:
                if clip.get("type") in {"text", "title"}:
                    add_note(f"Text clip kept as meta: {clip.get('text')!r} at {clip.get('start')}s")
                continue
            fid = add_file(path)
            c_id = str(uuid.uuid4())
            c_dict = {
                "id": c_id, "file_id": fid, "track": track_id,
                "start": float(clip.get("start", 0.0)),
                "end": float(clip.get("end", max(0.0, float(clip.get("start", 0.0)) + 0.1))),
                "position": float(clip.get("start", 0.0)),
                "x": clip.get("x", 0), "y": clip.get("y", 0), "scale": clip.get("scale", 1.0),
            }
            tr = clip.get("transition") or {}
            if isinstance(tr, dict):
                _apply_simple_fades(c_dict, tr, add_note)
            project["clips"].append(c_dict)

    # Map audio
    if tl.get("audio"):
        a_track_id = str(uuid.uuid4())
        project["tracks"].append({"id": a_track_id, "number": len(project["tracks"])})
        for a in (tl.get("audio") or []):
            apath = a.get("path")
            if not apath: continue
            fid = add_file(apath)
            project["clips"].append({
                "id": str(uuid.uuid4()), "file_id": fid, "track": a_track_id,
                "start": float(a.get("start", 0.0)),
                "end": float(a.get("end", max(0.0, float(a.get("start", 0.0)) + 0.1))),
                "position": float(a.get("start", 0.0)),
            })

    project["export_path"] = export.get("filename") or f"output_{uuid.uuid4().hex}.mp4"
    return project

def _timeline_to_osp_depr(timeline: Dict[str, Any], export: Dict[str, Any]) -> Dict[str, Any]:
    """Translate our timeline spec into a minimal OpenShot project structure.

    Also accepts the Shotstack/Local-style schema used in this project. If keys
    like `timelines` or `tracks[0].clips[*].transition` are present, we attempt a
    best-effort adaptation (see `_adapt_from_portal_timeline`).
    """
    # If this looks like the portal's timeline format, adapt first
    if _looks_like_portal_timeline(timeline):
        timeline = _adapt_from_portal_timeline(timeline, export)

    prof = {
        "fps_num": int(export.get("fps", _DEF_PROFILE["fps_num"])),
        "fps_den": 1,
        "width": int(export.get("width", _DEF_PROFILE["width"])),
        "height": int(export.get("height", _DEF_PROFILE["height"])),
        "sample_rate": int(export.get("sample_rate", _DEF_PROFILE["sample_rate"])),
        "channels": int(export.get("channels", _DEF_PROFILE["channels"])),
    }

    # OpenShot OSP skeleton
    project = {
        "files": [],
        "clips": [],
        "tracks": [],
        "effects": [],
        "export_path": "",
        "profile": {
            "fps": f"{prof['fps_num']}/{prof['fps_den']}",
            "width": prof["width"],
            "height": prof["height"]
        },
        "meta": {
            "generator": "news-to-video.openshot-provider",
            "generated_at": int(time.time()),
            "notes": [],
        },
    }

    file_index = {}

    def add_note(msg: str) -> None:
        project["meta"]["notes"].append(msg)

    def add_file(path: str) -> str:
        p = os.path.abspath(path)
        if p in file_index:
            return file_index[p]
        
        file_id = 0
        project["files"].append({
            "id": file_id,
            "path": p,
            "media_type": _guess_media_type(p),
        })
        file_index[p] = file_id
        return file_id

    # Map video/image clips
    for t_idx, t in enumerate(timeline.get("tracks", [])):
        track_id = str(uuid.uuid4())
        project["tracks"].append({"id": track_id, "number": t_idx})
        for clip in t.get("clips", []):
            path = clip.get("path")
            if not path:
                # Text overlays or generated elements may not have a path.
                # We keep them as meta notes for now until text rendering impl is agreed.
                if clip.get("type") in {"text", "title"}:
                    add_note(f"Text clip kept as meta: {clip.get('text')!r} at {clip.get('start')}s")
                continue
            fid = add_file(path)
            c_id = str(uuid.uuid4())
            c_dict = {
                "id": c_id,
                "file_id": fid,
                "track": track_id,
                "start": float(clip.get("start", 0.0)),
                "end": float(clip.get("end", max(0.0, float(clip.get("start", 0.0)) + 0.1))),
                "position": float(clip.get("start", 0.0)),
                # Basic transform placeholders (OpenShot uses keyframes normally):
                "x": clip.get("x", 0),
                "y": clip.get("y", 0),
                "scale": clip.get("scale", 1.0),
            }

            # Handle simple transition fade in/out if present
            tr = clip.get("transition") or {}
            if isinstance(tr, dict):
                _apply_simple_fades(c_dict, tr, add_note)

            project["clips"].append(c_dict)

    # Map audio as additional files placed on a dedicated audio track
    if timeline.get("audio"):
        a_track_id = str(uuid.uuid4())
        project["tracks"].append({"id": a_track_id, "number": len(project["tracks"])})
        for a in timeline["audio"]:
            apath = a.get("path")
            if not apath:
                continue
            fid = add_file(apath)
            project["clips"].append({
                "id": str(uuid.uuid4()),
                "file_id": fid,
                "track": a_track_id,
                "start": float(a.get("start", 0.0)),
                "end": float(a.get("end", max(0.0, float(a.get("start", 0.0)) + 0.1))),
                "position": float(a.get("start", 0.0)),
            })

    # Export target hint (used by our provider; OpenShot GUI will ignore it)
    project["export_path"] = export.get("filename") or f"output_{uuid.uuid4().hex}.mp4"
    return project


def _apply_simple_fades(c_dict: Dict[str, Any], tr: Dict[str, Any], add_note) -> None:
    """Map a subset of portal transitions to OpenShot-friendly fade hints.

    We only handle fade in/out here. Slide/wipe/reveal are added as notes for now
    (pending confirmation on desired mapping & availability).
    """
    # Accept shapes like {"in": "fade", "out": "fadeSlow"} or
    # {"in": {"name": "fade", "duration": 0.5}}
    def parse_one(val) -> (str, float):
        name = None
        dur = None
        if isinstance(val, str):
            name = val
        elif isinstance(val, dict):
            name = val.get("name")
            dur = val.get("duration")
        name = (name or "none").lower()
        # default durations approx
        default = {"fade": 0.5, "fadeslow": 1.0, "fadefast": 0.25}
        if dur is None:
            dur = default.get(name.replace(" ", ""), 0.5)
        return name, float(dur)

    name_in, dur_in = parse_one(tr.get("in"))
    name_out, dur_out = parse_one(tr.get("out"))

    if name_in.startswith("fade"):
        c_dict["fade_in"] = dur_in
    elif name_in != "none":
        add_note(f"Transition IN '{name_in}' not mapped; using hard cut.")

    if name_out.startswith("fade"):
        c_dict["fade_out"] = dur_out
    elif name_out != "none":
        add_note(f"Transition OUT '{name_out}' not mapped; using hard cut.")


def _looks_like_portal_timeline(data: Dict[str, Any]) -> bool:
    # Heuristics: tracks->clips present and some clips have 'transition' or 'type'
    if not isinstance(data, dict):
        return False
    tracks = data.get("tracks")
    if not isinstance(tracks, list):
        return False
    for t in tracks:
        for c in t.get("clips", []):
            if isinstance(c, dict) and ("transition" in c or "type" in c or "top_image_p" in c):
                return True
    return False


def _adapt_from_portal_timeline(src: Dict[str, Any], export: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort normalize portal/local/shotstack-like timeline to the internal one."""
    dst = {"tracks": [], "audio": src.get("audio", [])}

    # Copy through tracks & clips, normalize keys
    for t in (src.get("tracks") or []):
        nt = {"clips": []}
        for c in (t.get("clips") or []):
            nc = {
                "type": c.get("type"),
                "path": c.get("path") or c.get("src") or c.get("url"),
                "start": c.get("start") if c.get("start") is not None else c.get("position", 0.0),
                "end": c.get("end") if c.get("end") is not None else (
                    (c.get("start") or 0.0) + (c.get("length") or c.get("duration") or 0.1)
                ),
                "transition": c.get("transition"),
                "x": c.get("x"),
                "y": c.get("y"),
                "scale": c.get("scale"),
                "text": c.get("text"),
            }
            nt["clips"].append(nc)
        dst["tracks"].append(nt)

    # Profiles: adapt common names if present
    prof = (src.get("profile") or src.get("export") or {}).get("name") or (export.get("name"))
    if prof:
        preset = _profile_preset(str(prof))
        export.update(preset)

    return dst

def _adapt_from_portal_timeline_depr(src: Dict[str, Any], export: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort normalize portal/local/shotstack-like timeline to the internal one.

    Supported (confirmed): tracks[].clips[].{path,start,end,transition{in,out}}.

    Pending confirmation (left as meta notes by `_timeline_to_osp`): text overlays,
    advanced transitions (slide/wipe/reveal/carousel/shuffle), keyframed transforms,
    subtitles inline.
    """
    dst = {"tracks": [], "audio": src.get("audio", [])}

    # Copy through tracks & clips, normalize keys
    for t in (src.get("tracks") or []):
        nt = {"clips": []}
        for c in (t.get("clips") or []):
            nc = {
                "type": c.get("type"),
                "path": c.get("path") or c.get("src") or c.get("url"),
                "start": c.get("start") if c.get("start") is not None else c.get("position", 0.0),
                "end": c.get("end") if c.get("end") is not None else (
                    (c.get("start") or 0.0) + (c.get("length") or c.get("duration") or 0.1)
                ),
                "transition": c.get("transition"),
                "x": c.get("x"),
                "y": c.get("y"),
                "scale": c.get("scale"),
                "text": c.get("text"),
            }
            nt["clips"].append(nc)
        dst["tracks"].append(nt)

    # Profiles: adapt common names if present
    prof = (src.get("profile") or src.get("export") or {}).get("name") or (export.get("name"))
    if prof:
        preset = _profile_preset(str(prof))
        export.update(preset)

        return dst
    
    prof = {
        "fps_num": int(export.get("fps", _DEF_PROFILE["fps_num"])),
        "fps_den": 1,
        "width": int(export.get("width", _DEF_PROFILE["width"])),
        "height": int(export.get("height", _DEF_PROFILE["height"])),
        "sample_rate": int(export.get("sample_rate", _DEF_PROFILE["sample_rate"])),
        "channels": int(export.get("channels", _DEF_PROFILE["channels"])),
    }
    project = {
        "files": [],
        "clips": [],
        "tracks": [],
        "effects": [],
        "export_path": "",
        "profile": {"fps": f"{prof['fps_num']}/{prof['fps_den']}", "width": prof["width"], "height": prof["height"]},
        "meta": {"generator": "news-to-video.openshot-provider", "generated_at": int(time.time()), "original_timeline": timeline},
    }
    file_index = {}
    def add_file(path: str) -> str:
        p = os.path.abspath(path)
        if p in file_index:
            return file_index[p]
        file_id = str(uuid.uuid4())
        project["files"].append({"id": file_id, "path": p, "media_type": _guess_media_type(p)})
        file_index[p] = file_id
        return file_id
    for t_idx, t in enumerate(timeline.get("tracks", [])):
        track_id = str(uuid.uuid4())
        project["tracks"].append({"id": track_id, "number": t_idx})
        for clip in t.get("clips", []):
            path = clip.get("path")
            if not path:
                continue
            fid = add_file(path)
            project["clips"].append({"id": str(uuid.uuid4()), "file_id": fid, "track": track_id, "start": float(clip.get("start", 0.0)), "end": float(clip.get("end", max(0.0, float(clip.get("start", 0.0)) + 0.1))), "position": float(clip.get("start", 0.0)), "x": clip.get("x", 0), "y": clip.get("y", 0), "scale": clip.get("scale", 1.0)})
    if timeline.get("audio"):
        a_track_id = str(uuid.uuid4())
        project["tracks"].append({"id": a_track_id, "number": len(project["tracks"])})
        for a in timeline["audio"]:
            apath = a.get("path")
            if not apath:
                continue
            fid = add_file(apath)
            project["clips"].append({"id": str(uuid.uuid4()), "file_id": fid, "track": a_track_id, "start": float(a.get("start", 0.0)), "end": float(a.get("end", max(0.0, float(a.get("start", 0.0)) + 0.1))), "position": float(a.get("start", 0.0))})
    project["export_path"] = export.get("filename") or f"output_{uuid.uuid4().hex}.mp4"
    return project


def _guess_media_type(path: str) -> str:
    ext = os.path.splitext(path.lower())[1]
    if ext in {".mp4", ".mov", ".mkv", ".webm"}: return "video"
    if ext in {".mp3", ".wav", ".aac", ".flac", ".m4a"}: return "audio"
    if ext in {".jpg", ".jpeg", ".png"}: return "image"
    return "unknown"


def _render_with_libopenshot(project_path: Path, export_path: Path, job: _OpenShotJob) -> None:
    import openshot as oslib  # type: ignore
    with open(project_path, "r", encoding="utf-8") as f:
        project = json.load(f)
    fps_num = int(job.export.get("fps", _DEF_PROFILE["fps_num"]))
    width = int(job.export.get("width", _DEF_PROFILE["width"]))
    height = int(job.export.get("height", _DEF_PROFILE["height"]))
    timeline = oslib.Timeline(width, height, fps_num, 1, oslib.LAYOUT_HD)
    files_by_id = {f["id"]: f for f in project.get("files", [])}
    for c in project.get("clips", []):
        f = files_by_id.get(c.get("file_id"))
        if not f:
            continue
        reader = oslib.FFmpegReader(f.get("path"))
        clip = oslib.Clip(reader)
        start = float(c.get("start", 0.0))
        end = float(c.get("end", start))
        if end < start:
            end = start
        clip.Position(start)
        clip.End(end)
        timeline.AddClip(clip)
    writer = oslib.FFmpegWriter(str(export_path))
    writer.SetVideoCodec("libx264")
    writer.SetAudioCodec("aac")
    writer.SetVideoBitRate(int(job.export.get("video_bitrate", 4000)) * 1000)
    writer.SetAudioBitRate(int(job.export.get("audio_bitrate", 192)) * 1000)
    writer.SetAudioSampleRate(int(job.export.get("sample_rate", _DEF_PROFILE["sample_rate"])))
    writer.SetAudioChannels(int(job.export.get("channels", _DEF_PROFILE["channels"])))
    total_frames = int(timeline.Duration() * fps_num)
    def on_progress(frame: int) -> None:
        pct = int((frame / max(1, total_frames)) * 100)
        state = job.read_state()
        state.update({"progress": min(100, max(0, pct))})
        job.write_state(state)
    timeline.Export(writer, on_progress)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenShot provider headless test")
    parser.add_argument("--workdir", required=True, help="Job work directory")
    parser.add_argument("--config", required=True, help="Path to config JSON")
    parser.add_argument("--action", choices=["prepare", "start", "status", "collect"], default="prepare")
    args = parser.parse_args()
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if args.action == "prepare":
        out = prepare(cfg, args.workdir)
    elif args.action == "start":
        out = start(cfg, args.workdir)
    elif args.action == "status":
        out = status(cfg, args.workdir)
    else:
        out = collect_outputs(cfg, args.workdir)
    print(json.dumps(out, ensure_ascii=False, indent=2))


# --------------------------- App-facing convenience API -------------------------

def _profile_preset(name: str) -> Dict[str, Any]:
    name = (name or "").lower()
    presets = {
        "1080p": {"width": 1920, "height": 1080, "fps": 30},
        "720p": {"width": 1280, "height": 720, "fps": 30},
        "square": {"width": 1080, "height": 1080, "fps": 30},
        "vertical": {"width": 1080, "height": 1920, "fps": 30},
    }
    return presets.get(name, presets["1080p"])  # default


def render_via_openshot(project_dir: str, profile: Union[str, Dict[str, Any]] = "1080p") -> Dict[str, Any]:
    print(f'\n\t\tSTART ===> render_via_openshot({project_dir}, {profile})')
    """High-level entry called by the app to render a json2video task via OpenShot.

    Args:
        project_dir: Path to a directory produced by json2video task, expected to
            contain at least `timeline.json`. Media files should be reachable by
            absolute paths or relative to this directory.
        profile: Either a preset name ("1080p", "720p", "square", "vertical") or
            a dict with explicit export settings (width, height, fps, sample_rate,
            channels, filename, video_bitrate, audio_bitrate).

    Returns:
        Final provider state dict (e.g., {status, pid, outputs, message, ...}). If
        libopenshot is not available, status will be "prepared" and include path to
        the generated .osp file for manual export.
    """
    pdir = Path(project_dir)
    # timeline_path = pdir / "timeline.json"

    timeline_path = next((c for c in [
        pdir / "timeline.json",
        pdir / "outputs" / "timeline.json",
        pdir / ".build" / "timeline.json",
        pdir / "data" / "timeline.json",
    ] if c.exists()), None)
    if not timeline_path:
        raise FileNotFoundError(f"Missing timeline.json in {project_dir}")
    
    if not timeline_path.exists():
        raise FileNotFoundError(f"Missing timeline.json in {project_dir}")

    with open(timeline_path, "r", encoding="utf-8") as f:
        timeline = json.load(f)

    if isinstance(profile, dict):
        export = {**_DEF_PROFILE, **profile}
    else:
        export = {**_DEF_PROFILE, **_profile_preset(str(profile))}

    # Sensible filename if not provided
    export.setdefault("filename", f"output_{export['width']}x{export['height']}.mp4")

    # Compose provider config
    cfg: Dict[str, Any] = {
        "engine": "openshot",
        "timeline": timeline,
        "export": export,
        # pid left unset to auto-generate
    }

    # Place job workspace under a hidden folder inside the project
    workdir = str(pdir / ".openshot" / "jobs")
    if not Path(workdir).exists():
        print(f'❌ workdir dosn\'t exist==>{workdir}')

    # Prepare
    print(f'START to prep')
    prep = prepare(cfg, workdir)
    print(f'prep==>{prep}')
    print(f'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB')

    # If libopenshot is available, run the render synchronously and return final state
    if prep.get("libopenshot_available"):
        final = start({**cfg, "pid": prep["pid"]}, workdir)
        # Attach outputs explicitly
        try:
            outs = collect_outputs({**cfg, "pid": prep["pid"]}, workdir)
            final.update(outs)
        except Exception:
            pass
        return final

    # Otherwise return prepared state (manual export path included)
    return prep
