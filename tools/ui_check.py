#!/usr/bin/env python3
"""浏览器端冒烟测试（开发用）。

依赖 Playwright，并使用系统已安装的 Edge（channel="msedge"），不需要额外下载浏览器::

    python tools/ui_check.py --url http://127.0.0.1:8091 --shots ./screenshots

会依次验证：概览加载、音乐库列表、播放器/歌词、单曲抽屉、候选弹窗、批量修复任务，
并收集控制台错误。仅用于开发验收，不打包进安装包。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print("需要 playwright：pip install playwright", file=sys.stderr)
    raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8091")
    parser.add_argument("--shots", default="screenshots")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--skip-network", action="store_true", help="跳过候选检索与修复任务（较慢）")
    args = parser.parse_args()

    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    errors: list = []
    summary: dict = {"screenshots": [], "lyrics_lines": 0, "job": None, "console_errors": errors}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=not args.headed)
        page = browser.new_page(viewport={"width": 1806, "height": 959}, device_scale_factor=1)
        page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type in ("error",) else None)
        page.on("pageerror", lambda exc: errors.append(f"pageerror:{exc}"))

        page.goto(args.url, wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(path=str(shots / "ui-dashboard.png"))
        summary["screenshots"].append("ui-dashboard.png")
        print("dashboard:", page.title())

        page.goto(f"{args.url}/#/library", wait_until="networkidle")
        page.wait_for_selector(".track-row", timeout=15000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(shots / "ui-library.png"))
        summary["screenshots"].append("ui-library.png")
        rows = page.locator(".track-row").count()
        summary["rows"] = rows
        print("library rows:", rows)

        # 播放一首有歌词的曲目
        page.locator(".track-row").nth(5).locator(".track-play").click()
        page.wait_for_timeout(1800)
        playing = page.locator("#pbTitle").inner_text()
        summary["playing"] = playing
        print("playing:", playing)

        # 打开正在播放
        page.locator("[data-action='open-player']").first.click()
        page.wait_for_timeout(1200)
        lines = page.locator(".lyric-line").count()
        summary["lyrics_lines"] = lines
        print("lyrics lines:", lines)
        page.screenshot(path=str(shots / "ui-player.png"))
        summary["screenshots"].append("ui-player.png")
        page.locator("[data-action='close-player']").click()
        page.wait_for_timeout(400)

        # 单曲抽屉
        page.locator(".track-row").nth(1).click()
        page.wait_for_selector(".td-title", timeout=10000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(shots / "ui-track.png"))
        summary["screenshots"].append("ui-track.png")
        summary["track_title"] = page.locator(".td-title").inner_text()
        page.locator("[data-action='close-track']").click()
        page.wait_for_timeout(300)

        if not args.skip_network:
            # 候选弹窗（封面）
            page.locator(".track-row").first.locator("[data-action='candidates']").click()
            page.wait_for_selector("#candidateBody", timeout=10000)
            try:
                page.wait_for_selector(".candidate", timeout=40000)
            except Exception:
                print("candidates: none returned")
            page.wait_for_timeout(500)
            page.screenshot(path=str(shots / "ui-candidates.png"))
            summary["screenshots"].append("ui-candidates.png")
            summary["candidates"] = page.locator(".candidate").count()
            page.locator("[data-action='close-modal']").first.click()
            page.wait_for_timeout(300)

            # 批量修复：只预览 1 首
            page.goto(f"{args.url}/#/repair", wait_until="networkidle")
            page.wait_for_selector("#repairDry", timeout=10000)
            page.locator("#scopeChips [data-scope='missing_any']").click()
            page.locator("#repairDry").check()
            page.locator("#repairLimit").fill("1")
            page.locator("[data-action='start-batch']").click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(shots / "ui-repair-job.png"))
            summary["screenshots"].append("ui-repair-job.png")
            try:
                page.wait_for_function(
                    "() => { const el = document.querySelector('.job-sub'); return el && /已完成|失败|已取消/.test(el.textContent); }",
                    timeout=120000,
                )
            except Exception:
                print("job: timeout")
            summary["job"] = page.locator(".job-sub").inner_text() if page.locator(".job-sub").count() else ""
            print("job:", summary["job"])

        # 移动端视图
        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
        mobile.goto(args.url, wait_until="networkidle")
        mobile.wait_for_timeout(1000)
        mobile.screenshot(path=str(shots / "ui-mobile.png"))
        summary["screenshots"].append("ui-mobile.png")

        browser.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
