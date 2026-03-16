import requests
import os
from dotenv import load_dotenv

# Load environment variables from project root
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(base_dir, '.env')
load_dotenv(env_path)

class CloudflareTranslator:
    def __init__(self):
        self.url = os.getenv("TRANSLATOR_URL")
        self.api_key = os.getenv("TRANSLATOR_API_KEY")
        
        if not self.url or not self.api_key:
            print(f"Warning: TRANSLATOR_URL ({self.url}) or TRANSLATOR_API_KEY ({self.api_key}) missing or invalid in {env_path}")

    def translate_text(self, prompt):
        """
        Sends the full prompt to Cloudflare Workers AI.
        """
        try:
            payload = {
                "text": prompt
            }
            headers = {
                "x-api-key": self.api_key,
                "Content-Type": "application/json"
            }
            
            print(f"Calling Cloudflare API: {self.url}...")
            response = requests.post(self.url, headers=headers, json=payload, timeout=60)
            
            if response.status_code != 200:
                print(f"Cloudflare API Error ({response.status_code}): {response.text}")
                return ""

            result = response.json()
            
            # Check various common result keys for Cloudflare Workers AI
            if isinstance(result, dict):
                # Priority 1: 'response' (Llama 3.1 default)
                if "response" in result:
                    return result["response"]
                # Priority 2: 'translation' or 'translated_text'
                if "translation" in result:
                    return result["translation"]
                if "translated_text" in result:
                    return result["translated_text"]
                # Priority 3: 'result.response'
                if "result" in result:
                    res_val = result["result"]
                    if isinstance(res_val, dict) and "response" in res_val:
                        return res_val["response"]
                    if isinstance(res_val, str):
                        return res_val
            
            print(f"Unexpected JSON format or empty result: {result}")
            return ""
        except Exception as e:
            print(f"Cloudflare Translation Exception: {e}")
            return ""

    def translate_srt(self, srt_text, batch_size=5):
        """
        Translates SRT content by splitting into blocks and chunking (from test_translator.py).
        """
        if not srt_text or not srt_text.strip():
            return ""

        def split_srt_blocks(text):
            # Split by double newline to get SRT entries
            return [b.strip() for b in text.strip().split("\n\n") if b.strip()]

        def chunk_blocks(blocks, size):
            for i in range(0, len(blocks), size):
                yield blocks[i:i + size]

        blocks = split_srt_blocks(srt_text)
        translated_all_blocks = []
        
        print(f"Starting SRT Translation for {len(blocks)} blocks (Batch: {batch_size})...")
        
        for i, chunk in enumerate(chunk_blocks(blocks, batch_size)):
            sub_window = "\n\n".join(chunk)
            print(f"Translating Chunk {i+1}...")
            
            prompt = f"""
Translate the Chinese subtitles into Vietnamese.

Rules:
- The subtitles are in SRT format
- Keep index numbers unchanged
- Keep timestamps unchanged
- Only translate subtitle text (the lines after timestamps)
- Keep the exact same SRT structure
- Each subtitle block must remain exactly the same except the text line.
- Use natural, catchy Vietnamese suitable for marketing videos.

Subtitles:
{sub_window}
"""
            translated_response = self.translate_text(prompt)
            if translated_response:
                translated_all_blocks.append(translated_response.strip())
            else:
                # Fallback to original if API fails
                translated_all_blocks.append(sub_window)
                
        return "\n\n".join(translated_all_blocks)

def translate_segments_to_srt(srt_text, model_path=None, src_lang="auto"):
    """
    Translates raw SRT text using the Cloudflare logic.
    """
    translator = CloudflareTranslator()
    return translator.translate_srt(srt_text)

def translate_segments(segments, model_path=None, src_lang="auto"):
    """
    Legacy segments list interface. Converts to SRT first then translates.
    """
    # Simple conversion to SRT for the translator
    srt_lines = []
    for i, seg in enumerate(segments):
        srt_lines.append(f"{i+1}")
        # Format [0.0s] to 00:00:00,000 style for robustness if needed, 
        # but here we keep it simple as the translator handles it.
        # Actually, let's use a standard format for better results.
        import datetime
        def fmt_time(s):
            td = datetime.timedelta(seconds=s)
            hours, remainder = divmod(td.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{td.microseconds//1000:03d}"
            
        srt_lines.append(f"{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}")
        srt_lines.append(f"{seg['text']}\n")
        
    srt_text = "\n".join(srt_lines)
    translator = CloudflareTranslator()
    translated_srt = translator.translate_srt(srt_text)
    
    # We return the translated SRT string now
    return translated_srt

if __name__ == "__main__":
    # Test
    test_segs = [{'start': 0, 'end': 1, 'text': 'Hello world'}]
    res = translate_segments(test_segs, src_lang="en")
    print(res)
