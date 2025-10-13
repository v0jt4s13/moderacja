"""
AWS Elemental MediaConvert integration for the News-to-video project.

Follows the same module style as other render backends in this codebase
(e.g., json2video.py). Provides a thin wrapper for:
  - creating a transcode job (MP4 16:9, MP4 9:16, optional HLS)
  - checking job status and progress
  - listing and cancelling jobs
  - mapping AWS statuses to the project's unified status schema

Environment variables (required unless marked optional):
  AWS_REGION
  MEDIACONVERT_ROLE_ARN
  MEDIACONVERT_QUEUE_ARN            (optional; defaults to default queue)
  MEDIACONVERT_ENDPOINT              (optional; auto-resolved and cached)
  MEDIACONVERT_BUCKET               
  CLOUDFRONT_BASE_URL                (optional; https://cdn.example.com)

External deps: boto3

Example:
    from mediaconvert import create_job, get_job_status

    res = create_job(
        input_url="s3://my-bucket/path/input.mp4",   # also accepts https
        output_prefix="projects/newsvideo/42/",
        job_name="news-42",
        make_hls=True,
    )
    print(res["job_id"])  # store for polling

    status = get_job_status(res["job_id"])  # returns unified dict
"""
from __future__ import annotations

import os
import json
import time
import logging
from typing import Dict, Any, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---- Exceptions -----------------------------------------------------------

class MediaConvertError(Exception):
    pass


# ---- Client bootstrap & endpoint caching ---------------------------------

_cached_endpoint_url: Optional[str] = None


def _get_env(name: str, required: bool = True, default: Optional[str] = None) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise MediaConvertError(f"Missing required env var: {name}")
    return val or ""


def _resolve_endpoint(session: boto3.session.Session) -> str:
    """Resolve and cache the account-specific MediaConvert endpoint."""
    global _cached_endpoint_url
    if _cached_endpoint_url:
        return _cached_endpoint_url

    region = _get_env("AWS_REGION")
    mc = session.client("mediaconvert", region_name=region)
    try:
        resp = mc.describe_endpoints(MaxResults=1)
        ep = resp["Endpoints"][0]["Url"]
        _cached_endpoint_url = ep
        logger.info("Resolved MediaConvert endpoint: %s", ep)
        return ep
    except ClientError as e:
        raise MediaConvertError(f"❌ [_resolve_endpoint] Failed to resolve MediaConvert endpoint: {e}") from e


def _client() -> Any:
    """Return a boto3 MediaConvert client with the proper endpoint."""
    region = _get_env("AWS_REGION")
    endpoint = os.getenv("MEDIACONVERT_ENDPOINT")

    session = boto3.session.Session(region_name=region)
    if not endpoint:
        endpoint = _resolve_endpoint(session)

    cfg = Config(retries={"max_attempts": 10, "mode": "standard"})
    return session.client("mediaconvert", endpoint_url=endpoint, config=cfg)


# ---- Public API -----------------------------------------------------------

