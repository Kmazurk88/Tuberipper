"""Optional Discord message when a job finishes. Set DISCORD_WEBHOOK_URL to enable."""

import os
import traceback

import requests

COLOR_OK, COLOR_WARN, COLOR_FAIL = 0x2ECC71, 0xF1C40F, 0xE74C3C
MAX_LINES = 15


def configured() -> bool:
    return bool(os.environ.get("DISCORD_WEBHOOK_URL"))


def job_finished(job) -> None:
    """Posts a summary embed for a finished job. Never raises: a Discord
    outage must not affect the download."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not getattr(job, "notify", True):
        return
    if not url:
        return
    try:
        with job.lock:
            tracks = sorted(job.tracks, key=lambda t: t["index"])
            done = [t for t in tracks if t["status"] == "done"]
            existing = [t for t in tracks if t["status"] == "skipped_exists"]
            failed = [t for t in tracks if t["status"] == "failed"]
        if not (done or failed):
            return

        def label(t):
            m = t.get("match") or {}
            return f"{m.get('artist') or t.get('artist')} - {m.get('title') or t.get('title')}"

        lines = [f"✅ {label(t)}" for t in done[:MAX_LINES]]
        if len(done) > MAX_LINES:
            lines.append(f"…and {len(done) - MAX_LINES} more")
        lines += [f"❌ {label(t)}: {t.get('detail') or 'failed'}" for t in failed[:MAX_LINES]]
        if existing:
            lines.append(f"({len(existing)} already in library)")
        album = ((done[0].get("match") or {}).get("album") if done else "") or ""
        title = f"Added {len(done)} song{'s' if len(done) != 1 else ''}" + (f" · {album}" if album and len(done) > 1 else "")
        title = (f"[{os.environ['INSTANCE_LABEL'].strip()}] " if os.environ.get("INSTANCE_LABEL", "").strip() else "") + title
        color = COLOR_FAIL if failed and not done else COLOR_WARN if failed else COLOR_OK
        requests.post(url, json={"embeds": [{"title": title[:250], "description": "\n".join(lines)[:4000],
                                             "color": color}]}, timeout=10)
    except Exception:
        traceback.print_exc()
