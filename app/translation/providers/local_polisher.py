import os
import shutil
import subprocess
import sys
import threading
import re
from pathlib import Path

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..srt_utils import parse_numbered_lines, validate_texts


class LocalPolisherProvider:
    _model_lock = threading.Lock()
    _cached_model = None
    _cached_model_path = ""
    _cached_signature = ()

    @classmethod
    def _candidate_cuda_bin_dirs(cls) -> list[str]:
        candidates = []
        toolkit_root = str(os.getenv("CUDAToolkit_ROOT", "")).strip()
        if toolkit_root:
            candidates.append(os.path.join(toolkit_root, "bin"))
        nvcc_path = shutil.which("nvcc")
        if nvcc_path:
            candidates.append(os.path.dirname(os.path.abspath(nvcc_path)))
        default_root = Path(r"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA")
        if default_root.exists():
            version_dirs = sorted(default_root.glob("v*"), reverse=True)
            for version_dir in version_dirs:
                candidates.append(str(version_dir / "bin"))
        unique = []
        seen = set()
        for item in candidates:
            normalized = os.path.normcase(os.path.abspath(item)) if item else ""
            if normalized and os.path.isdir(normalized) and normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        return unique

    @classmethod
    def _ensure_cuda_runtime_on_path(cls) -> None:
        current_path = os.environ.get("PATH", "")
        for cuda_bin in cls._candidate_cuda_bin_dirs():
            if cuda_bin not in current_path:
                os.environ["PATH"] = cuda_bin + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(cuda_bin)
                except Exception:
                    pass

    @classmethod
    def detect_runtime_capabilities(cls) -> dict:
        info = {
            "cpu_count": os.cpu_count() or 8,
            "nvidia_gpu_detected": False,
            "gpu_name": "",
            "gpu_vram_mb": 0,
            "llama_gpu_supported": False,
        }
        cls._ensure_cuda_runtime_on_path()
        try:
            import llama_cpp

            support_fn = getattr(llama_cpp, "llama_supports_gpu_offload", None)
            if callable(support_fn):
                info["llama_gpu_supported"] = bool(support_fn())
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            first_line = (result.stdout or "").splitlines()[0].strip() if (result.stdout or "").strip() else ""
            if first_line:
                parts = [part.strip() for part in first_line.split(",", 1)]
                info["nvidia_gpu_detected"] = True
                info["gpu_name"] = parts[0]
                if len(parts) > 1:
                    try:
                        info["gpu_vram_mb"] = int(parts[1])
                    except Exception:
                        info["gpu_vram_mb"] = 0
        except Exception:
            pass
        return info

    @classmethod
    def recommended_runtime_config(cls, hardware_info: dict | None = None) -> dict:
        info = hardware_info or cls.detect_runtime_capabilities()
        cpu_count = max(4, int(info.get("cpu_count") or 8))
        threads = max(4, cpu_count - 2)
        has_gpu_path = bool(info.get("nvidia_gpu_detected") and info.get("llama_gpu_supported"))
        gpu_vram_mb = int(info.get("gpu_vram_mb") or 0)

        n_ctx = 2048
        if has_gpu_path and gpu_vram_mb >= 10000:
            n_ctx = 3072
        elif not has_gpu_path and cpu_count >= 16:
            n_ctx = 3072

        n_batch = 1024 if has_gpu_path else 768
        n_ubatch = 512 if has_gpu_path else 384
        return {
            "n_ctx": n_ctx,
            "n_threads": threads,
            "n_threads_batch": threads,
            "n_batch": n_batch,
            "n_ubatch": n_ubatch,
            "gpu_layers": 999 if has_gpu_path else 0,
            "flash_attn": bool(has_gpu_path),
        }

    @classmethod
    def runtime_status_summary(cls, hardware_info: dict | None = None) -> str:
        info = hardware_info or cls.detect_runtime_capabilities()
        cpu_count = int(info.get("cpu_count") or 0)
        gpu_name = str(info.get("gpu_name") or "").strip()
        gpu_vram_mb = int(info.get("gpu_vram_mb") or 0)
        gpu_vram_gb = gpu_vram_mb / 1024.0 if gpu_vram_mb > 0 else 0.0
        if info.get("nvidia_gpu_detected") and info.get("llama_gpu_supported"):
            return (
                f"NVIDIA GPU detected: {gpu_name or 'Unknown GPU'}"
                + (f" ({gpu_vram_gb:.1f} GB VRAM). " if gpu_vram_gb else ". ")
                + "Current llama-cpp-python build supports GPU offload."
            )
        if info.get("nvidia_gpu_detected"):
            return (
                f"NVIDIA GPU detected: {gpu_name or 'Unknown GPU'}"
                + (f" ({gpu_vram_gb:.1f} GB VRAM). " if gpu_vram_gb else ". ")
                + "Current llama-cpp-python build is CPU-only, so local AI cannot use the GPU yet."
            )
        return f"CPU-only local AI. Detected {cpu_count} logical CPU threads."

    def __init__(self):
        default_model = os.path.join(os.getcwd(), "models", "ai", "gemma-4-E4B-it-Q4_K_M.gguf")
        self.model_path = os.getenv("LOCAL_TRANSLATOR_MODEL_PATH", default_model).strip()
        self.hardware_info = self.detect_runtime_capabilities()
        recommended = self.recommended_runtime_config(self.hardware_info)
        self.n_ctx = self._safe_int(os.getenv("LOCAL_TRANSLATOR_N_CTX", str(recommended["n_ctx"])), recommended["n_ctx"])
        self.n_threads = self._safe_int(
            os.getenv("LOCAL_TRANSLATOR_N_THREADS", str(recommended["n_threads"])),
            recommended["n_threads"],
        )
        self.n_threads_batch = self._safe_int(
            os.getenv("LOCAL_TRANSLATOR_N_THREADS_BATCH", str(recommended["n_threads_batch"])),
            recommended["n_threads_batch"],
        )
        self.n_batch = self._safe_int(os.getenv("LOCAL_TRANSLATOR_N_BATCH", str(recommended["n_batch"])), recommended["n_batch"])
        self.n_ubatch = self._safe_int(os.getenv("LOCAL_TRANSLATOR_N_UBATCH", str(recommended["n_ubatch"])), recommended["n_ubatch"])
        self.gpu_layers = self._safe_int(os.getenv("LOCAL_TRANSLATOR_GPU_LAYERS", str(recommended["gpu_layers"])), recommended["gpu_layers"])
        self.flash_attn = self._safe_bool(os.getenv("LOCAL_TRANSLATOR_FLASH_ATTN", str(recommended["flash_attn"]).lower()), recommended["flash_attn"])
        self.temperature = self._safe_float(os.getenv("LOCAL_TRANSLATOR_TEMPERATURE", "0.15"), 0.15)
        self.max_tokens = self._safe_int(os.getenv("LOCAL_TRANSLATOR_MAX_TOKENS", "1400"), 1400)

    def is_configured(self) -> bool:
        return bool(self.model_path and os.path.exists(self.model_path))

    def polish_batch(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str] = None,
        src_lang: str,
        target_lang: str,
        style_instruction: str = "",
        timeout: int = 60,
        max_retries: int = 1,
    ) -> tuple[list[str], list[str], str]:
        if not self.is_configured():
            raise TranslationConfigError(
                "Local translator is not configured. Set LOCAL_TRANSLATOR_MODEL_PATH to a valid .gguf file."
            )

        try:
            model = self._get_model()
        except Exception as exc:
            raise TranslationConfigError(str(exc)) from exc

        prompt = self._build_prompt(
            source_texts=source_texts,
            translated_texts=translated_texts,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
        )

        try:
            result = model.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Translate or rewrite subtitles for short videos. "
                            "Return only numbered lines like '1. text'. Keep the exact line count."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self._estimate_max_tokens(source_texts, translated_texts),
            )
        except Exception as exc:
            raise TranslationProviderError(f"Local GGUF inference failed: {exc}") from exc

        text = self._extract_text(result)
        lines = parse_numbered_lines(text)
        if not validate_texts(lines, len(source_texts)):
            raise TranslationValidationError(
                f"Local translator returned {len(lines)} lines but expected {len(source_texts)}."
            )
        return lines, [], f"local-gguf ({os.path.basename(self.model_path)})"

    def _build_prompt(self, source_texts, translated_texts, src_lang, target_lang, style_instruction) -> str:
        is_direct = not translated_texts
        style_part = f" Style: {style_instruction}" if style_instruction else ""
        if is_direct:
            lines = [f"{i + 1}. {s}" for i, s in enumerate(source_texts)]
            header = (
                f"Translate these subtitle lines from {src_lang} to {target_lang}.{style_part}\n"
                "Target: natural Vietnamese for short-form video voiceover/subtitles.\n"
                "Format: Number. Text"
            )
        else:
            lines = [f"{i + 1}. {s} ||| {t}" for i, (s, t) in enumerate(zip(source_texts, translated_texts))]
            header = (
                f"Rewrite these subtitle translations from {src_lang} to {target_lang}.{style_part}\n"
                "Target: natural Vietnamese for short-form video voiceover/subtitles.\n"
                "Format: Number. Source ||| Draft"
            )
        return (
            f"{header}\n"
            "Rules: Keep meaning accurate, concise, natural, and easy to read quickly. "
            "Return only numbered lines. No commentary, no code fences, no extra headings.\n\n"
            + "\n".join(lines)
        )

    def _get_model(self):
        signature = (
            self.model_path,
            self.n_ctx,
            self.n_threads,
            self.n_threads_batch,
            self.n_batch,
            self.n_ubatch,
            self.gpu_layers,
            self.flash_attn,
        )
        with self._model_lock:
            if (
                self.__class__._cached_model is not None
                and self.__class__._cached_model_path == self.model_path
                and self.__class__._cached_signature == signature
            ):
                return self.__class__._cached_model

            self._ensure_cuda_runtime_on_path()
            try:
                from llama_cpp import Llama
            except Exception as exc:
                raise TranslationConfigError(
                    "llama-cpp-python is not installed. Run: python -m pip install llama-cpp-python"
                ) from exc

            model = Llama(
                model_path=self.model_path,
                n_ctx=self.n_ctx,
                n_batch=self.n_batch,
                n_ubatch=self.n_ubatch,
                n_threads=self.n_threads,
                n_threads_batch=self.n_threads_batch,
                n_gpu_layers=self.gpu_layers,
                flash_attn=self.flash_attn,
                offload_kqv=True,
                verbose=False,
            )
            self.__class__._cached_model = model
            self.__class__._cached_model_path = self.model_path
            self.__class__._cached_signature = signature
            return model

    def _extract_text(self, result) -> str:
        if isinstance(result, dict):
            choices = result.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return str(result or "").strip()

    def split_single_line_text(
        self,
        *,
        text: str,
        target_lang: str = "vi",
        max_chars: int = 24,
        max_chunks: int = 4,
    ) -> list[str]:
        if not self.is_configured():
            raise TranslationConfigError(
                "Local translator is not configured. Set LOCAL_TRANSLATOR_MODEL_PATH to a valid .gguf file."
            )
        compact = " ".join(str(text or "").replace("\n", " ").split()).strip()
        if not compact:
            return []

        try:
            model = self._get_model()
        except Exception as exc:
            raise TranslationConfigError(str(exc)) from exc

        prompt = (
            f"Split this {target_lang} subtitle into natural short reading chunks for single-line subtitles. "
            f"Keep the exact same wording and order. Do not rewrite, add, or remove words. "
            f"Return one line only, using ' || ' as the separator between chunks. "
            f"Target around {max_chars} characters per chunk, maximum {max_chunks} chunks.\n\n"
            f"Text: {compact}"
        )

        try:
            result = model.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You only split subtitle text into reading chunks. "
                            "Preserve the original wording exactly. Return one plain line using ' || ' separators only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=min(256, self.max_tokens),
            )
        except Exception as exc:
            raise TranslationProviderError(f"Local GGUF cue split failed: {exc}") from exc

        output = self._extract_text(result)
        chunks = [" ".join(part.split()) for part in output.split("||") if part.strip()]
        if not chunks:
            return [compact]
        return chunks

    def select_keyword_highlights_batch(
        self,
        *,
        texts: list[str],
        target_lang: str = "vi",
        max_keywords: int = 2,
    ) -> list[list[str]]:
        if not self.is_configured():
            raise TranslationConfigError(
                "Local translator is not configured. Set LOCAL_TRANSLATOR_MODEL_PATH to a valid .gguf file."
            )
        cleaned_texts = [" ".join(str(text or "").replace("\n", " ").split()).strip() for text in (texts or [])]
        if not cleaned_texts:
            return []

        try:
            model = self._get_model()
        except Exception as exc:
            raise TranslationConfigError(str(exc)) from exc

        numbered_lines = "\n".join(f"{idx + 1}. {text}" for idx, text in enumerate(cleaned_texts))
        prompt = (
            f"Select up to {max_keywords} short keyword phrases from each {target_lang} subtitle line for visual highlighting. "
            "Keep the exact original wording from the line. Do not rewrite or translate. "
            "Prefer names, numbers, products, strong nouns, and emotionally important phrases. "
            "Return only numbered lines. Use ' || ' between phrases on the same line. "
            "If a line should not highlight anything, still return the numbered line with nothing after the dot.\n\n"
            f"{numbered_lines}"
        )

        try:
            result = model.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You select subtitle highlight phrases only. "
                            "Return numbered lines only. Keep exact wording from the input line. "
                            "Never explain your choices."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=min(512, self.max_tokens),
            )
        except Exception as exc:
            raise TranslationProviderError(f"Local GGUF keyword highlight failed: {exc}") from exc

        output = self._extract_text(result)
        parsed_lines = self._parse_numbered_output(output)
        if len(parsed_lines) != len(cleaned_texts):
            raise TranslationValidationError(
                f"Local keyword highlight returned {len(parsed_lines)} lines but expected {len(cleaned_texts)}."
            )

        results: list[list[str]] = []
        for raw_line in parsed_lines:
            parts = [" ".join(part.split()) for part in str(raw_line or "").split("||")]
            deduped: list[str] = []
            seen = set()
            for part in parts:
                normalized = part.strip(" ,;|-")
                key = normalized.lower()
                if not normalized or key in seen:
                    continue
                seen.add(key)
                deduped.append(normalized)
                if len(deduped) >= max_keywords:
                    break
            results.append(deduped)
        return results

    def _estimate_max_tokens(self, source_texts: list[str], translated_texts: list[str] | None) -> int:
        total_chars = sum(len(str(item or "")) for item in source_texts)
        if translated_texts:
            total_chars += sum(len(str(item or "")) for item in translated_texts)
        estimated = 160 + (total_chars // 2)
        estimated = max(256, min(self.max_tokens, estimated))
        return estimated

    def _parse_numbered_output(self, raw: str) -> list[str]:
        items: list[str] = []
        for line in str(raw or "").splitlines():
            match = re.match(r"^\s*\d+\.\s*(.*?)\s*$", line)
            if match:
                items.append(match.group(1).strip())
        return items

    def _safe_int(self, raw_value: str, fallback: int) -> int:
        try:
            return int(str(raw_value).strip())
        except Exception:
            return fallback

    def _safe_float(self, raw_value: str, fallback: float) -> float:
        try:
            return float(str(raw_value).strip())
        except Exception:
            return fallback

    def _safe_bool(self, raw_value: str, fallback: bool) -> bool:
        normalized = str(raw_value or "").strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return fallback
