"""Internship Radar — command line.

    python radar.py                 open the app (http://127.0.0.1:8765) and keep it refreshing
    python radar.py run             run the full agent pipeline once (used by the scheduled task)
    python radar.py status          print the current shortlist and pipeline numbers
    python radar.py schedule        install / remove the daily Windows scheduled task
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASK_NAME = "InternshipRadarDaily"
SENDER_TASK = "InternshipRadarSender"


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cmd_run(args: argparse.Namespace) -> int:
    from radar import pipeline

    agents = [item.strip() for item in args.agents.split(",")] if args.agents else None
    sources = [item.strip() for item in args.sources.split(",")] if args.sources else None
    result = pipeline.run_pipeline(agents, trigger=args.trigger, force=args.force, only_sources=sources)
    scout = result.get("scout") or {}
    print(f"\nRun {result.get('status')}: {scout.get('kept', 0)} matching offers, {scout.get('new', 0)} new.")
    for line in scout.get("new_titles", [])[:15]:
        print("  +", line)
    return 0 if result.get("status") in ("ok", "partial") else 1


def cmd_status(_: argparse.Namespace) -> int:
    from radar import db

    db.initialize()
    stats = db.one("SELECT count(*) AS total, sum(score >= 85) AS a, sum(score >= 72 AND score < 85) AS b FROM offers WHERE active = 1")
    print(f"Active offers: {stats['total']}  (grade A: {stats['a'] or 0}, grade B: {stats['b'] or 0})")
    print(f"Contacts: {db.one('SELECT count(*) AS n FROM contacts')['n']}   "
          f"Applications: {db.one('SELECT count(*) AS n FROM applications')['n']}   "
          f"Last run: {db.get_setting('last_run_at') or 'never'}")
    print("\nTop matches")
    for row in db.rows("SELECT score, title, org, country FROM offers WHERE active = 1 AND status IN ('new', 'shortlisted') "
                       "ORDER BY score DESC LIMIT 20"):
        print(f"  {row['score']:3d}  {row['title'][:70]} — {row['org']} ({row['country'] or 'remote'})")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from radar import server

    return server.serve(port=args.port, open_browser=not args.no_browser, autorun=not args.no_autorun)


def cmd_send_due(_: argparse.Namespace) -> int:
    """Send approved e-mails whose waiting time is over (run every 15 minutes by the sender task)."""
    from radar import db, mailer

    db.initialize()
    for outcome in mailer.send_due():
        print(f"sent to {outcome['to']} from {outcome['from']}" if outcome["sent"] else f"failed: {outcome['error']}")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.remove:
        codes = [subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], check=False).returncode for name in (TASK_NAME, SENDER_TASK)]
        return max(codes)
    if args.show:
        codes = [subprocess.run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"], check=False).returncode for name in (TASK_NAME, SENDER_TASK)]
        return max(codes)
    python = Path(sys.executable).with_name("pythonw.exe")
    python = python if python.exists() else Path(sys.executable)
    script = f'"{python}" "{ROOT / "radar.py"}"'
    daily = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", f"{script} run --trigger schedule",
                            "/SC", "DAILY", "/ST", args.time, "/F"], check=False).returncode
    sender = subprocess.run(["schtasks", "/Create", "/TN", SENDER_TASK, "/TR", f"{script} send-due",
                             "/SC", "MINUTE", "/MO", "15", "/F"], check=False).returncode
    if daily == 0 and sender == 0:
        print(f"Scheduled: the pipeline runs every day at {args.time}, and approved e-mails are sent within 15 minutes "
              "of their waiting time ending. Remove both with: python radar.py schedule --remove")
    return max(daily, sender)


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Internship Radar — autonomous 2027 PFE internship finder.")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="open the web app")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--no-autorun", action="store_true", help="do not refresh automatically while the app is open")
    run = commands.add_parser("run", help="run the agent pipeline once")
    run.add_argument("--agents", help="comma-separated subset: scout,analyst,contacts,directory,writer,followup,reporter")
    run.add_argument("--sources", help="comma-separated source-name prefixes, e.g. inria,cea,greenhouse:datadog")
    run.add_argument("--force", action="store_true", help="refresh contacts and directory links even if recent")
    run.add_argument("--trigger", default="cli")
    commands.add_parser("status", help="print the shortlist")
    commands.add_parser("send-due", help="send approved e-mails whose waiting time is over")
    schedule = commands.add_parser("schedule", help="install the Windows scheduled tasks (daily scan + e-mail sender)")
    schedule.add_argument("--time", default="07:30")
    schedule.add_argument("--remove", action="store_true")
    schedule.add_argument("--show", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        return cmd_run(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "send-due":
        return cmd_send_due(args)
    if args.command == "schedule":
        return cmd_schedule(args)
    if args.command is None:
        args = serve.parse_args([])
    return cmd_serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
