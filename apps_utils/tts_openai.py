import os
import requests
from apps_utils.debug_utils import printLog

def tts_openai(text, voice="nova", speed=1.0, output_path=None):
    printLog(f'\n\t\tSTART ==> tts_openai(text, {voice}, {speed}, {output_path})')
    if not output_path:
        return False

    api_key = os.environ["OPENAI_API_KEY"]
    # printLog(f'def tts_openai ==> api_key={api_key}, {voice}, {speed}, {output_path}')
    headers = {
        "Authorization": f'Bearer {api_key}'
    }

    data = {
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "speed": speed
    }

    # printLog(f'def tts_openai ==> https://api.openai.com/v1/audio/speech", headers={headers}, json={data}')
    response = requests.post("https://api.openai.com/v1/audio/speech", headers=headers, json=data)
    # printLog(f'def tts_openai ==> response={response}')
    if response.ok:
        with open(output_path, "wb") as f:
            f.write(response.content)
        return True
    return False

def list_voices():
    voices = [
        {"name": "nova", "language_codes": ["pl-PL"], "gender": "FEMALE"},
        {"name": "onyx", "language_codes": ["pl-PL"], "gender": "MALE"},
        {"name": "shimmer", "language_codes": ["pl-PL"], "gender": "FEMALE"}
    ]
    first_row = [{
            "name": "--- głosy wspierające język PL ---",
            "language_codes": [],
            "gender": ""
    }]
    return first_row + voices