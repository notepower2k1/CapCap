import os


def browse_audio_folder(gui):
    from PySide6.QtWidgets import QFileDialog

    dir_path = QFileDialog.getExistingDirectory(gui, "Select Audio Folder")
    if dir_path:
        gui.audio_folder_edit.setText(dir_path)


def browse_srt_output_folder(gui):
    from PySide6.QtWidgets import QFileDialog

    dir_path = QFileDialog.getExistingDirectory(gui, "Select SRT Export Folder")
    if dir_path:
        gui.srt_output_folder_edit.setText(dir_path)


def browse_audio_source(gui):
    from PySide6.QtWidgets import QFileDialog

    file_path, _ = QFileDialog.getOpenFileName(gui, "Open Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
    if file_path:
        gui.audio_source_edit.setText(file_path)


def browse_background_audio(gui):
    from PySide6.QtWidgets import QFileDialog

    file_path, _ = QFileDialog.getOpenFileName(gui, "Open Background Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
    if file_path:
        gui.bg_music_edit.setText(file_path)


def browse_existing_mixed_audio(gui):
    from PySide6.QtWidgets import QFileDialog

    file_path, _ = QFileDialog.getOpenFileName(gui, "Open Mixed Audio", "", "Audio Files (*.wav *.mp3 *.flac)")
    if file_path:
        gui.mixed_audio_edit.setText(file_path)
        gui.use_existing_audio_radio.setChecked(True)
        gui.last_mixed_vi_path = file_path
        gui.processed_artifacts["mixed_vi"] = file_path
        gui.update_project_artifact("mixed_vi", file_path)


def browse_voice_output_folder(gui):
    from PySide6.QtWidgets import QFileDialog

    dir_path = QFileDialog.getExistingDirectory(gui, "Select Voice Output Folder")
    if dir_path:
        gui.voice_output_folder_edit.setText(dir_path)
        if hasattr(gui, "final_output_folder_edit"):
            gui.final_output_folder_edit.setText(dir_path)


def open_folder(gui, path):
    from PySide6.QtWidgets import QMessageBox

    try:
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        os.startfile(os.path.abspath(path))
    except Exception as exc:
        QMessageBox.critical(gui, "Error", f"Could not open folder:\n{exc}")


def cleanup_file_if_exists(path: str):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
