"""
json2video.py — obsługa zadania "json2video" dla projektu News to video

Założenia:
- Wejściem jest specyfikacja JSON opisująca wideo (timeline, klipy, przejścia, napisy itd.).
- Moduł waliduje strukturę i dopasowuje nazwy przejść do wspieranych.
- Moduł uruchamia proces renderowania poprzez abstrakcyjny adapter (HTTP API lub lokalny renderer).
- Wyjściem są ścieżki do wygenerowanych plików MP4 (16:9, 1:1, 9:16) oraz metadane.

Integracja:
- Funkcja run(task) ma spójne API z innymi taskami projektu.
- Logger używa przestrzeni nazw "news2video.json2video".
- Wspieramy tryb CLI dla szybkich testów: `python json2video.py --in spec.json --out outdir/`.

Uwaga:
- Lista dozwolonych przejść jest zgodna z tym, co obsługuje renderer (zob. ALLOWED_TRANSITIONS).
- Nazwy spoza listy są mapowane na domyślne "fade", a informacje trafiają do loga (oraz do pola warnings w wyniku).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal

try:
    from pydantic import BaseModel, Field, ValidationError
    try:
        # Pydantic v2
        from pydantic import model_validator  # type: ignore
        PYDANTIC_V2 = True
    except Exception:  # Pydantic v1
        from pydantic import root_validator  # type: ignore
        PYDANTIC_V2 = False
except Exception:  # pydantic nie jest krytyczny do działania — fallback miękki
    PYDANTIC_V2 = False
    BaseModel = object  # type: ignore
    Field = lambda default=None, **_: default  # type: ignore
    ValidationError = Exception  # type: ignore
    def model_validator(*args, **kwargs):  # type: ignore
        def wrap(fn):
            return fn
        return wrap
    def root_validator(*args, **kwargs):  # type: ignore
        def wrap(fn):
            return fn
        return wrap


LOGGER = logging.getLogger("news2video.json2video")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _f = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _h.setFormatter(_f)
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

# Zgodne z komunikatem błędu w projekcie — lista wspieranych przejść
ALLOWED_TRANSITIONS = {
    "none",
    "fade", "fadeSlow", "fadeFast",
    "reveal", "revealSlow", "revealFast",
    "wipeLeft", "wipeLeftSlow", "wipeLeftFast",
    "wipeRight", "wipeRightSlow", "wipeRightFast",
    "slideLeft", "slideLeftSlow", "slideLeftFast",
    "slideRight", "slideRightSlow", "slideRightFast",
    "slideUp", "slideUpSlow", "slideUpFast",
    "slideDown", "slideDownSlow", "slideDownFast",
    "carouselLeft", "carouselLeftSlow", "carouselLeftFast",
    "carouselRight", "carouselRightSlow", "carouselRightFast",
    "carouselUp", "carouselUpSlow", "carouselUpFast",
    "carouselDown", "carouselDownSlow", "carouselDownFast",
    "shuffleTopRight", "shuffleTopRightSlow", "shuffleTopRightFast",
    "shuffleRightTop", "shuffleRightTopSlow", "shuffleRightTopFast",
    "shuffleRightBottom", "shuffleRightBottomSlow", "shuffleRightBottomFast",
    "shuffleBottomRight", "shuffleBottomRightSlow", "shuffleBottomRightFast",
    "shuffleBottomLeft", "shuffleBottomLeftSlow", "shuffleBottomLeftFast",
}

DEFAULT_TRANSITION = "fade"
SUPPORTED_ASPECTS = ["16:9", "9:16", "1:1"]


class Transition(BaseModel):  # type: ignore[misc]
    name: str = Field(alias="name")
    duration: float = Field(default=0.5, ge=0.0)

    @property
    def name(self) -> str:
        return getattr(self, "name", DEFAULT_TRANSITION)

    if PYDANTIC_V2:
        @model_validator(mode="after")  # type: ignore[valid-type]
        def _normalize(self):
            if not getattr(self, "name", None) or self.name not in ALLOWED_TRANSITIONS:
                self.name = DEFAULT_TRANSITION
            return self
    else:
        @root_validator(skip_on_failure=True)  # type: ignore[valid-type]
        def _normalize(cls, values):
            name = values.get("name") or DEFAULT_TRANSITION
            if name not in ALLOWED_TRANSITIONS:
                values["name"] = DEFAULT_TRANSITION
            return values


class Clip(BaseModel):  # type: ignore[misc]
    src: Optional[str] = None
    text: Optional[str] = None
    start: float = Field(default=0.0, ge=0.0)
    duration: float = Field(default=3.0, gt=0.0)
    transition_in: Optional[Transition] = Field(default=None, alias="transitionIn")
    transition_out: Optional[Transition] = Field(default=None, alias="transitionOut")
    layout: Dict[str, Any] = Field(default_factory=dict)


class Track(BaseModel):  # type: ignore[misc]
    kind: Literal["video", "audio", "subtitle"]
    clips: List[Clip] = Field(default_factory=list)


class Timeline(BaseModel):  # type: ignore[misc]
    fps: int = Field(default=30, ge=1, le=120)
    width: int = Field(default=1920, ge=256)
    height: int = Field(default=1080, ge=256)
    tracks: List[Track] = Field(default_factory=list)


class VideoSpec(BaseModel):  # type: ignore[misc]
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: Optional[str] = None
    timeline: Timeline
    outputs: Dict[str, Any] = Field(default_factory=dict)  # np. preset/aspecty


@dataclass
class RenderJob:
    job_id: str
    spec: Dict[str, Any]
    aspects: List[str] = field(default_factory=lambda: SUPPORTED_ASPECTS.copy())
    status: str = "queued"  # queued|rendering|done|error
    outputs: Dict[str, Optional[str]] = field(default_factory=dict)  # aspect->path/url
    warnings: List[str] = field(default_factory=list)


# ===== Walidacja i normalizacja =====

def _normalize_transitions(spec: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Zamienia niedozwolone nazwy przejść na DEFAULT_TRANSITION i zbiera ostrzeżenia."""
    warnings: List[str] = []
    tracks = spec.get("timeline", {}).get("tracks", [])
    for t_i, track in enumerate(tracks):
        clips = track.get("clips", [])
        for c_i, clip in enumerate(clips):
            for key in ("transitionIn", "transitionOut"):
                tr = clip.get(key)
                if isinstance(tr, dict):
                    name = tr.get("name")
                    if name and name not in ALLOWED_TRANSITIONS:
                        warnings.append(
                            f"tracks[{t_i}].clips[{c_i}].{key}.name='{name}' nieobsługiwane – zamieniono na '{DEFAULT_TRANSITION}'"
                        )
                        tr["name"] = DEFAULT_TRANSITION
    return spec, warnings


