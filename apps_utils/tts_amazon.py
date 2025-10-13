import os
import boto3

def tts_amazon(text, voice="Maja", speed=1.0, output_path=None):
    if not output_path:
        return False

    polly = boto3.client(
        "polly",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )

    ssml_text = f"""
<speak>
  <prosody rate='{int(speed * 100)}%'>{text}</prosody>
</speak>"""

    response = polly.synthesize_speech(
        TextType="ssml",
        Text=ssml_text,
        VoiceId=voice,
        OutputFormat="mp3"
    )
    # print("Polly response:", response)
    if "AudioStream" in response:
        with open(output_path, "wb") as f:
            f.write(response["AudioStream"].read())
        return True
    return False

def list_voices():
    polly = boto3.client(
        "polly",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )

    result = polly.describe_voices()
    voices = []

    for v in result["Voices"]:
        voices.append({
            "name": v["Id"],
            "language_codes": [v["LanguageCode"]],
            "gender": v["Gender"]
        })

    pl_voices = [v for v in voices if any("pl" in code.lower() for code in v["language_codes"])]
    allowed_langs = {"en-gb", "uk-ua"}
    other_voices = [
        v for v in voices
        if not any("pl" in code.lower() for code in v["language_codes"]) and
          any(code.lower() in allowed_langs for code in v["language_codes"])
    ]

    first_row = [{
        "name": "--- głosy wspierające język PL ---",
        "language_codes": [],
        "gender": ""
    }]
    brake_voices = [{
        "name": "--- pozostałe głosy ---",
        "language_codes": [],
        "gender": ""
    }]
    
    return first_row + pl_voices + brake_voices + other_voices



