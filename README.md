# CapCap

CapCap la desktop tool local de Viet hoa video theo workflow project-based: transcribe, translate, AI refine, subtitle preview, TTS, audio mix va export theo mode nguoi dung chon.

Tai lieu nay da duoc cap nhat theo spec moi trong `newSpec.md`. Luu y: repo hien tai moi o giai doan chuyen doi, nen README mo ta ca huong kien truc moi lan implementation dang co.

## Muc tieu san pham

CapCap huong toi mot workflow ban tu dong:

- tu dong hoa phan `Prepare`
- giu cac buoc subtitle, TTS, mix, preview va export o che do co review
- dam bao ket qua preview nhat quan voi ket qua export

He thong cho phep:

- extract transcript tu video
- dich sang tieng Viet
- AI refine ban dich khi can
- tao subtitle va preview truc tiep
- tao voice tieng Viet
- mix voice voi background
- export video voi subtitle, voice, hoac ca hai

## Export modes

CapCap duoc thiet ke de ho tro 4 mode:

- `Vietnamese Voice`
- `Vietnamese Subtitle`
- `Vietnamese Voice + Subtitle`
- `Custom`

Tuy chon bo sung:

- `Translator AI`
  - `OFF`: chi dung translation provider co ban
  - `ON`: raw translation xong se refine them bang AI provider

## Workflow moi

### Prepare phase

Voi cac mode khong phai `Custom`, he thong se tu dong chay:

1. extract audio
2. transcribe
3. raw translate
4. AI refine neu bat `Translator AI`
5. separate vocal/background neu mode co voice

### Manual phase

Nguoi dung chu dong review va thao tac tiep:

1. sua transcript / translation
2. build subtitle
3. generate TTS
4. mix audio
5. preview
6. export

### Custom mode

Neu chon `Custom`, pipeline `Prepare` khong tu chay. User chon tung step rieng le.

## Kien truc dich

Spec moi chia he thong thanh 3 lop:

- `Engine layer`: wrap FFmpeg, faster-whisper, translator, refine, Demucs, TTS, subtitle, preview, export
- `Workflow layer`: dieu phoi pipeline, resume, retry, skip step, quan ly state project
- `UI layer`: hien thi state, nhan input, trigger workflow, hien thi preview va loi

Huong refactor chi tiet duoc mo ta trong [structure.md](D:\CodingTime\CapCap\structure.md).

## Project data model

Moi video nen duoc quan ly nhu mot project rieng co:

- `source/`
- `analysis/`
- `translation/`
- `audio/`
- `subtitle/`
- `preview/`
- `export/`
- `project.json`

Don vi du lieu trung tam la `segment`, noi chua transcript goc, ban dich raw, ban dich refined, final subtitle text va final TTS text.

## Trang thai hien tai cua repo

Code hien tai da duoc tach mot phan lon theo target architecture. Repo dang dung cau truc thuc te sau:

- [app/core](D:\CodingTime\CapCap\app\core): `Segment`, `ProjectState` va core state/model
- [app/services](D:\CodingTime\CapCap\app\services): `ProjectService`, `SegmentService`, `GUIProjectBridge`, `EngineRuntime`, `WorkflowRuntime`
- [app/workflows](D:\CodingTime\CapCap\app\workflows): `PrepareWorkflow`, `VoiceWorkflow`, `ExportWorkflow`
- [app/main.py](D:\CodingTime\CapCap\app\main.py): CLI entry qua `WorkflowRuntime`
- [ui/main_window.py](D:\CodingTime\CapCap\ui\main_window.py): PySide6 main window
- [ui/views](D:\CodingTime\CapCap\ui\views): panel/view builders
- [ui/controllers](D:\CodingTime\CapCap\ui\controllers): subtitle/pipeline/preview controllers
- [ui/widgets](D:\CodingTime\CapCap\ui\widgets): custom widgets
- [ui/worker_adapters](D:\CodingTime\CapCap\ui\worker_adapters): processing/preview workers
- [ui/utils](D:\CodingTime\CapCap\ui\utils): media/settings/dialog/file helpers
- [ui/gui.py](D:\CodingTime\CapCap\ui\gui.py) va [ui/workers.py](D:\CodingTime\CapCap\ui\workers.py): compatibility shim giu cach goi cu

Dieu nay co nghia la README hien tai mo ta huong phat trien chinh, khong khang dinh repo da refactor xong theo structure moi.

## Yeu cau moi truong

- Windows 10/11
- Python 3.9+
- FFmpeg / FFprobe trong `bin/`
- model files trong `models/`

Ghi chu ASR:

- CapCap hien dung `faster-whisper` qua Python package
- model se duoc download va cache o lan dau tien theo model name dang dung
- khong con can `whisper.cpp` binaries trong `bin/whisper/`

## Cai dat

```bash
git clone https://github.com/notepower2k1/CapCap.git
cd CapCap
pip install -r requirements.txt
```

Tao file `.env` tu `.env_example` va dien cac bien can thiet cho provider ma ban dung.

## Chay ung dung

GUI hien tai:

```bash
python ui/gui.py
```

Hoac chay truc tiep main window moi:

```bash
python ui/main_window.py
```

CLI cu:

```bash
python app/main.py <video_path>
```

## Uu tien tiep theo

1. Tach engine layer thanh package/adapters ro rang hon.
2. Chuan hoa provider abstraction cho translation, AI refine, TTS va separation.
3. Giam dan cac shim compatibility trong `ui/`.
4. Hoan thien them workflow/service neu team muon chia nho hon nua.
5. Tiep tuc dong bo code voi target architecture trong `structure.md`.
