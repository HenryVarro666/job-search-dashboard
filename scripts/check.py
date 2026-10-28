#!/usr/bin/env python3
"""Run source-only regressions and a synthetic build; never reads a personal vault."""
from pathlib import Path
from html.parser import HTMLParser
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "工具" / "求职进度面板"

def run(args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)

class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(); self.scripts = []; self.current = None
    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") != "application/json": self.current = ""
    def handle_data(self, data):
        if self.current is not None: self.current += data
    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append(self.current); self.current = None

def main():
    run([sys.executable, "-B", "-m", "unittest", "discover", "-p", "test_*.py"], PANEL)
    for path in sorted(PANEL.glob("test_*.cjs")): run(["node", str(path)], PANEL)
    run([sys.executable, "scripts/demo.py"])
    page = ROOT / ".local/demo/工具/求职进度面板/dashboard.html"
    text = page.read_text(encoding="utf-8")
    for marker in ("__DATA_JSON__", "__CHART_MODELS_JS__", "__COMPANY_FILTER_JS__", "__COMPANY_FILTER_CSS__"):
        if marker in text: raise AssertionError("Unfilled build marker: " + marker)
    parser = Scripts(); parser.feed(text)
    with tempfile.TemporaryDirectory() as directory:
        for i, script in enumerate(parser.scripts):
            path = Path(directory) / f"inline-{i}.js"; path.write_text(script)
            run(["node", "--check", str(path)])
    run(["node", "scripts/dom-smoke.cjs"])
    print("Synthetic regressions, build, JavaScript syntax and DOM checks passed.")
if __name__ == "__main__": main()
