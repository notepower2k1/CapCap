import os
import sys
from video_processor import extract_audio
from whisper_processor import transcribe_audio
from subtitle_builder import generate_srt
from translator import translate_segments

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <video_path>")
        return

    video_path = sys.argv[1]
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        return

    # 1. Project paths
    project_root = os.getcwd()
    whisper_model = os.path.join(project_root, "models", "ggml-base.bin")
    nllb_model = os.path.join(project_root, "models", "nllb-200-distilled-600M")
    
    # 2. Prepare output path
    video_filename = os.path.basename(video_path)
    file_basename = os.path.splitext(video_filename)[0]
    
    temp_dir = os.path.join(project_root, "temp")
    output_dir = os.path.join(project_root, "output")
    
    for d in [temp_dir, output_dir]:
        if not os.path.exists(d):
            os.makedirs(d)
        
    audio_output_path = os.path.join(temp_dir, file_basename + ".wav")
    srt_original_path = os.path.join(output_dir, file_basename + "_original.srt")
    srt_translated_path = os.path.join(output_dir, file_basename + "_vi.srt")

    # 3. Step: Audio Extraction
    print(f"--- Step 1: Extracting audio ---")
    if extract_audio(video_path, audio_output_path):
        print(f"Success: Audio saved to {audio_output_path}")
    else:
        print("Failed: Audio extraction.")
        return

    # 4. Step: Transcription
    print(f"\n--- Step 2: Transcribing audio (Whisper) ---")
    # Luôn lấy tiếng gốc
    segments = transcribe_audio(audio_output_path, whisper_model, language='auto', task='transcribe')
    
    if segments:
        print(f"Success: Generated {len(segments)} segments.")
        
        # 5. Step: Generate Original SRT
        print(f"\n--- Step 3: Generating Original Subtitle ---")
        generate_srt(segments, srt_original_path)

        # 6. Step: Translation (NLLB)
        print(f"\n--- Step 4: Translating to Vietnamese (NLLB) ---")
        # Lưu ý: Whisper auto-detect có thể trả về code khác với NLLB
        # Ở đây ta mặc định zho_Hans cho video Trung Quốc bạn đang làm
        # Trong bản nâng cấp sau, ta sẽ map tự động từ Whisper language sang NLLB code
        translated_segments = translate_segments(segments, nllb_model, src_lang="zho_Hans")
        
        # 7. Step: Generate Translated SRT
        print(f"\n--- Step 5: Generating Vietnamese Subtitle ---")
        generate_srt(translated_segments, srt_translated_path)
        
        print(f"\nCOMPLETED! Files are in the 'output' folder.")
    else:
        print("Failed: Transcription.")


if __name__ == "__main__":
    main()
