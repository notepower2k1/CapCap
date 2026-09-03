"""
Script tự động hóa tạo bộ cài đặt CapCap Installer bằng Inno Setup.
- Tự động tải vc_redist.x64.exe nếu chưa có
- Kiểm tra và gọi Inno Setup Compiler (ISCC.exe)
"""

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
INSTALLER_DIR = ROOT_DIR / "installer"
VC_REDIST_PATH = INSTALLER_DIR / "vc_redist.x64.exe"
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
ISS_SCRIPT_PATH = INSTALLER_DIR / "CapCap_Setup.iss"
DIST_APP_DIR = ROOT_DIR / "dist" / "CapCap"

INNO_SETUP_SEARCH_PATHS = [
    Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
    Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
    Path("C:/Program Files (x86)/Inno Setup 5/ISCC.exe"),
    Path("C:/Program Files/Inno Setup 5/ISCC.exe"),
]


def ensure_vc_redist() -> bool:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    if VC_REDIST_PATH.exists() and VC_REDIST_PATH.stat().st_size > 10 * 1024 * 1024:
        print(f"[OK] Đã có file: {VC_REDIST_PATH}")
        return True

    print("[*] Đang tải Microsoft Visual C++ Redistributable (x64) từ Microsoft...")
    try:
        def progress(count, block_size, total_size):
            percent = int(count * block_size * 100 / max(1, total_size))
            print(f"\r  -> Tiến độ: {percent}% ({count * block_size // (1024*1024)}MB / {total_size // (1024*1024)}MB)", end="", flush=True)

        urllib.request.urlretrieve(VC_REDIST_URL, str(VC_REDIST_PATH), reporthook=progress)
        print("\n[OK] Tải vc_redist.x64.exe thành công!")
        return True
    except Exception as e:
        print(f"\n[Error] Không thể tải vc_redist.x64.exe: {e}")
        print("  Vui lòng tải thủ công từ https://aka.ms/vs/17/release/vc_redist.x64.exe và lưu vào thư mục installer/")
        return False


def find_iscc() -> Path | None:
    which_iscc = shutil.which("ISCC.exe") or shutil.which("iscc")
    if which_iscc:
        return Path(which_iscc)

    for p in INNO_SETUP_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def build_installer() -> None:
    print("=" * 60)
    print("  CapCap Installer Builder (Inno Setup)")
    print("=" * 60)

    # 1. Kiểm tra dist/CapCap
    if not DIST_APP_DIR.exists() or not (DIST_APP_DIR / "CapCap.exe").exists():
        print(f"[Warning] Thư mục ứng dụng {DIST_APP_DIR} chưa có file CapCap.exe!")
        print("  Hãy chạy PyInstaller trước để tạo thư mục dist/CapCap:")
        print("  pyinstaller --clean CapCap.spec")
        proceed = input("\nBạn có muốn tiếp tục không? (y/n): ").strip().lower()
        if proceed != "y":
            return

    # 2. Đảm bảo có vc_redist.x64.exe
    if not ensure_vc_redist():
        return

    # 3. Tìm Inno Setup Compiler
    iscc_path = find_iscc()
    if not iscc_path:
        print("\n[Error] Không tìm thấy Inno Setup trên máy tính!")
        print("  Bạn có thể cài đặt nhanh qua lệnh Windows Terminal / PowerShell:")
        print("    winget install JRSoftware.InnoSetup")
        print("  Hoặc tải từ trang chủ: https://jrsoftware.org/isdl.php")
        return

    print(f"[OK] Tìm thấy Inno Setup Compiler: {iscc_path}")

    # 4. Biên dịch bộ cài đặt
    print("\n[*] Đang biên dịch bộ cài đặt CapCap_Setup...")
    cmd = [str(iscc_path), str(ISS_SCRIPT_PATH)]
    result = subprocess.run(cmd)

    if result.returncode == 0:
        dist_installer = ROOT_DIR / "dist_installer"
        print("\n" + "=" * 60)
        print("  [SUCCESS] Tạo bộ cài đặt thành công!")
        print(f"  File cài đặt nằm tại: {dist_installer}")
        print("=" * 60)
    else:
        print(f"\n[Error] Quá trình đóng gói thất bại với mã lỗi {result.returncode}")


if __name__ == "__main__":
    build_installer()
