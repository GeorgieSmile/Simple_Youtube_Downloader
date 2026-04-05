import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

import yt_dlp

# ── Configuration ─────────────────────────────────────────────────────────────

CHANNELS_FILE      = "channels.txt"
COMPLETED_FILE     = "completed_channels.txt"
OUTPUT_DIR         = "downloads"
ARCHIVE_FILE       = "archive.txt"
LOG_FILE           = "downloader.log"
SLEEP_MIN          = 3       # min seconds between video downloads
SLEEP_MAX          = 10      # max seconds between video downloads
RATE_LIMIT         = 2 * 1024 * 1024  # bandwidth cap in bytes/sec (2 MB/s)
MAX_FILENAME_BYTES = 150     # max byte length for filenames
MIN_DURATION       = 120     # seconds — skip videos shorter than 2 min (Shorts)
MAX_DURATION       = 5400    # seconds — skip videos longer than 90 min
MAX_RETRIES        = 3       # attempts per channel before giving up
RETRY_WAIT         = 30      # seconds to wait between retry attempts

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Graceful shutdown ──────────────────────────────────────────────────────────

stop_ref = [False]  # mutable so match_filter can read it

def handle_sigint(_sig, _frame):
    stop_ref[0] = True
    log.warning("Stop signal received. Will stop after current download completes...")

signal.signal(signal.SIGINT, handle_sigint)

# ── Helpers ────────────────────────────────────────────────────────────────────

def load_completed_channels():
    if not os.path.exists(COMPLETED_FILE):
        return set()
    with open(COMPLETED_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}

def mark_channel_completed(channel):
    with open(COMPLETED_FILE, "a", encoding="utf-8") as f:
        f.write(channel + "\n")

def load_channels():
    if not os.path.exists(CHANNELS_FILE):
        log.error(f"{CHANNELS_FILE} not found.")
        sys.exit(1)
    channels = []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                channels.append(line)
    return channels

def build_channel_url(channel):
    if channel.startswith("http://") or channel.startswith("https://"):
        # Strip query params (e.g. ?si=...) and force the /videos tab
        parsed = urlparse(channel)
        path = parsed.path.rstrip("/")
        if not path.endswith("/videos"):
            path += "/videos"
        return urlunparse(("https", "www.youtube.com", path, "", "", ""))
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}/videos"
    return f"https://www.youtube.com/@{channel}/videos"

def truncate_title(info):
    """Trim info['title'] so the final filename stays within MAX_FILENAME_BYTES.

    The filename template is: "<title> [<id>].<ext>"
    We calculate how many bytes the suffix takes, then trim the title to fit.
    Decoding with errors='ignore' avoids splitting a multibyte character mid-byte.
    """
    title = info.get("title", "")
    video_id = info.get("id", "")
    # Suffix is always ASCII so byte count == char count
    suffix = f" [{video_id}].flac"
    max_title_bytes = MAX_FILENAME_BYTES - len(suffix)
    title_bytes = title.encode("utf-8")
    if len(title_bytes) > max_title_bytes:
        info["title"] = title_bytes[:max_title_bytes].decode("utf-8", errors="ignore")

def make_match_filter():
    """Filter videos by duration and stop cleanly when stop_ref is set."""
    def match_filter(info, *, incomplete):
        # Check stop before starting a new video download
        if stop_ref[0]:
            raise yt_dlp.utils.DownloadCancelled("Stop requested by user")
        # Trim title to respect MAX_FILENAME_BYTES (byte-aware, not char-aware)
        truncate_title(info)
        duration = info.get("duration")
        if duration is not None:
            try:
                duration = float(duration)
                if duration < MIN_DURATION:
                    return f"Duration {duration:.0f}s is under {MIN_DURATION}s (Shorts/too short)"
                if duration > MAX_DURATION:
                    return f"Duration {duration:.0f}s is over {MAX_DURATION}s (too long)"
            except (TypeError, ValueError):
                pass  # unknown duration — allow it
        return None
    return match_filter

def make_progress_hook(stats):
    """Return a yt-dlp progress hook that updates the given stats dict."""
    def hook(d):
        if d["status"] == "finished":
            stats["downloaded"] += 1
            title = d.get("info_dict", {}).get("title", d.get("filename", "unknown"))
            log.info(f"  [saved] {title}")
        elif d["status"] == "error":
            stats["errors"] += 1
    return hook

