import os
import collections
import json
import boto3
import re
from config import get_config
get_config()
# from news_reader_project.s3_utils import S3_PREFIX, s3_session
from botocore.exceptions import ClientError
BASE_DIR = os.path.dirname(__file__)
RESULTS_DIR = os.path.join(BASE_DIR, "results")
AUDIO_TEST_RATINGS = os.path.join(RESULTS_DIR, "audio_test_ratings.jsonl")
S3_PREFIX = "londynek/audio/news-agency/"






# brak dostepu do .env
# sprobowac obejsc problem i zaimportowac s3_sessio




def s3_session() -> dict:
    try:
        region_name=os.getenv("S3_REGION")
        bucket = os.getenv("AWS_S3_BUCKET")
        session = boto3.session.Session()
        s3_session = session.client(
            's3',
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=region_name
        )
        
    except Exception as e:
        print(f"❌ Błąd przy próbie połączenia do S3: {e}")
        return {'Err': str(e)}, '', ''
    return s3_session, bucket, region_name

def load_ratings():
    ratings_file = AUDIO_TEST_RATINGS
    data = collections.defaultdict(lambda: {"sum": 0, "count": 0, "by_ip": {}})

    if not os.path.exists(ratings_file):
        return data

    with open(ratings_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                item = json.loads(line)
                key = f"{item['lang']}::{item['voice']}"
                ip = item.get("ip", "unknown")
                score = item["score"]
                if isinstance(score, str):
                    if score == "up":
                        score = 1
                    elif score == "down":
                        score = 0
                    else:
                        try:
                            score = int(score)
                        except:
                            continue
                data[key]["by_ip"][ip] = score
            except Exception:
                continue

    # przelicz sumy i liczby ocen
    for key in data:
        scores = list(data[key]["by_ip"].values())
        data[key]["sum"] = sum(scores)
        data[key]["count"] = len(scores)

    return data

def sort_urls_by_paragraph(urls: list) -> list:
    def extract_paragraph_num(url):
        match = re.search(r'_pl_p(\d+)\.mp3$', url)
        return int(match.group(1)) if match else float('inf')
    
    return sorted(urls, key=extract_paragraph_num)

def sort_audio_index_on_s3(year: int, month: int, lang: str):
    s3, bucket, region = s3_session()
    # print(s3, bucket, region)
    s3_key = f"{S3_PREFIX}{year:04d}/{month:02d}/audionews_{year:04d}_{month:02d}_{lang}.json"

    try:
        response = s3.get_object(Bucket=bucket, Key=s3_key)
        content = response['Body'].read().decode('utf-8')
        existing_data = json.loads(content)
        # print(f'existing_data type={type(existing_data)}')
        # print(f"existing_data.get('data') type={type(existing_data.get('data'))}")
        if "data" not in existing_data:
            existing_data["data"] = {}
        # print("ℹ️ Załadowano istniejący plik z S3")
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchKey':
            print("⚠️ Plik nie istnieje, zostanie utworzony nowy")
        else:
            print(f"❌ Błąd przy pobieraniu pliku z S3: {e}")
            return None
        
    try:
        print(f"🔍 Pobieranie pliku indeksu: {s3_key}")
        # obj = s3.get_object(Bucket=bucket, Key=s3_key)
        # body = obj["Body"].read().decode("utf-8")
        # # print(body)
        # data = json.loads(body)
        # # print(data)
        # print(data.get("status"))

        if existing_data.get("status") == "success" and "data" in existing_data:
            for ad_id, entry in existing_data["data"].items():
                # print(ad_id, type(entry)) #, entry.get('urls'))
                if "urls" in entry:
                    entry["urls"] = sort_urls_by_paragraph(entry["urls"])

            # Zapisz z powrotem
            updated_body = json.dumps(existing_data, indent=2, ensure_ascii=False, )
            s3.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=json.dumps(existing_data, ensure_ascii=False, indent=2),
                ContentType='application/json',
                ACL='public-read'
            )
            print(f"✅ Posortowano i zapisano: {s3_key}")
        else:
            print(f"⚠️ Brak danych w pliku: {s3_key}")

    except Exception as e:
        print(f"❌ Błąd podczas sortowania indeksu {s3_key}: {e}")
