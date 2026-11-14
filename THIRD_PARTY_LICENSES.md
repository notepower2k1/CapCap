# Third-Party Licenses

CapCap includes or can distribute third-party libraries, binaries, models, fonts, and other resources. Those components remain under their respective licenses; CapCap's Apache-2.0 license applies only to CapCap's original code and documentation.

## Runtime libraries

- CapCut speech request formats, voice metadata handling, and task response handling were compared with [K07VN/capcut-tts-api](https://github.com/K07VN/capcut-tts-api). CapCap implements the common-task STT protocol with its own existing transport/signing helpers; it does not install or bundle that SDK. CapCut cloud services remain subject to their own terms.
- Optional CapCut PC integration loads `sscronet.dll` from the user's existing CapCut installation. This proprietary library is not included in CapCap's distribution and remains subject to CapCut's terms. CapCap's Python binding targets the [Chromium Cronet C API](https://github.com/chromium/chromium/blob/130.0.6723.58/components/cronet/native/cronet.idl); the headless integration approach was investigated using [capcut-tts-api-tn](https://github.com/thonguyeen/capcut-tts-api-tn). No helper binary or source from that repository is distributed.
- [PySide6 / Qt](https://www.qt.io/licensing/)
- [NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt)
- [SciPy](https://github.com/scipy/scipy/blob/main/LICENSE.txt)
- [OpenCV](https://github.com/opencv/opencv/blob/4.x/LICENSE)
- [RapidOCR](https://github.com/RapidAI/RapidOCR/blob/main/LICENSE)
- [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE)
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)
- [CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE)
- [Piper](https://github.com/rhasspy/piper/blob/master/LICENSE.md)
- [Edge-TTS](https://github.com/rany2/edge-tts)

## Bundled and optional binaries

- [FFmpeg](https://ffmpeg.org/legal.html) — the exact bundled build's license configuration applies.
- [mpv](https://github.com/mpv-player/mpv/blob/master/COPYING)
- NVIDIA CUDA/cuDNN runtime files — subject to NVIDIA's redistribution terms.

## Models, voices, and fonts

Model, voice, and font files may have licenses different from the software that loads them. Their original license files should be retained in their respective resource folders. This includes SenseVoice, Faster-Whisper, RapidOCR, Piper voices, speaker-detection models, and bundled fonts.

When redistributing a build with downloaded optional resources, verify and include the license supplied by the resource's original publisher.
