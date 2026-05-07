from pathlib import Path

DASHBOARD_HTML = (Path(__file__).parent / "static" / "dashboard.html").read_text()
