# Simple YouTube Downloader

Downloads all videos from a list of YouTube channels and saves them as `.flac` audio files. Built to run unattended for hours on a home connection — handles resuming, graceful stops, and rate limiting.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) installed and on your PATH
- [uv](https://github.com/astral-sh/uv) (or plain pip)

## Setup

```bash
# Create and activate virtual environment
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
uv pip install "yt-dlp[default]"
```

## Usage

**1. Add channels to `channels.txt`** — one per line, `#` for comments:

```
# Music channels
@LinusTechTips
https://www.youtube.com/@mkbhd
```

**2. Run the downloader:**

```bash
# Download everything
python downloader.py

# Incremental — only videos from the last 30 days
python downloader.py --since 30
```

**3. Check the log** at any time:

```bash
cat downloader.log
```

## Output structure

```
downloads/
└── ChannelName/
    └── Video_Title [videoID].flac
```

Metadata (title, channel, upload date) is embedded in every `.flac` file.

## How resuming works

Two files track progress automatically — you never need to edit them:

| File | Tracks |
|---|---|
| `archive.txt` | Every downloaded video ID. Already-downloaded videos are skipped. |
| `completed_channels.txt` | Channels fully processed. Skipped entirely on next run. |

If the script is stopped mid-channel, re-running it picks up from the first un-downloaded video in that channel.

## Configuration

All settings are constants at the top of `downloader.py`:

| Constant | Default | Description |
|---|---|---|
| `SLEEP_MIN` / `SLEEP_MAX` | 3 / 10 s | Random delay between downloads |
| `RATE_LIMIT` | `2 * 1024 * 1024` | Bandwidth cap (2 MB/s) |
| `MIN_DURATION` | 120 s | Skip videos shorter than 2 min (Shorts) |
| `MAX_DURATION` | 5400 s | Skip videos longer than 90 min |
| `MAX_RETRIES` | 3 | Retry attempts per channel on failure |
| `RETRY_WAIT` | 30 s | Wait between retry attempts |
| `MAX_FILENAME_BYTES` | 150 | Max filename length in bytes |

## Stopping safely

Press `Ctrl+C` at any time. The script will finish the current download, then exit cleanly. Re-run to continue.

## Files created at runtime

| File | Purpose |
|---|---|
| `archive.txt` | yt-dlp video ID archive |
| `completed_channels.txt` | Fully processed channels |
| `downloader.log` | Timestamped log of every run |
| `downloads/` | Output audio files |
