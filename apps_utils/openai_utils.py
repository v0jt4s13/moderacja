import os
import tempfile
import uuid
from pathlib import Path

from apps_utils.tts_openai import tts_openai
from apps_utils.tts_google import tts_google
from apps_utils.tts_microsoft import tts_microsoft
from apps_utils.tts_amazon import tts_amazon
from apps_utils.tts_elevenlabs import tts_elevenlabs

def generate_audio_from_text(text, article_id, options=None):

    if not text or not text.strip():
        return None, {}

    options = options or {}
    
    provider = options.get("provider", "openai")
    voice = options.get("voice")
    speed = float(options.get("speed", 1.0))
    temp_dir = tempfile.gettempdir()
    filename = f"{article_id}_{provider}_{uuid.uuid4().hex[:8]}.mp3"
    output_path = str(Path(temp_dir) / filename)

    print(f"➡️ Zapisywanie do pliku: {output_path}")
    print(f"✔️ Długość tekstu: {len(text)}")
    print(f"🎙️ Provider: {provider}, voice: {voice}, speed: {speed}")

    # print(f'tts_({text}, {voice}, {speed}, {output_path})')

    try:
        if provider == "openai":
            success = tts_openai(text, voice=voice, speed=speed, output_path=output_path)
        elif provider == "google":
            success = tts_google(text, voice=voice, speed=speed, output_path=output_path)
        elif provider == "microsoft":
            success = tts_microsoft(text, voice=voice, speed=speed, output_path=output_path)
        elif provider == "amazon":
            success = tts_amazon(text, voice=voice, speed=speed, output_path=output_path)
        elif provider == "elevenlabs":
            success = tts_elevenlabs(text, voice=voice, speed=speed, output_path=output_path)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        print(f"🔁 Response status (success): {success}")
        
        if success:
            print(f'success | {provider} | {voice}')
            return output_path, {
                "provider": provider,
                "voice": voice,
                "speed": speed
            }
        else:
            print(f'fail | {provider} | {voice}')
            return None, {}

    except Exception as e:
        print(f"❌ Błąd TTS ({provider}):", e)
        return None, {}
