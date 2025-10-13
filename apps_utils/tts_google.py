import os
from google.cloud import texttospeech
from loggers import news_reader_project_logger

def tts_google(text, voice="pl-PL-Wavenet-A", speed=1.0, output_path=None):
    if not output_path:
        return False

    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)

    voice_config = texttospeech.VoiceSelectionParams(
        language_code=voice[0:5],
        name=voice
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speed
    )

    try:
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice_config,
            audio_config=audio_config
        )
        news_reader_project_logger.info('🎉 🎉 🎉 Synthesis succeeded.')
    except Exception as err:
        news_reader_project_logger.error(f'❌ [ERROR] Error: {err}')

    with open(output_path, "wb") as out:
        out.write(response.audio_content)
        return True

def google_list_voices():
    return list_voices()

def list_voices():
    client = texttospeech.TextToSpeechClient()

    response = client.list_voices()

    simplified = []
    simplified.append({
        "name": "--- głosy wspierające język PL ---",
        "language_codes": [],
        "gender": ""
    })
    for voice in response.voices:
        if any(code.startswith(("pl")) for code in voice.language_codes):
            simplified.append({
                "name": voice.name,
                "language_codes": list(voice.language_codes),
                "gender": texttospeech.SsmlVoiceGender(voice.ssml_gender).name,
                "natural_sample_rate_hertz": voice.natural_sample_rate_hertz
            })

    simplified.append({
        "name": "--- pozostałe głosy ---",
        "language_codes": [],
        "gender": ""
    })
    
    for voice in response.voices:
        if any(code.startswith(("en-GB", "uk-UA")) for code in voice.language_codes):
            simplified.append({
                "name": voice.name,
                "language_codes": list(voice.language_codes),
                "gender": texttospeech.SsmlVoiceGender(voice.ssml_gender).name,
                "natural_sample_rate_hertz": voice.natural_sample_rate_hertz
            })


    return simplified

