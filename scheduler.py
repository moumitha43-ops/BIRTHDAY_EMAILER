"""
scheduler.py — APScheduler wrapper for daily birthday auto-send
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from helpers import cfg_get, cfg_set, logger, get_todays_birthdays
from send import send_all

_scheduler: BackgroundScheduler | None = None
JOB_ID = "daily_birthday"


def _daily_job():
    logger.info("=== Auto-scheduler: daily birthday run starting ===")
    try:
        matches = get_todays_birthdays()
        if not matches:
            logger.info("Auto-scheduler: no birthdays today.")
            return
        logger.info("Auto-scheduler: %d match(es) today.", len(matches))
        result = send_all(matches)
        logger.info(
            "Auto-scheduler done — %d sent, %d failed, %d skipped.",
            len(result.get("sent", [])),
            len(result.get("failed", [])),
            len(result.get("skipped", [])),
        )
    except Exception as e:
        logger.error("Auto-scheduler error: %s", e)


def start_scheduler():
    global _scheduler

    try:
        _scheduler = BackgroundScheduler()
        auto  = cfg_get("auto_send", "false").lower() == "true"
        t     = cfg_get("send_time", "08:00")
        tz    = cfg_get("timezone",  "Asia/Kolkata")

        if auto:
            h, m = map(int, t.split(":"))
            _scheduler.add_job(
                _daily_job,
                CronTrigger(hour=h, minute=m, timezone=tz),
                id=JOB_ID,
                replace_existing=True,
            )
            logger.info("Scheduler started — auto-send at %s (%s)", t, tz)
        else:
            logger.info("Scheduler started — auto-send disabled")

        _scheduler.start()
    except Exception as e:
        logger.error("Scheduler failed to start: %s", e)


def update_schedule(send_time=None, auto_send=None, timezone=None):
    global _scheduler

    if send_time  is not None: cfg_set("send_time",  send_time)
    if auto_send  is not None: cfg_set("auto_send",  "true" if auto_send else "false")
    if timezone   is not None: cfg_set("timezone",   timezone)

    if _scheduler is None:
        return

    auto = cfg_get("auto_send", "false").lower() == "true"
    t    = cfg_get("send_time", "08:00")
    tz   = cfg_get("timezone",  "Asia/Kolkata")

    try:
        if auto:
            h, m = map(int, t.split(":"))
            _scheduler.add_job(
                _daily_job,
                CronTrigger(hour=h, minute=m, timezone=tz),
                id=JOB_ID,
                replace_existing=True,
            )
            logger.info("Schedule updated — auto-send at %s (%s)", t, tz)
        else:
            if _scheduler.get_job(JOB_ID):
                _scheduler.remove_job(JOB_ID)
            logger.info("Auto-send disabled")
    except Exception as e:
        logger.error("Failed to update schedule: %s", e)


def get_next_run() -> str:
    if _scheduler:
        job = _scheduler.get_job(JOB_ID)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%d %b %Y, %I:%M %p %Z")
    return "Not scheduled"
