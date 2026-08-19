from __future__ import annotations

import os
import re
import traceback
from typing import Callable

from runtime_paths import bin_path, temp_path


class VideoDownloadService:
    """
    Service to download videos from YouTube, Douyin, TikTok, Bilibili,
    Kuaishou, Facebook, and 1000+ platforms using yt-dlp.
    """

    def __init__(self, download_dir: str = ""):
        self.download_dir = download_dir or temp_path("downloads")
        os.makedirs(self.download_dir, exist_ok=True)

    @staticmethod
    def is_available() -> bool:
        try:
            import yt_dlp
            return True
        except ImportError:
            return False

    @staticmethod
    def extract_url(text: str) -> str:
        """
        Extract the first valid http(s) URL from text (e.g. from Douyin/TikTok share text).
        """
        if not text:
            return ""
        match = re.search(r"https?://[^\s\"'<>]+", str(text).strip())
        return match.group(0) if match else ""

    def _ffmpeg_location(self) -> str:
        """Find the directory containing ffmpeg.exe."""
        ffmpeg_exe = bin_path("ffmpeg", "ffmpeg.exe")
        if os.path.exists(ffmpeg_exe):
            return os.path.dirname(os.path.abspath(ffmpeg_exe))
        ffmpeg_bin = bin_path("ffmpeg")
        if os.path.exists(ffmpeg_bin):
            return os.path.abspath(ffmpeg_bin)
        return ""

    def _resolve_cookie_file(self, custom_cookie_file: str = "") -> str:
        """Resolve a valid cookies.txt file from custom path or standard locations."""
        if custom_cookie_file and os.path.isfile(custom_cookie_file):
            return os.path.abspath(custom_cookie_file)
        candidates = [
            bin_path("cookies.txt"),
            bin_path("douyin_cookies.txt"),
            temp_path("cookies.txt"),
            os.path.abspath("cookies.txt"),
        ]
        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 0:
                return os.path.abspath(c)
        return ""

    def _default_headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,vi;q=0.7",
        }

    def get_video_info(
        self,
        raw_input: str,
        *,
        cookie_file: str = "",
        browser_cookies: str = "",
    ) -> dict:
        """
        Fetch video metadata without downloading.
        Returns: {title, thumbnail, duration, id, uploader, url}
        """
        url = self.extract_url(raw_input)
        if not url:
            raise ValueError("No valid URL found in input.")

        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": False,
            "http_headers": self._default_headers(),
        }
        ffmpeg_dir = self._ffmpeg_location()
        if ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_dir

        resolved_cookie = self._resolve_cookie_file(cookie_file)
        if resolved_cookie:
            ydl_opts["cookiefile"] = resolved_cookie
        elif browser_cookies:
            ydl_opts["cookiesfrombrowser"] = (browser_cookies,)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise RuntimeError(f"Could not extract video metadata from: {url}")

            title = info.get("title", "video")
            thumbnail = info.get("thumbnail", "")
            duration = info.get("duration", 0)
            uploader = info.get("uploader", "") or info.get("channel", "")

            return {
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration,
                "id": info.get("id", ""),
                "uploader": uploader,
                "url": url,
            }

    def download_video(
        self,
        raw_input: str,
        *,
        output_dir: str = "",
        cookie_file: str = "",
        browser_cookies: str = "",
        progress_callback: Callable[[int, str, str], None] | None = None,
    ) -> tuple[str, dict]:
        """
        Download video to mp4 format with best video + best audio merged.
        progress_callback(percent: int, speed_str: str, message: str)
        Returns: (output_video_path, metadata)
        """
        url = self.extract_url(raw_input)
        if not url:
            raise ValueError("No valid URL found in input.")

        import yt_dlp

        target_dir = output_dir or self.download_dir
        os.makedirs(target_dir, exist_ok=True)

        downloaded_file = [""]


        def _progress_hook(d: dict):
            status = d.get("status")
            if status == "downloading":
                total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded_bytes = d.get("downloaded_bytes") or 0
                speed = d.get("speed") or 0

                percent = 0
                if total_bytes > 0:
                    percent = max(0, min(99, int((downloaded_bytes / total_bytes) * 100)))

                speed_str = ""
                if speed > 1024 * 1024:
                    speed_str = f"{speed / (1024 * 1024):.1f} MB/s"
                elif speed > 1024:
                    speed_str = f"{speed / 1024:.1f} KB/s"

                eta = d.get("eta")
                eta_str = f", ETA: {eta}s" if eta else ""
                msg = f"Downloading... ({speed_str}{eta_str})" if speed_str else "Downloading..."

                if progress_callback:
                    progress_callback(percent, speed_str, msg)

            elif status == "finished":
                filename = d.get("filename")
                if filename:
                    downloaded_file[0] = filename
                if progress_callback:
                    progress_callback(95, "", "Merging video and audio streams...")

        outtmpl = os.path.join(target_dir, "%(title).80B_%(id)s.%(ext)s")

        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "http_headers": self._default_headers(),
            "progress_hooks": [_progress_hook],
        }

        resolved_cookie = self._resolve_cookie_file(cookie_file)
        if resolved_cookie:
            ydl_opts["cookiefile"] = resolved_cookie
        elif browser_cookies:
            ydl_opts["cookiesfrombrowser"] = (browser_cookies,)

        ffmpeg_dir = self._ffmpeg_location()
        if ffmpeg_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_dir


        print(f"[VideoDownloader] Starting download for: {url}")
        if progress_callback:
            progress_callback(5, "", "Connecting to video server...")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError(f"Failed to download video from: {url}")

            final_path = ydl.prepare_filename(info)
            base, _ = os.path.splitext(final_path)
            mp4_path = f"{base}.mp4"
            if os.path.exists(mp4_path):
                final_path = mp4_path
            elif not os.path.exists(final_path) and downloaded_file[0] and os.path.exists(downloaded_file[0]):
                final_path = downloaded_file[0]

            if not os.path.exists(final_path):
                video_id = str(info.get("id", "") or "")
                if video_id:
                    for f in os.listdir(target_dir):
                        if video_id in f and f.endswith(".mp4"):
                            final_path = os.path.join(target_dir, f)
                            break

            if not os.path.exists(final_path):
                raise FileNotFoundError(f"Downloaded video file not found at {final_path}")

            if progress_callback:
                progress_callback(100, "", "Download complete!")

            print(f"[VideoDownloader] Download successful: {final_path}")
            return final_path, info
