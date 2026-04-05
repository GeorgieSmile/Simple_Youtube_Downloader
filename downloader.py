import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

import yt_dlp

# ── Configuration ─────────────────────────────────────────────────────────────

CHANNELS_FILE      = "channels.txt"
COMPLETED_FILE     = "completed_channels.txt"
OUTPUT_DIR         = "downloads"
ARCHIVE_FILE       = "archive.txt"
LOG_FILE           = "downloader.log"
SLEEP_MIN          = 3       # min seconds between video downloads
SLEEP_MAX          = 10      # max seconds between video downloads
RATE_LIMIT         = "2M"    # bandwidth cap (bytes/sec), e.g. "2M" = 2 MB/s
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

stop_requested = False

def handle_sigint(sig, frame):
    global stop_requested
    stop_requested = True
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
        return channel
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}/videos"
    return f"https://www.youtube.com/@{channel}/videos"

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

# ── yt-dlp options ─────────────────────────────────────────────────────────────

def make_ydl_opts(stats, since_days=None):
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
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration >= {MIN_DURATION} & duration <= {MAX_DURATION}"
        ),
        "ignoreerrors": True,
        "restrictfilenames": True,
        "trim_file_name": MAX_FILENAME_BYTES,
        "quiet": False,
        "no_warnings": False,
        "progress_hooks": [make_progress_hook(stats)],
    }
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
    return parser.parse_args()

def download_channel_with_retry(url, ydl_opts):
    """Try to download a channel up to MAX_RETRIES times. Returns True on success."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
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
        if stop_requested:
            log.info("Stopping as requested.")
            break

        if channel in completed:
            log.info(f"[{i}/{total}] Skipping (already completed): {channel}")
            continue

        url = build_channel_url(channel)
        log.info(f"[{i}/{total}] Processing: {channel}")

        # Per-channel stats dict shared with the progress hook
        stats = {"downloaded": 0, "errors": 0}
        ydl_opts = make_ydl_opts(stats, since_days=args.since)

        success = download_channel_with_retry(url, ydl_opts)

        total_downloaded += stats["downloaded"]
        total_errors += stats["errors"]
        log.info(
            f"  Channel summary — downloaded: {stats['downloaded']}, errors: {stats['errors']}"
        )

        if success and not stop_requested:
            mark_channel_completed(channel)
            log.info(f"  Channel complete: {channel}")

    print()
    log.info("─" * 50)
    log.info(f"Run complete — total downloaded: {total_downloaded}, total errors: {total_errors}")
    if stop_requested:
        log.info("Stopped early. Re-run to continue where you left off.")
    log.info(f"Log saved to: {LOG_FILE}")

if __name__ == "__main__":
    main()
