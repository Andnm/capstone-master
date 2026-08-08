"""Artifact opt-in: chỉ ghi HTML/screenshot khi run yêu cầu rõ ràng."""
import gzip
from pathlib import Path
from typing import Dict, Optional


def save_page_artifacts(driver, artifact_root: str, run_id: int, item_id: int) -> Dict[str, Optional[str]]:
    target = Path(artifact_root).resolve() / str(run_id) / str(item_id)
    target.mkdir(parents=True, exist_ok=True)
    html_path = target / "page.html.gz"
    screenshot_path = target / "page.png"
    html_path.write_bytes(gzip.compress((driver.page_source or "").encode("utf-8"), compresslevel=6))
    driver.save_screenshot(str(screenshot_path))
    return {"artifact_html_path": str(html_path), "screenshot_path": str(screenshot_path)}
