import os
import sys

import requests
from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)


def resolve_voice_id(raw_value: str) -> str:
    candidate = (raw_value or "").strip()
    if not candidate:
        return ""
    return os.getenv(candidate, candidate).strip()


def main():
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_arg = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    text = sys.argv[2].strip() if len(sys.argv) > 2 else "Xin chao, day la bai test voice API."

    voice_id = resolve_voice_id(voice_arg)
    if not api_key:
        raise SystemExit("Missing ELEVENLABS_API_KEY in .env")
    if not voice_id:
        raise SystemExit("Usage: python app/test_elevenlabs_tts.py <voice_id_or_env_name> [text]")

    model_id = "eleven_turbo_v2_5"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "language_code": "vi",
        "output_format": "mp3_44100_128",
    }

    print(f"Testing ElevenLabs voice_id: {voice_id}")
    print(f"Model: {model_id}")
    print(f"Text: {text}")
    print(f"URL: {url}")

    response = requests.post(url, headers=headers, json=payload, timeout=120)
    print(f"Status: {response.status_code}")

    content_type = response.headers.get("Content-Type", "")
    print(f"Content-Type: {content_type}")

    if response.status_code >= 400:
        try:
            print("Error JSON:")
            print(response.json())
        except ValueError:
            print("Error Text:")
            print(response.text)
        raise SystemExit(1)

    output_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"eleven_test_{voice_id}.mp3")
    with open(output_path, "wb") as output_file:
        output_file.write(response.content)

    print(f"Saved audio: {output_path}")


if __name__ == "__main__":
    main()
