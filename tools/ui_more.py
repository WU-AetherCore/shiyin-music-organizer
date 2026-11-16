#!/usr/bin/env python3
"""补充 UI 验收：文件夹选择、设置页、候选弹窗与队列抽屉。开发用。"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--shots", default="screenshots")
    args = parser.parse_args()
    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1806, "height": 959})
        page.on("console", lambda m: errors.append(f"{m.type}:{m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror:{e}"))
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_timeout(900)
        page.screenshot(path=str(shots / "final-dashboard.png"))

        # 文件夹选择器
        page.locator("[data-action='open-folder']").first.click()
        page.wait_for_selector(".folder-current", timeout=10000)
        page.wait_for_timeout(700)
        page.screenshot(path=str(shots / "final-folder.png"))
        page.locator("[data-action='close-modal']").first.click()
        page.wait_for_timeout(300)

        # 设置
        page.goto(f"{args.url}/#/settings", wait_until="networkidle")
        page.wait_for_selector("[data-setting='repair.min_cover_size']", timeout=15000)
        page.wait_for_timeout(700)
        page.screenshot(path=str(shots / "final-settings.png"))
        before = page.locator("[data-setting='repair.min_cover_size']").input_value()
        page.locator("[data-setting='repair.min_cover_size']").fill("320")
        page.locator("[data-action='save-settings']").click()
        page.wait_for_timeout(1600)
        page.screenshot(path=str(shots / "final-settings-saved.png"))
        # 还原
        page.locator("[data-setting='repair.min_cover_size']").fill(before or "300")
        page.locator("[data-action='save-settings']").click()
        page.wait_for_timeout(1200)

        # 搜索并打开候选
        page.goto(f"{args.url}/#/library", wait_until="networkidle")
        page.wait_for_selector(".track-row", timeout=15000)
        page.locator("#globalSearch").fill("晴天")
        page.wait_for_timeout(1200)
        page.locator(".track-row").first.locator("[data-action='candidates']").click()
        page.wait_for_selector("#candidateBody", timeout=10000)
        try:
            page.wait_for_selector(".candidate", timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(700)
        page.screenshot(path=str(shots / "final-candidates.png"))
        count = page.locator(".candidate").count()
        page.locator("[data-action='close-modal']").first.click()
        page.wait_for_timeout(300)

        # 队列抽屉
        page.locator("[data-action='open-queue']").first.click()
        page.wait_for_timeout(700)
        page.screenshot(path=str(shots / "final-queue.png"))
        page.locator("[data-action='close-queue']").first.click()

        # 修复中心
        page.goto(f"{args.url}/#/repair", wait_until="networkidle")
        page.wait_for_selector("#repairDry", timeout=10000)
        page.wait_for_timeout(900)
        page.screenshot(path=str(shots / "final-repair.png"))

        # 移动端（普通 viewport，确保底栏在截图内）
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        mobile.goto(f"{args.url}/#/library", wait_until="networkidle")
        mobile.wait_for_selector(".track-row", timeout=15000)
        mobile.wait_for_timeout(800)
        mobile.screenshot(path=str(shots / "final-mobile.png"))
        mobile.locator(".track-row .track-play").first.click()
        mobile.wait_for_timeout(1200)
        mobile.locator("[data-action='open-player']").first.click()
        mobile.wait_for_timeout(900)
        mobile.screenshot(path=str(shots / "final-mobile-player.png"))

        browser.close()
    print("candidates:", count)
    print("errors:", errors)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
