#!/usr/bin/env python3
"""Build a self-contained synthetic demo without accessing any personal vault."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
def main():
    dest = ROOT / ".local" / "demo"
    shutil.copytree(ROOT / "工具", dest / "工具", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.json", "dashboard.html", "board.html", "out", "raw", "logs", "manual", "ref"))
    data = {"updated": "2026-10-27", "gmail_account": "", "applications": [
      {"id": "example-a", "company": "Example Research", "req": "DEMO-01", "title": "Research Scientist", "channel": "官网直投", "events": [
        {"date": "2026-10-19", "type": "applied", "summary": "Synthetic application", "source": ""},
        {"date": "2026-10-23", "type": "assessment", "summary": "Assessment invitation; completion unknown", "source": ""}],
        "next": {"date": "2026-10-29", "action": "Review the synthetic assessment invitation"},
        "submitted": {"resume": "demo-resume.pdf", "desired_salary": "未问"}},
      {"id": "example-b", "company": "Example Systems", "req": "DEMO-02", "title": "Machine Learning Engineer", "channel": "内推", "events": [
        {"date": "2026-10-20", "type": "referral_request", "summary": "Synthetic referral request", "source": ""}]},
      {"id": "example-c", "company": "Example Analytics", "req": "DEMO-03", "title": "Data Scientist", "channel": "官网直投", "events": [
        {"date": "2026-10-15", "type": "applied", "summary": "Synthetic application", "source": ""},
        {"date": "2026-10-22", "type": "rejected", "summary": "Synthetic rejection", "source": ""}],
        "submitted": {"resume": "demo-resume.pdf", "desired_salary": "未问"}}]}
    folder = dest / "工具" / "求职进度面板"
    (folder / "applications.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    subprocess.run([sys.executable, str(folder / "make_dashboard.py"), "--today", "2026-10-27"], check=True)
    print(folder / "dashboard.html")
if __name__ == "__main__":
    main()