def create_job(
    input_url: str,
    output_prefix: str,
    job_name: Optional[str] = None,
    make_hls: bool = False,
    enable_thumbs: bool = False,
    additional_tags: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create a MediaConvert job.

    Args:
        input_url: s3:// or https:// input. For https, MediaConvert must have access.
        output_prefix: path inside MEDIACONVERT_BUCKET, e.g. "projects/ntv/123/".
        job_name: optional name.
        make_hls: also produce HLS output group.
        enable_thumbs: produce thumbnails (jpg) from the first output.
        additional_tags: dict of AWS resource tags.

    Returns:
        dict with job_id, queue_arn, raw (AWS response), and predicted output keys.
    """
    bucket = _get_env("MEDIACONVERT_BUCKET")
    role_arn = _get_env("MEDIACONVERT_ROLE_ARN")
    queue_arn = os.getenv("MEDIACONVERT_QUEUE_ARN")  # optional

    outputs, output_groups = _build_outputs(bucket, output_prefix, make_hls, enable_thumbs)

    job_settings = {
        "Inputs": [
            {
                "FileInput": input_url,
                "TimecodeSource": "ZEROBASED",
                "VideoSelector": {},
                "AudioSelectors": {"Audio 1": {"DefaultSelection": "DEFAULT"}},
            }
        ],
        "OutputGroups": output_groups,
        "TimecodeConfig": {"Source": "ZEROBASED"},
    }

    client = _client()

    tags = {"Project": "news-to-video"}
    if additional_tags:
        tags.update(additional_tags)

    req = {
        "Role": role_arn,
        "Settings": job_settings,
        "UserMetadata": {"module": "mediaconvert.py"},
        "Tags": tags,
    }
    if queue_arn:
        req["Queue"] = queue_arn
    if job_name:
        req["Name"] = job_name

    try:
        resp = client.create_job(**req)
        job_id = resp["Job"]["Id"]
        q_arn = resp["Job"].get("Queue")
        return {
            "job_id": job_id,
            "queue_arn": q_arn,
            "raw": resp,
            "predicted_outputs": outputs,
        }
    except ClientError as e:
        raise MediaConvertError(f"❌ [create_job] Failed to create job: {e}") from e


def get_job_status(job_id: str) -> Dict[str, Any]:
    """Return a unified status dict for the given job id.

    Output schema (stable across render backends):
        {
          "status": "queued|rendering|done|error",
          "provider": "mediaconvert",
          "job_id": "...",
          "percent": 0..100 or None,
          "message": optional,
          "outputs": {"mp4_169": url, "mp4_916": url, "hls_master": url} (when done)
        }
    """
    client = _client()
    try:
        resp = client.get_job(Id=job_id)
    except ClientError as e:
        raise MediaConvertError(f"❌ [get_job_ststus] Failed to get job: {e}") from e

    job = resp.get("Job", {})
    aws_status = job.get("Status")  # SUBMITTED|PROGRESSING|COMPLETE|CANCELED|ERROR
    job_progress = None

    # Progress may be available under JobProgress.jobPercentComplete in newer APIs.
    prog = job.get("JobPercentComplete") or job.get("JobProgress", {}).get("JobPercentComplete")
    if isinstance(prog, int):
        job_progress = max(0, min(100, prog))

    unified_status = _map_status(aws_status)

    out: Dict[str, Any] = {
        "status": unified_status,
        "provider": "mediaconvert",
        "job_id": job_id,
        "percent": job_progress,
    }

    if unified_status == "error":
        # Try to lift an error message from the queue or job details
        msg = job.get("ErrorMessage") or job.get("Messages", {}).get("Info")
        out["message"] = json.dumps(msg) if isinstance(msg, (dict, list)) else (msg or "Unknown error")

    if unified_status == "done":
        # Build public URLs for known keys when possible
        outputs = _infer_output_urls(job)
        out["outputs"] = outputs

    return out


def list_jobs(max_results: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List recent jobs; optionally filter by AWS status string."""
    client = _client()
    params: Dict[str, Any] = {"MaxResults": max_results}
    if status:
        params["Status"] = status
    try:
        resp = client.list_jobs(**params)
        return resp.get("Jobs", [])
    except ClientError as e:
        raise MediaConvertError(f"❌ [list_jobs] Failed to list jobs: {e}") from e


def cancel_job(job_id: str) -> Dict[str, Any]:
    client = _client()
    try:
        resp = client.cancel_job(Id=job_id)
        return resp
    except ClientError as e:
        raise MediaConvertError(f"❌ [list_jobs] Failed to cancel job: {e}") from e


# ---- Helpers --------------------------------------------------------------

def _map_status(aws_status: Optional[str]) -> str:
    mapping = {
        None: "error",
        "SUBMITTED": "queued",
        "PROGRESSING": "rendering",
        "COMPLETE": "done",
        "CANCELED": "error",
        "ERROR": "error",
    }
    return mapping.get(aws_status, "error")


def _s3_url(bucket: str, key: str) -> str:
    base = os.getenv("CLOUDFRONT_BASE_URL")
    if base:  # serve via CDN if configured
        base = base.rstrip("/")
        return f"{base}/{key.lstrip('/')}"
    return f"s3://{bucket}/{key.lstrip('/')}"


def _infer_output_urls(job: Dict[str, Any]) -> Dict[str, str]:
    """Best-effort reconstruction of output object keys -> public URLs.

    We rely on OutputGroup settings we submitted; if not present (e.g., job
    created externally), returns an empty dict.
    """
    bucket = _get_env("MEDIACONVERT_BUCKET")

    # Try to read back settings
    settings = job.get("Settings", {}) or job.get("SettingsJson", {})
    out = {}
    try:
        groups = settings.get("OutputGroups", [])
        for g in groups:
            name = g.get("Name") or g.get("CustomName")
            out_settings = g.get("OutputGroupSettings", {})
            if out_settings.get("Type") == "FILE_GROUP_SETTINGS":
                dest = out_settings.get("FileGroupSettings", {}).get("Destination", "s3:///")
                # Gather each output and name modifiers
                for o in g.get("Outputs", []):
                    nm = o.get("NameModifier", "")
                    container = (o.get("ContainerSettings", {}) or {}).get("Container")
                    ext = ".mp4" if container == "MP4" else ""
                    # Heuristic mapping based on our builder
                    if nm == "_169":
                        out["mp4_169"] = _s3_url(bucket, _strip_s3_prefix(dest) + f"{nm}{ext}")
                    elif nm == "_916":
                        out["mp4_916"] = _s3_url(bucket, _strip_s3_prefix(dest) + f"{nm}{ext}")
            elif out_settings.get("Type") == "HLS_GROUP_SETTINGS":
                dest = out_settings.get("HlsGroupSettings", {}).get("Destination", "s3:///")
                master = _s3_url(bucket, _strip_s3_prefix(dest) + "master.m3u8")
                out["hls_master"] = master
    except Exception as e:
        logger.warning("Could not infer output URLs: %s", e)
    return out


def _strip_s3_prefix(s3_uri: str) -> str:
    if s3_uri.startswith("s3://"):
        parts = s3_uri.split("/", 3)
        # s3://bucket/key...
        return parts[3] + ("/" if not parts[3].endswith("/") else "") if len(parts) > 3 else ""
    return s3_uri.lstrip("/")


def _build_outputs(bucket: str, prefix: str, make_hls: bool, thumbs: bool) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Return (predicted_outputs, output_groups) for the job settings."""
    norm_prefix = prefix.strip("/") + "/"

    # File group for MP4s
    file_group = {
        "Name": "File MP4",
        "OutputGroupSettings": {
            "Type": "FILE_GROUP_SETTINGS",
            "FileGroupSettings": {
                "Destination": f"s3://{bucket}/{norm_prefix}mp4/"
            },
        },
        "Outputs": [
            _mp4_output(name_modifier="_169", width=1920, height=1080),
            _mp4_output(name_modifier="_916", width=1080, height=1920),
        ],
    }

    if thumbs:
        file_group["Outputs"][0]["VideoDescription"]["CodecSettings"]["H264Settings"]["MinIInterval"] = 1
        file_group["Outputs"][0]["VideoDescription"]["CodecSettings"]["H264Settings"]["GopSizeUnits"] = "SECONDS"
        file_group["Outputs"][0]["VideoDescription"]["CodecSettings"]["H264Settings"]["GopSize"] = 2
        # Add thumbnails to the first output
        file_group["Outputs"][0]["OutputSettings"] = {
            "HlsSettings": {},  # placeholder (not used for file), but required by MC SDK sometimes
        }
        file_group["Outputs"][0]["ContainerSettings"]["Mp4Settings"] = {
            "CslgAtom": "INCLUDE",
            "FreeSpaceBox": "EXCLUDE",
            "MoovPlacement": "PROGRESSIVE_DOWNLOAD",
        }

    output_groups = [file_group]

    predicted = {
        "mp4_169": _s3_url(bucket, f"{norm_prefix}mp4/_169.mp4"),
        "mp4_916": _s3_url(bucket, f"{norm_prefix}mp4/_916.mp4"),
    }

    if make_hls:
        hls_group = {
            "Name": "HLS",
            "OutputGroupSettings": {
                "Type": "HLS_GROUP_SETTINGS",
                "HlsGroupSettings": {
                    "Destination": f"s3://{bucket}/{norm_prefix}hls/",
                    "SegmentLength": 6,
                    "MinSegmentLength": 0,
                    "DirectoryStructure": "SINGLE_DIRECTORY",
                    "ManifestDurationFormat": "INTEGER",
                    "ManifestCompression": "NONE",
                    "ClientCache": "ENABLED",
                    "CaptionLanguageSetting": "OMIT",
                    "CodecSpecification": "RFC_4281",
                    "OutputSelection": "MANIFESTS_AND_SEGMENTS",
                    "ProgramDateTime": "EXCLUDE",
                    "StreamInfResolution": "INCLUDE",
                },
            },
            "Outputs": [
                _hls_output(360),
                _hls_output(480),
                _hls_output(720),
                _hls_output(1080),
            ],
        }
        output_groups.append(hls_group)
        predicted["hls_master"] = _s3_url(bucket, f"{norm_prefix}hls/master.m3u8")

    return predicted, output_groups


def _mp4_output(name_modifier: str, width: int, height: int) -> Dict[str, Any]:
    return {
        "ContainerSettings": {"Container": "MP4"},
        "NameModifier": name_modifier,
        "VideoDescription": {
            "Width": width,
            "Height": height,
            "ScalingBehavior": "DEFAULT",
            "TimecodeInsertion": "DISABLED",
            "AntiAlias": "ENABLED",
            "Sharpness": 50,
            "CodecSettings": {
                "Codec": "H_264",
                "H264Settings": {
                    "RateControlMode": "QVBR",
                    "QvbrQualityLevel": 7,
                    "MaxBitrate": 8000000,
                    "SceneChangeDetect": "TRANSITION_DETECTION",
                    "GopSize": 90,
                    "GopSizeUnits": "FRAMES",
                    "GopClosedCadence": 1,
                    "Slices": 1,
                    "NumberBFramesBetweenReferenceFrames": 2,
                    "CodecProfile": "HIGH",
                    "CodecLevel": "LEVEL_4_1",
                    "EntropyEncoding": "CABAC",
                    "ParControl": "SPECIFIED",
                    "ParNumerator": 1,
                    "ParDenominator": 1,
                },
            },
            "ColorMetadata": "INSERT",
        },
        "AudioDescriptions": [
            {
                "AudioSourceName": "Audio 1",
                "CodecSettings": {
                    "Codec": "AAC",
                    "AacSettings": {
                        "Bitrate": 128000,
                        "CodingMode": "CODING_MODE_2_0",
                        "SampleRate": 48000,
                    },
                },
            }
        ],
    }


def _hls_output(height: int) -> Dict[str, Any]:
    # Simple ladder: derive bitrate from height
    bitrate_table = {360: 800000, 480: 1400000, 720: 2800000, 1080: 5000000}
    br = bitrate_table.get(height, 2000000)
    return {
        "NameModifier": f"_{height}p",
        "ContainerSettings": {"Container": "M3U8"},
        "VideoDescription": {
            "Width": int(height * (16/9)),
            "Height": height,
            "CodecSettings": {
                "Codec": "H_264",
                "H264Settings": {
                    "RateControlMode": "QVBR",
                    "QvbrQualityLevel": 7,
                    "MaxBitrate": br,
                    "GopSize": 2,
                    "GopSizeUnits": "SECONDS",
                    "GopClosedCadence": 1,
                    "NumberBFramesBetweenReferenceFrames": 2,
                    "CodecProfile": "MAIN",
                },
            },
        },
        "AudioDescriptions": [
            {
                "AudioSourceName": "Audio 1",
                "CodecSettings": {
                    "Codec": "AAC",
                    "AacSettings": {
                        "Bitrate": 128000,
                        "CodingMode": "CODING_MODE_2_0",
                        "SampleRate": 48000,
                    },
                },
            }
        ],
        "OutputSettings": {"HlsSettings": {"SegmentModifier": f"_{height}p"}},
    }


# ---- Project-facing convenience wrapper ----------------------------------

def start_transcode(
    input_url: str,
    output_prefix: str,
    job_name: Optional[str] = None,
    with_hls: bool = False,
) -> Dict[str, Any]:
    """Alias for create_job with sane defaults, returns a minimal record to persist."""
    res = create_job(
        input_url=input_url,
        output_prefix=output_prefix,
        job_name=job_name,
        make_hls=with_hls,
    )
    return {
        "provider": "mediaconvert",
        "job_id": res["job_id"],
        "queue_arn": res.get("queue_arn"),
        "predicted_outputs": res.get("predicted_outputs", {}),
    }


def render_status(job_id: str) -> Dict[str, Any]:
    """Small adapter returning the schema used by /api/status in this project.

    Expected keys:
      - status (queued|rendering|done|error)
      - url_ready (bool or None)
      - outputs (dict of label->url when available)
    """
    s = get_job_status(job_id)
    out = {
        "status": s["status"],
        "provider": "mediaconvert",
        "percent": s.get("percent"),
        "url_ready": True if s["status"] == "done" else None,
        "outputs": s.get("outputs") or {},
        "job_id": job_id,
    }
    if s["status"] == "error":
        out["message"] = s.get("message")
    return out


__all__ = [
    "MediaConvertError",
    "create_job",
    "get_job_status",
    "list_jobs",
    "cancel_job",
    "start_transcode",
    "render_status",
]
