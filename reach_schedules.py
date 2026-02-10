"""
Reach-at-time: schedule notifications to reach the user at set times (e.g. via Telegram).
Schedules are stored in ~/.cursor-enhanced/reach-schedules.json.
Use system cron to run `cursor-enhanced reach-fire` every minute to fire due schedules.
"""

import os
import json
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("cursor_enhanced.reach")

REACH_SCHEDULES_FILE = os.path.expanduser("~/.cursor-enhanced/reach-schedules.json")


def _load_schedules() -> Dict[str, Any]:
    path = REACH_SCHEDULES_FILE
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "schedules" in data:
                    return data
        except Exception as e:
            logger.warning(f"Failed to load reach schedules: {e}")
    return {"schedules": []}


def _save_schedules(data: Dict[str, Any]) -> None:
    path = REACH_SCHEDULES_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def list_schedules() -> List[Dict[str, Any]]:
    data = _load_schedules()
    return data.get("schedules") or []


def _get_tz(tz_name: Optional[str]):
    """Return ZoneInfo for tz_name or UTC if None/invalid."""
    if not (tz_name and str(tz_name).strip()):
        return timezone.utc
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(tz_name.strip())
    except Exception:
        return timezone.utc


def add_schedule(
    *,
    time: Optional[str] = None,
    cron: Optional[str] = None,
    once_at: Optional[str] = None,
    message: str,
    channel: str = "telegram",
    timezone_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a reach schedule. Set one of: time (HH:MM daily), cron (5-field), or once_at (ISO datetime UTC)."""
    if not (time or cron or once_at):
        raise ValueError("One of --time HH:MM, --cron '0 9 * * *', or --reach-in-minutes N / --reach-once-at ISO is required")
    if not message or not message.strip():
        raise ValueError("--message is required")
    data = _load_schedules()
    schedules = data.get("schedules") or []
    entry = {
        "id": str(uuid.uuid4()),
        "time": time.strip() if time else None,
        "cron": cron.strip() if cron else None,
        "once_at": once_at.strip() if once_at else None,
        "message": message.strip(),
        "channel": channel.strip() or "telegram",
        "enabled": True,
    }
    if timezone_name and str(timezone_name).strip():
        entry["timezone"] = str(timezone_name).strip()
    schedules.append(entry)
    data["schedules"] = schedules
    _save_schedules(data)
    return entry


def remove_schedule(schedule_id: str) -> bool:
    data = _load_schedules()
    schedules = data.get("schedules") or []
    new_list = [s for s in schedules if s.get("id") != schedule_id]
    if len(new_list) == len(schedules):
        return False
    data["schedules"] = new_list
    _save_schedules(data)
    return True


def _parse_time(hhmm: str) -> Optional[tuple]:
    """Return (hour, minute) or None."""
    hhmm = (hhmm or "").strip()
    if not hhmm:
        return None
    parts = hhmm.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)
    except ValueError:
        pass
    return None


def _is_due_time(now_in_tz: datetime, time_str: str) -> bool:
    """True if time_str is HH:MM and matches current hour:minute in the given time."""
    parsed = _parse_time(time_str)
    if not parsed:
        return False
    h, m = parsed
    return now_in_tz.hour == h and now_in_tz.minute == m


def _is_due_cron(now_in_tz: datetime, cron_expr: str) -> bool:
    """True if cron expression is due at this minute in the given time (uses croniter)."""
    try:
        import croniter
    except ImportError:
        logger.warning("croniter not installed; cron schedules need: pip install croniter")
        return False
    try:
        # Use naive minute in the schedule's timezone for croniter.
        minute_start = now_in_tz.replace(second=0, microsecond=0)
        if minute_start.tzinfo is not None:
            minute_start = minute_start.replace(tzinfo=None)
        base = minute_start - timedelta(seconds=1)
        it = croniter.croniter(cron_expr, base)
        next_run = it.get_next(datetime)
        return next_run == minute_start.replace(second=0, microsecond=0)
    except Exception as e:
        logger.warning(f"Invalid cron '{cron_expr}': {e}")
        return False


def _parse_once_at(once_at_str: str) -> Optional[datetime]:
    """Parse ISO datetime string to UTC datetime. Returns None on parse error."""
    once_at_str = (once_at_str or "").strip()
    if not once_at_str:
        return None
    try:
        if once_at_str.endswith("Z"):
            dt = datetime.fromisoformat(once_at_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(once_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError) as e:
        logger.warning("Invalid once_at %r: %s", once_at_str, e)
        return None


def get_due_schedules(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Return schedules that are due at the given time (default: now). One-shot uses UTC; daily/cron use schedule timezone or UTC."""
    now_utc = now if now is not None else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    data = _load_schedules()
    schedules = data.get("schedules") or []
    due = []
    for s in schedules:
        if not s.get("enabled", True):
            continue
        if s.get("once_at"):
            once_dt = _parse_once_at(s["once_at"])
            if once_dt is not None and now_utc >= once_dt:
                due.append(s)
        elif s.get("time"):
            tz = _get_tz(s.get("timezone"))
            now_in_tz = now_utc.astimezone(tz)
            if _is_due_time(now_in_tz, s["time"]):
                due.append(s)
        elif s.get("cron"):
            tz = _get_tz(s.get("timezone"))
            now_in_tz = now_utc.astimezone(tz)
            if _is_due_cron(now_in_tz, s["cron"]):
                due.append(s)
    return due


def fire_due_schedules(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Find due schedules, send messages (e.g. Telegram), and return list of fired entries.
    One-shot (once_at) schedules are removed after firing.
    Call this from `reach-fire` CLI (e.g. from cron every minute).
    """
    due = get_due_schedules(now)
    fired = []
    for s in due:
        channel = (s.get("channel") or "telegram").lower()
        message = (s.get("message") or "").strip()
        if not message:
            continue
        if channel == "telegram":
            try:
                from telegram_integration import send_to_paired_users
                if send_to_paired_users(message):
                    fired.append(s)
                    logger.info("Reach fired: %s -> telegram", s.get("id"))
                else:
                    logger.warning("Reach fired but no Telegram delivery: %s", s.get("id"))
            except Exception as e:
                logger.error("Reach Telegram send failed: %s", e)
        else:
            logger.warning("Reach channel not supported: %s", channel)
    # Remove one-shot schedules after sending so they only fire once
    for s in fired:
        if s.get("once_at"):
            remove_schedule(s["id"])
            logger.info("Reach one-shot removed: %s", s.get("id"))
    return fired


async def fire_due_schedules_async(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Async version: find due reach schedules, send via Telegram (async), remove one-shots.
    Use this from the Telegram bot's scheduler so reminders fire without cron.
    """
    due = get_due_schedules(now)
    fired = []
    for s in due:
        channel = (s.get("channel") or "telegram").lower()
        message = (s.get("message") or "").strip()
        if not message:
            continue
        if channel == "telegram":
            try:
                from telegram_integration import send_to_paired_users_async
                if await send_to_paired_users_async(message):
                    fired.append(s)
                    logger.info("Reach fired: %s -> telegram", s.get("id"))
                else:
                    logger.warning("Reach fired but no Telegram delivery: %s", s.get("id"))
            except Exception as e:
                logger.error("Reach Telegram send failed: %s", e)
        else:
            logger.warning("Reach channel not supported: %s", channel)
    for s in fired:
        if s.get("once_at"):
            remove_schedule(s["id"])
            logger.info("Reach one-shot removed: %s", s.get("id"))
    return fired
