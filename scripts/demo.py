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
    # Every record below is fictional and deliberately exercises a different state.
    for number, day, result in [(4, "2026-08-12", None), (5, "2026-09-03", "closed"),
                                (6, "2026-09-16", "rejected"), (7, "2026-10-06", "human_reply"),
                                (8, "2026-10-13", "interview"), (9, "2026-10-21", "offer")]:
        events = [{"date": day, "type": "applied", "summary": "Synthetic application", "source": ""}]
        if result:
            events.append({"date": "2026-10-24", "type": result, "summary": "Synthetic " + result + " record", "source": ""})
        row = {"id": f"example-{number}", "company": f"Example Studio {number}", "req": f"DEMO-{number:02d}",
               "title": "Applied Scientist", "channel": "官网直投", "events": events,
               "submitted": {"resume": "demo-resume.pdf", "desired_salary": "未问"}}
        if result == "interview":
            row["next"] = {"date": "2026-10-30", "action": "Prepare the fictional interview exercise"}
        data["applications"].append(row)
    data["applications"].append({"id": "example-receipt", "company": "Example Platform", "req": "DEMO-10",
        "title": "ML Engineer", "channel": "官网直投", "events": [
            {"date": "2026-10-26", "type": "ack", "summary": "Synthetic receipt; actual submission date unknown", "source": ""}],
        "submitted": {"resume": "demo-resume.pdf", "desired_salary": "未问"}})
    data["applications"].append({"id": "example-undated-outcome", "company": "Example Systems", "req": "DEMO-11",
        "title": "Research Engineer", "channel": "内推", "stage": "closed", "events": [
            {"date": "2026-10-20", "type": "referral_request", "summary": "Synthetic referral, manual closure date unknown", "source": ""}]})
    folder = dest / "工具" / "求职进度面板"
    def save(name, value):
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    save("applications.json", data)
    save("targets.json", {"targets": [
        {"id": "candidate-a", "company": "Example Imaging", "req": "CAND-01", "title": "Imaging Research Scientist",
         "tier": "🥇 投", "location": "Example City", "url": "https://example.org/jobs/CAND-01", "added": "2026-10-26",
         "posted": "2026-10-25", "deadline": "2026-10-31", "pay": "$120,000–$155,000 (fictional)",
         "pay_low": 120000, "pay_high": 155000, "elig": "UNAUDITED", "elig_line": "Synthetic role; qualification review is not complete.",
         "action": "Review the synthetic requirements", "line": "Demo opportunity with explicit unknown conditions."},
        {"id": "candidate-b", "company": "Example Data", "req": "CAND-02", "title": "Data Scientist",
         "tier": "🟡 条件投", "location": "Example City", "url": "https://example.org/jobs/CAND-02", "added": "2026-10-24",
         "elig": "UNAUDITED", "elig_line": "Confirm experience requirements before applying.", "action": "Decide whether to pursue this fictional role"}]})
    save("manual_followups.json", {"records": [{"target_id": "candidate-b", "company": "Example Data", "req": "CAND-02",
        "disposition": "user_confirmation", "completed": ["Synthetic role summary prepared"],
        "needs_user": [{"question": "Would you consider this fictional location?"}], "needs_employer": [], "materials": []}]})
    save("internships.json", {"updated": "2026-10-27", "items": [{"id": "intern-example", "company": "Example Labs",
        "req": "INT-01", "title": "PhD Research Intern", "url": "https://example.org/jobs/INT-01", "state": "conditional",
        "group": "primary", "start": "Spring 2027 (fictional)", "duration": "12 weeks", "enrollment": "Must be enrolled; synthetic example",
        "return_to_school": "Unknown", "opt_status": "unconfirmed", "opt_note": "No eligibility conclusion is implied by this demo.",
        "location": "Example City", "next_step": "Confirm the synthetic eligibility conditions", "sources": []}]})
    subprocess.run([sys.executable, str(folder / "make_dashboard.py"), "--today", "2026-10-27"], check=True)
    print(folder / "dashboard.html")
if __name__ == "__main__":
    main()
