import os
import sys

from services import WorkflowRuntime


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <video_path>")
        return

    video_path = sys.argv[1]
    if not os.path.exists(video_path):
        print(f"Error: Video file not found: {video_path}")
        return

    runtime = WorkflowRuntime(os.getcwd())
    state = runtime.run_prepare(
        video_path,
        source_language="auto",
        target_language="vi",
        mode="subtitle",
        translator_ai=True,
        whisper_model_name="ggml-base.bin",
    )
    print(f"Project state: {runtime.project_state_path(state)}")


if __name__ == "__main__":
    main()
