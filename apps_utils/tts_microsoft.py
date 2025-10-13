import os
import azure.cognitiveservices.speech as speechsdk
from xml.sax.saxutils import escape

# https://portal.azure.com/#@wmarzecgmail.onmicrosoft.com/resource/subscriptions/9b733927-ccf0-4469-bf6a-e38ed3daa0a3/resourceGroups/Londynek/providers/Microsoft.CognitiveServices/accounts/LondynekTextToSpeech/cskeys

def tts_microsoft(text, voice="pl-PL-MarekNeural", speed=1.0, output_path=None):
    if not output_path:
        return False

    speech_config = speechsdk.SpeechConfig(
        subscription=os.getenv("AZURE_SPEECH_KEY"),
        region=os.getenv("AZURE_REGION")
    )
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)

    ssml = f"""
<speak version='1.0' xml:lang='pl-PL'>
  <voice name='{voice}'>
    <prosody rate='{(speed - 1) * 100:+.0f}%'>
      {escape(text)}
    </prosody>
  </voice>
</speak>"""

    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
    try:
        result = synthesizer.speak_ssml_async(ssml).get()
    except Exception as e:
        print(f'Err1: {e}')

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        print('🎉 🎉 🎉 Synthesis succeeded.')
        return True
    
    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation_details = result.cancellation_details
        print(f'Synthesis was canceled. Reason: {cancellation_details.reason}')
        if cancellation_details.reason == speechsdk.CancellationReason.Error:
            print(f'Error details: {cancellation_details.error_details}')
        return False

    # print('AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')
    # print(output_path)
    # print('BBBBBBBBBBBBBBBBBBBBBBBBBBBBB')
    
    # result = synthesizer.speak_ssml_async(ssml).get()
    # print('🎉 🎉 🎉 ', result)
    # return result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted

def microsoft_list_voices():
    return list_voices()

def list_voices():
    speech_config = speechsdk.SpeechConfig(
        subscription=os.getenv("AZURE_SPEECH_KEY"),
        region=os.getenv("AZURE_REGION")
    )

    speech_config.set_property(speechsdk.PropertyId.SpeechServiceConnection_EndpointId, "")
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)

    result = synthesizer.get_voices_async().get()
    voices = []

    for v in result.voices:
        v_name = v.name
        # v_name = v.name.replace('Microsoft Server Speech Text to Speech Voice (', '')
        # v_name = v_name.rstrip(')')
        voices.append({
            "name": v_name,
            "language_codes": [v.locale],
            "gender": v.gender.name
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
    
    # print(first_row + pl_voices + brake_voices + other_voices)

    return first_row + pl_voices + brake_voices + other_voices
