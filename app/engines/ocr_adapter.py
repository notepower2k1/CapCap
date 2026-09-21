from ocr_processor import transcribe_video_ocr, _load_ocr_engine
from services.gpu_stage_scheduler import GPUStageScheduler


class OcrAdapter:
    def transcribe(self, video_path: str, model_path: str = "", *, language: str = "auto", task: str = "transcribe", region: str = "bottom", **kwargs):
        with GPUStageScheduler.stage("ocr"):
            return transcribe_video_ocr(video_path, region=region, language=language, **kwargs)

    def load_model(self, model_path: str = ""):
        return _load_ocr_engine()

    def transcribe_with_model(self, model, video_path: str, *, language: str = "auto", task: str = "transcribe", region: str = "bottom", **kwargs):
        with GPUStageScheduler.stage("ocr"):
            return transcribe_video_ocr(video_path, region=region, ocr_engine=model, language=language, **kwargs)
