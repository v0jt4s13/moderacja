import json
import os
import boto3
import tempfile
from pathlib import Path
# from apps_utils.s3_utils import s3_session
from openshot import render_via_openshot


def s3_session() -> dict:
    try:
        region_name=os.getenv("S3_REGION")
        bucket = os.getenv("AWS_S3_BUCKET")
        session = boto3.session.Session()
        s3 = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name
        )
        
    except Exception as e:
        print(f"❌ Błąd przy próbie połączenia do S3: {e}")
        return {}
    return s3, bucket, region_name

def handler(event, context):
    """
    Event minimalny:
    {
    "project": {"bucket": "my-bucket", "prefix": "jobs/1234/"},
    "profile": "vertical" # lub dict: {"width":1080,"height":1920,"fps":30}
    }
    Zakładamy, że w s3://bucket/prefix/ istnieje timeline.json. Media mogą być lokalne
    lub mieć ścieżki s3:// (wtedy zostaną pobrane do /tmp/<job>/ i zmapowane).
    """
    proj = event.get("project") or {}
    # bucket = proj.get("bucket")
    bucket = os.getenv("AWS_S3_BUCKET")
    region = os.getenv("S3_REGION")
    prefix = (proj.get("prefix") or "").rstrip("/") + "/"
    profile = event.get("profile") or "1080p"


    if not bucket or not prefix:
        return {"statusCode": 400, "body": json.dumps({"error": "Missing project.bucket/prefix"})}

    # Pobierz timeline.json
    s3_key_timeline = prefix + "timeline.json"
    with tempfile.TemporaryDirectory() as td:
        pdir = Path(td) / "project"
        pdir.mkdir(parents=True, exist_ok=True)
        timeline_local = pdir / "timeline.json"
        
        s3, bucket, region_name = s3_session()

        s3.download_file(bucket, s3_key_timeline, str(timeline_local))



        # Wczytaj i przemapuj media z s3:// na lokalne
        timeline = json.loads(Path(timeline_local).read_text("utf-8"))
        # timeline = _rewrite_timeline_media(timeline, pdir)
        timeline_local.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), "utf-8")


        # Uruchom render (lub wygeneruj .osp)
        result = render_via_openshot(str(pdir), profile)


        # Zbierz pliki wyjściowe i wgraj do S3 pod prefixem outputs/
        outputs = result.get("outputs") or {}
        uploaded = {}
        for name, path in outputs.items():
            if not path:
                continue
            fpath = Path(path)
            if fpath.exists():
                out_key = f"{prefix}outputs/{fpath.name}"
                s3.upload_file(str(fpath), bucket, out_key)
                uploaded[name] = f"s3://{bucket}/{out_key}"


        body = {
            "status": result.get("status"),
            "message": result.get("message"),
            "pid": result.get("pid"),
            "outputs": uploaded,
            "libopenshot": result.get("libopenshot_available"),
        }

        return {"statusCode": 200, "body": json.dumps(body, ensure_ascii=False)}
    

event = {
  "project": {"bucket": "<twoj-bucket>", "prefix": "jobs/demo-001/"},
  "profile": "1080p"
}
resp = handler(event, None)
print("HTTP:", resp["statusCode"])
print("BODY:", resp["body"])
PY