def make_postprocessor_hook(pp_in_progress):
    """Track the file currently being post-processed so we can clean it up on interrupt."""
    def hook(d):
        if d["status"] == "started":
            pp_in_progress["path"] = d.get("filepath")
        elif d["status"] in ("finished", "error"):
            pp_in_progress["path"] = None
    return hook

# ── yt-dlp options ─────────────────────────────────────────────────────────────

def make_ydl_opts(stats, pp_in_progress, since_days=None, browser=None):
    opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "flac"},
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "outtmpl": os.path.join(OUTPUT_DIR, "%(uploader)s", "%(title)s [%(id)s].%(ext)s"),
        "download_archive": ARCHIVE_FILE,
        "sleep_interval": SLEEP_MIN,
        "max_sleep_interval": SLEEP_MAX,
        "ratelimit": RATE_LIMIT,
        "match_filter": make_match_filter(),
        "ignoreerrors": True,
        "windowsfilenames": True,
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [make_progress_hook(stats)],
        "postprocessor_hooks": [make_postprocessor_hook(pp_in_progress)],
    }
    if browser is not None:
        opts["cookiesfrombrowser"] = (browser,)
        log.info(f"Using cookies from browser: {browser}")
    if since_days is not None:
        cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y%m%d")
        opts["dateafter"] = cutoff
        log.info(f"Incremental mode: only videos uploaded on or after {cutoff}")
    return opts

# ── Main ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Download YouTube channel audio as .flac files."
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        metavar="DAYS",
        help="Only download videos uploaded in the last N days (incremental mode).",
    )
    parser.add_argument(
        "--browser",
        type=str,
        default=None,
        metavar="BROWSER",
        help="Pass cookies from a browser to bypass bot detection (e.g. --browser firefox).",
    )
    return parser.parse_args()

def download_channel_with_retry(url, ydl_opts, pp_in_progress):
    """Try to download a channel up to MAX_RETRIES times. Returns True on success."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            return True
        except yt_dlp.utils.DownloadCancelled:
            # User pressed Ctrl+C — clean up any partial postprocessor output
            path = pp_in_progress.get("path")
            if path and os.path.exists(path):
                log.warning(f"  Removing incomplete file: {os.path.basename(path)}")
                os.remove(path)
            return True
        except Exception as e:
            if attempt < MAX_RETRIES:
                log.warning(f"  Attempt {attempt}/{MAX_RETRIES} failed: {e}")
                log.warning(f"  Retrying in {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)
            else:
                log.error(f"  All {MAX_RETRIES} attempts failed: {e}")
    return False

def main():
    args = parse_args()

    channels = load_channels()
    if not channels:
        log.info("No channels found in channels.txt. Add some and re-run.")
        return

    completed = load_completed_channels()
    total = len(channels)
    log.info(f"Starting — {total} channel(s) in list, {len(completed)} already completed.")
    if args.since:
        log.info(f"--since {args.since}: skipping videos older than {args.since} days.")
    print()

    total_downloaded = 0
    total_errors = 0

    for i, channel in enumerate(channels, start=1):
        if stop_ref[0]:
            log.info("Stopping as requested.")
            break

        if channel in completed:
            log.info(f"[{i}/{total}] Skipping (already completed): {channel}")
            continue

        url = build_channel_url(channel)
        log.info(f"[{i}/{total}] Processing: {channel}")

        # Per-channel state shared with progress/postprocessor hooks
        stats = {"downloaded": 0, "errors": 0}
        pp_in_progress = {"path": None}
        ydl_opts = make_ydl_opts(stats, pp_in_progress, since_days=args.since, browser=args.browser)

        success = download_channel_with_retry(url, ydl_opts, pp_in_progress)

        total_downloaded += stats["downloaded"]
        total_errors += stats["errors"]
        log.info(
            f"  Channel summary — downloaded: {stats['downloaded']}, errors: {stats['errors']}"
        )

        if success and not stop_ref[0]:
            mark_channel_completed(channel)
            log.info(f"  Channel complete: {channel}")

    print()
    log.info("─" * 50)
    log.info(f"Run complete — total downloaded: {total_downloaded}, total errors: {total_errors}")
    if stop_ref[0]:
        log.info("Stopped early. Re-run to continue where you left off.")
    log.info(f"Log saved to: {LOG_FILE}")

if __name__ == "__main__":
    main()
