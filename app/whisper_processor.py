import subprocess
import os
import json
import re

def transcribe_audio(audio_path, model_path, whisper_path=None, language='auto', task='transcribe'):
    """
    Transcribes audio using whisper-cli.exe.
    
    Args:
        audio_path (str): Path to the input wav file.
        model_path (str): Path to the whisper model (.bin).
        whisper_path (str): Path to whisper-cli.exe.
        language (str): Language of the audio ('auto', 'zh', 'en', etc.).
        task (str): 'transcribe' to get original text, 'translate' to translate to English.
    """
    if whisper_path is None:
        whisper_path = os.path.join(os.getcwd(), 'bin', 'whisper', 'whisper-cli.exe')
    
    if not os.path.exists(whisper_path):
        raise FileNotFoundError(f"Whisper CLI not found at {whisper_path}")
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found at {model_path}")

    # Build command based on whisper-cli help
    command = [
        whisper_path,
        '-m', model_path,
        '-f', audio_path,
        '-l', language,
        '-oj', # Output JSON
        '-of', audio_path.replace('.wav', '') # Output file prefix
    ]
    
    # If task is translate, add --translate flag
    if task == 'translate':
        command.append('--translate')
    
    print(f"Executing: {' '.join(command)}")
    
    try:
        subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8')
        
        json_path = audio_path.replace('.wav', '.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # whisper.cpp json structure: {"transcription": [{"from": start_ms, "to": end_ms, "text": "...", ...}]}
            segments = []
            for seg in data.get('transcription', []):
                segments.append({
                    'start': seg['offsets']['from'] / 1000.0,
                    'end': seg['offsets']['to'] / 1000.0,
                    'text': seg['text'].strip()
                })
            return segments
        else:
            print("Error: JSON output file not found.")
            return None
            
    except subprocess.CalledProcessError as e:
        print(f"Error during transcription: {e.stderr}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None

if __name__ == "__main__":
    # Test section
    test_audio = os.path.join("temp", "test_audio.wav")
    test_model = os.path.join("models", "ggml-base.bin")
    
    if os.path.exists(test_audio) and os.path.exists(test_model):
        results = transcribe_audio(test_audio, test_model)
        if results:
            for s in results[:5]: # Show first 5 segments
                print(f"[{s['start']} -> {s['end']}] {s['text']}")
    else:
        print("Test files not found.")