def validate_spec(raw_spec: Dict[str, Any]) -> Tuple[VideoSpec, List[str]]:
    spec_norm, warnings = _normalize_transitions(json.loads(json.dumps(raw_spec)))
    try:
        if hasattr(VideoSpec, "model_validate"):
            model = VideoSpec.model_validate(spec_norm)  # type: ignore[attr-defined]
        else:
            model = VideoSpec.parse_obj(spec_norm)  # type: ignore[attr-defined]
    except ValidationError as e:  # type: ignore[name-defined]
        print(f'❌ [validate_spec] Specyfikacja JSON jest nieprawidłowa: {e}')
        # Wyrzucamy podsumowanie w jednym komunikacie
        raise ValueError(f"Specyfikacja JSON jest nieprawidłowa: {e}")
    return model, warnings


# ===== Adapter renderujący =====

class RenderAdapter:
    """Abstrakcja nad mechanizmem renderowania.

    Implementacje:
    - HTTPRenderAdapter: POST do zewnętrznego API renderującego (np. REMOTION/FFmpeg service)
    - LocalStubAdapter: szybki stub tworzący puste pliki .mp4 (na potrzeby testów i CI)
    """

    def submit(self, job: RenderJob) -> str:
        raise NotImplementedError

    def poll(self, job_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def collect(self, job_id: str) -> Dict[str, str]:
        raise NotImplementedError


class LocalStubAdapter(RenderAdapter):
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def submit(self, job: RenderJob) -> str:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._jobs[job.job_id] = {
            "status": "rendering",
            "start": time.time(),
            "aspects": job.aspects,
        }
        LOGGER.info("[stub] Submit job %s for aspects %s", job.job_id, job.aspects)
        return job.job_id

    def poll(self, job_id: str) -> Dict[str, Any]:
        j = self._jobs.get(job_id)
        if not j:
            return {"status": "error", "error": "job_not_found"}
        # Symulacja krótkiego renderu
        if time.time() - j["start"] > 1.0:
            j["status"] = "done"
        return {"status": j["status"]}

    def collect(self, job_id: str) -> Dict[str, str]:
        j = self._jobs.get(job_id)
        if not j:
            raise RuntimeError("job_not_found")
        outputs: Dict[str, str] = {}
        for aspect in j["aspects"]:
            filename = f"{job_id.replace('-', '')}_{aspect.replace(':', 'x')}.mp4"
            path = self.out_dir / filename
            # Tworzymy pusty plik jako placeholder
            with open(path, "wb") as f:
                f.write(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")
            outputs[aspect] = str(path)
        return outputs


class HTTPRenderAdapter(RenderAdapter):
    """Prosty adapter HTTP — POST /render, GET /status/<id>, GET /result/<id>?aspect=16:9
    Wymagane zmienne środowiskowe:
        RENDER_API_URL (np. https://renderer.internal)
        RENDER_API_TOKEN (opcjonalne)
    """

    def __init__(self):
        import urllib.request
        self._http = urllib.request
        self.base = os.getenv("RENDER_API_URL")
        self.token = os.getenv("RENDER_API_TOKEN")
        if not self.base:
            raise RuntimeError("Brak RENDER_API_URL — nie można użyć HTTPRenderAdapter")

    def submit(self, job: RenderJob) -> str:
        payload = json.dumps({
            "job_id": job.job_id,
            "spec": job.spec,
            "aspects": job.aspects,
        }).encode("utf-8")
        req = self._req("/render", data=payload, method="POST")
        with self._http.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("job_id", job.job_id)

    def poll(self, job_id: str) -> Dict[str, Any]:
        req = self._req(f"/status/{job_id}")
        with self._http.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def collect(self, job_id: str) -> Dict[str, str]:
        outputs: Dict[str, str] = {}
        for aspect in SUPPORTED_ASPECTS:
            req = self._req(f"/result/{job_id}?aspect={aspect}")
            try:
                with self._http.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if url := data.get("url"):
                        outputs[aspect] = url
            except Exception as e:  # brak danego aspektu nie jest krytyczny
                LOGGER.warning("❌ Brak wyniku dla aspect=%s: %s", aspect, e)
        return outputs

    def _req(self, path: str, data: Optional[bytes] = None, method: str = "GET"):
        url = self.base.rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return self._http.Request(url, data=data, headers=headers, method=method)


# ===== Główna ścieżka wykonania =====

def run(task: Dict[str, Any]) -> Dict[str, Any]:
    """Punkt wejścia dla task runnera projektu.

    Wejście (przykład):
        {
          "kind": "json2video",
          "spec": { ... },              # dict lub ścieżka do pliku
          "aspects": ["16:9", "9:16"], # opcjonalnie
          "out_dir": "/tmp/vid/",      # dla LocalStubAdapter
          "adapter": "auto|http|stub"   # wybór adaptera
        }

    Zwraca:
        {
          "status": "done|error",
          "job_id": "...",
          "outputs": {"16:9": "...", ...},
          "warnings": [...],
        }
    """
    if task.get("kind") != "json2video":
        raise ValueError("Nieprawidłowy rodzaj zadania dla json2video.run")

    spec_input = task.get("spec")
    if isinstance(spec_input, str) and os.path.exists(spec_input):
        with open(spec_input, "r", encoding="utf-8") as f:
            raw_spec = json.load(f)
    elif isinstance(spec_input, dict):
        raw_spec = spec_input
    else:
        raise ValueError("Brak lub nieprawidłowe pole 'spec' (dict lub ścieżka do .json)")

    spec_model, warnings = validate_spec(raw_spec)

    aspects = task.get("aspects") or SUPPORTED_ASPECTS
    aspects = [a for a in aspects if a in SUPPORTED_ASPECTS]
    if not aspects:
        aspects = ["16:9"]

    spec_dict = spec_model.model_dump() if hasattr(spec_model, "model_dump") else spec_model.dict()
    job = RenderJob(job_id=str(uuid.uuid4()), spec=spec_dict, aspects=aspects)
    job.warnings.extend(warnings)

    adapter_choice = (task.get("adapter") or "auto").lower()
    adapter: RenderAdapter
    if adapter_choice == "http":
        adapter = HTTPRenderAdapter()
    elif adapter_choice == "stub":
        out_dir = Path(task.get("out_dir") or "/tmp/json2video")
        adapter = LocalStubAdapter(out_dir)
    else:  # auto
        if os.getenv("RENDER_API_URL"):
            adapter = HTTPRenderAdapter()
        else:
            out_dir = Path(task.get("out_dir") or "/tmp/json2video")
            adapter = LocalStubAdapter(out_dir)

    LOGGER.info("Uruchamiam render json2video: job_id=%s, aspects=%s", job.job_id, job.aspects)

    job_id = adapter.submit(job)
    # polling
    last_status = "queued"
    start_ts = time.time()
    timeout_s = float(task.get("timeout_s", 600))

    while True:
        status_payload = adapter.poll(job_id)
        status = (status_payload or {}).get("status", "unknown")
        if status != last_status:
            LOGGER.info("[%s] status -> %s", job_id, status)
            last_status = status
        if status in ("done", "error"):
            break
        if time.time() - start_ts > timeout_s:
            LOGGER.error("[%s] timeout po %.1fs", job_id, timeout_s)
            status = "error"
            break
        time.sleep(1.0)

    outputs: Dict[str, str] = {}
    if status == "done":
        outputs = adapter.collect(job_id)

    result = {
        "status": status,
        "job_id": job_id,
        "outputs": outputs,
        "warnings": job.warnings,
    }
    return result


# ===== API dla aplikacji =====

def render_via_json2video(project_dir: str, profile: str) -> Dict[str, Any]:
    """Wygodna funkcja wywoływana z aplikacji.

    Konwencje wyszukiwania specyfikacji JSON (pierwszy istniejący plik):
      1) {project_dir}/profiles/{profile}.json
      2) {project_dir}/specs/{profile}.json
      3) {project_dir}/workdir/{profile}.json
      4) {project_dir}/{profile}.json
      5) {project_dir}/spec.json

    Wyniki trafiają do: {project_dir}/out/{profile}/

    Zwraca słownik zgodny z `run(task)`, np. {"status", "job_id", "outputs", "warnings"}.
    """
    base = Path(project_dir)
    candidates = [
        base / "profiles" / f"{profile}.json",
        base / "specs" / f"{profile}.json",
        base / "workdir" / f"{profile}.json",
        base / f"{profile}.json",
        base / "spec.json",
    ]

    spec_path: Optional[Path] = None
    for c in candidates:
        if c.exists() and c.is_file():
            spec_path = c
            break

    if not spec_path:
        msg = (
            f"Nie znaleziono specyfikacji dla profilu '{profile}'. "
            f"Sprawdzone ścieżki: {', '.join(str(c) for c in candidates)}"
        )
        LOGGER.error(msg)
        return {
            "status": "error",
            "error": "spec_not_found",
            "warnings": [msg],
        }

    out_dir = base / "out" / profile
    out_dir.mkdir(parents=True, exist_ok=True)

    # Aspekty można nadpisać zmienną środowiskową JSON2VIDEO_ASPECTS="16:9,9:16"
    aspects_env = os.getenv("JSON2VIDEO_ASPECTS")
    if aspects_env:
        aspects = [a.strip() for a in aspects_env.split(",") if a.strip() in SUPPORTED_ASPECTS]
        if not aspects:
            aspects = SUPPORTED_ASPECTS
    else:
        aspects = SUPPORTED_ASPECTS

    timeout_s = float(os.getenv("JSON2VIDEO_TIMEOUT_S", "600"))

    task = {
        "kind": "json2video",
        "spec": str(spec_path),
        "adapter": "auto",  # HTTP jeśli dostępny RENDER_API_URL, inaczej stub
        "out_dir": str(out_dir),
        "aspects": aspects,
        "timeout_s": timeout_s,
    }

    try:
        LOGGER.info("render_via_json2video start: dir=%s profile=%s spec=%s", project_dir, profile, spec_path)
        return run(task)
    except Exception as e:
        LOGGER.exception("❌ [render_via_json2video] render_via_json2video failed: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "warnings": [],
        }


# ===== CLI =====

def _cli():
    p = argparse.ArgumentParser(description="Render wideo z JSON (json2video)")
    p.add_argument("--in", dest="inp", required=True, help="Ścieżka do spec.json")
    p.add_argument("--out", dest="out_dir", default="./out", help="Katalog wyników (dla stub)")
    p.add_argument("--adapter", dest="adapter", default="auto", choices=["auto", "http", "stub"], help="Wybór adaptera")
    p.add_argument("--aspects", dest="aspects", default="16:9,9:16,1:1", help="Lista aspektów, np. 16:9,1:1")
    p.add_argument("--timeout", dest="timeout", type=float, default=600.0)
    args = p.parse_args()

    aspects = [a.strip() for a in args.aspects.split(",") if a.strip()]

    task = {
        "kind": "json2video",
        "spec": args.inp,
        "adapter": args.adapter,
        "out_dir": args.out_dir,
        "aspects": aspects,
        "timeout_s": args.timeout,
    }

    result = run(task)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
