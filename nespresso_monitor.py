"""
네스프레소 이벤트 카드 변경 모니터
- 변경 감지 시: 날짜 폴더 생성 → 스크린샷 저장 → Google Sheets 업데이트 → Slack 알림
- 매일 오전 9시 실행 (Windows Task Scheduler)
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import gspread
import requests as http_req
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from playwright.async_api import async_playwright

from nespresso_crawler import crawl

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 설정 ──────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent
load_dotenv(_BASE / "config.env")

SLACK_WEBHOOK_URL   = os.getenv("SLACK_WEBHOOK_URL", "")
GOOGLE_CREDS_PATH   = Path(os.getenv("GOOGLE_CREDENTIALS_PATH", str(_BASE / "credentials.json")))
SPREADSHEET_ID      = "1yedWS5jNzsd7C0W6f6t3Ma03gY2icOjZaQ31ksiDxkM"
DRIVE_FOLDER_ID     = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "14sSA2L8Uv1_Nhhhpz3acBQ7FL4tgNXTq")
DATA_DIR            = _BASE / os.getenv("DATA_DIR", "data")
LAST_RESULTS_FILE   = DATA_DIR / "last_results.json"


# ── 유틸 ──────────────────────────────────────────────────────────────────────
def safe_name(text: str, max_len: int = 40) -> str:
    """파일/폴더명에 사용 불가한 문자 제거"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:max_len]


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ── 변경 감지 ─────────────────────────────────────────────────────────────────
def detect_changes(old: list, new: list) -> dict:
    """이전/현재 카드 목록 비교 → 추가 / 삭제 / 수정 분류"""
    old_map = {c["title"]: c for c in old}
    new_map = {c["title"]: c for c in new}

    added   = [c for c in new if c["title"] not in old_map]
    removed = [c for c in old if c["title"] not in new_map]

    modified = []
    for title, nc in new_map.items():
        if title not in old_map:
            continue
        oc   = old_map[title]
        diff = []
        if oc.get("image") != nc.get("image"):
            diff.append("이미지")
        if oc.get("cta", {}).get("url") != nc.get("cta", {}).get("url"):
            diff.append("CTA URL")
        if oc.get("cta", {}).get("text") != nc.get("cta", {}).get("text"):
            diff.append("CTA 텍스트")
        if diff:
            modified.append({"card": nc, "old": oc, "diff": diff})

    return {"added": added, "removed": removed, "modified": modified}


def has_any_change(changes: dict) -> bool:
    return any(changes[k] for k in ("added", "removed", "modified"))


# ── 스크린샷 ──────────────────────────────────────────────────────────────────
async def capture_all(cards: list, folder: Path):
    """
    모든 카드에 대해:
      1. 카드 이미지 파일 다운로드
      2. CTA URL 페이지 전체 스크린샷
    """
    folder.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        _headless = os.getenv("HEADLESS", "false").lower() == "true"
        browser = await pw.chromium.launch(
            headless=_headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
                "--window-size=1920,1080",
            ],
        )
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        for i, card in enumerate(cards):
            prefix = f"{i + 1:02d}_{safe_name(card['title'])}"

            # 1) 카드 이미지 다운로드 (Playwright 컨텍스트 요청 — 봇 차단 우회)
            img_url = card.get("image", "")
            if img_url:
                try:
                    resp = await ctx.request.get(img_url, timeout=30_000)
                    if resp.ok:
                        ext = img_url.split("?")[0].rsplit(".", 1)[-1].lower()
                        ext = ext if ext in {"jpg", "jpeg", "png", "webp", "gif"} else "jpg"
                        path = folder / f"card_{prefix}.{ext}"
                        path.write_bytes(await resp.body())
                        log(f"  [card image] {path.name}")
                    else:
                        log(f"  [card image] HTTP {resp.status} — {card['title']!r}")
                except Exception as e:
                    log(f"  [card image] 실패 — {card['title']!r}: {e}")

            # 2) 상세 페이지 전체 스크린샷
            detail_url = card.get("cta", {}).get("url", "")
            if detail_url:
                try:
                    await page.goto(detail_url, wait_until="domcontentloaded", timeout=30_000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=12_000)
                    except Exception:
                        await page.wait_for_timeout(3_000)

                    # 쿠키 팝업 닫기
                    for sel in ["#onetrust-accept-btn-handler", "button[id*='accept']"]:
                        try:
                            btn = page.locator(sel).first
                            if await btn.count() > 0:
                                await btn.click(timeout=2_000)
                                await page.wait_for_timeout(500)
                                break
                        except Exception:
                            pass

                    # lazy-load 트리거
                    for _ in range(4):
                        await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        await page.wait_for_timeout(400)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(600)

                    path = folder / f"detail_{prefix}.png"
                    await page.screenshot(path=str(path), full_page=True)
                    log(f"  [detail shot] {path.name}")
                except Exception as e:
                    log(f"  [detail shot] 실패 — {card['title']!r}: {e}")

        await browser.close()


# ── Google Drive ──────────────────────────────────────────────────────────────
_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png",  ".webp": "image/webp",
}

def _drive_service():
    creds = Credentials.from_service_account_file(
        str(GOOGLE_CREDS_PATH),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)


def upload_to_drive(folder: Path, date_str: str):
    """날짜 폴더를 Drive에 생성하고 folder 내 파일을 모두 업로드"""
    if not GOOGLE_CREDS_PATH.exists():
        log(f"  [Drive] credentials 없음 — 건너뜀")
        return None

    files = sorted(folder.glob("*"))
    if not files:
        log("  [Drive] 업로드할 파일 없음")
        return None

    try:
        service = _drive_service()

        # 날짜 이름으로 하위 폴더 생성
        folder_meta = {
            "name": date_str,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [DRIVE_FOLDER_ID],
        }
        drive_folder = service.files().create(
            body=folder_meta, fields="id,webViewLink", supportsAllDrives=True
        ).execute()
        folder_id   = drive_folder["id"]
        folder_link = drive_folder.get("webViewLink", "")
        log(f"  [Drive] 폴더 생성: {date_str} → {folder_link}")

        # 파일 업로드
        for f in files:
            mime = _MIME.get(f.suffix.lower(), "application/octet-stream")
            media = MediaFileUpload(str(f), mimetype=mime, resumable=False)
            service.files().create(
                body={"name": f.name, "parents": [folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()
            log(f"  [Drive] 업로드: {f.name}")

        log(f"  [Drive] 총 {len(files)}개 파일 업로드 완료")
        return folder_link

    except Exception as e:
        log(f"  [Drive] 오류: {e}")
        return None


# ── Google Sheets ─────────────────────────────────────────────────────────────
def update_sheets(cards: list, changes: dict, date_str: str):
    if not GOOGLE_CREDS_PATH.exists():
        log(f"  [Sheets] credentials 없음 ({GOOGLE_CREDS_PATH}) — 건너뜀")
        return
    try:
        creds = Credentials.from_service_account_file(
            str(GOOGLE_CREDS_PATH),
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        gc = gspread.authorize(creds)
        ss = gc.open_by_key(SPREADSHEET_ID)

        # ── Sheet1 (gid=0): 현재 카드 상태 ──────────────────────────────
        ws = ss.get_worksheet(0)
        ws.clear()
        header = ["#", "제목", "이미지 URL", "CTA 텍스트", "CTA URL", "마지막 업데이트"]
        rows = [header] + [
            [
                i + 1,
                c["title"],
                c.get("image", ""),
                c.get("cta", {}).get("text", ""),
                c.get("cta", {}).get("url", ""),
                date_str,
            ]
            for i, c in enumerate(cards)
        ]
        ws.update(rows, "A1")
        ws.format("A1:F1", {"textFormat": {"bold": True}})
        log("  [Sheets] 현재 카드 시트 업데이트 완료")

        # ── Sheet2: 변경 이력 ────────────────────────────────────────────
        try:
            ws_log = ss.worksheet("변경 이력")
        except gspread.exceptions.WorksheetNotFound:
            ws_log = ss.add_worksheet("변경 이력", rows=1000, cols=7)
            ws_log.append_row(
                ["날짜", "유형", "제목", "이미지 URL", "CTA 텍스트", "CTA URL", "변경 내용"]
            )
            ws_log.format("A1:G1", {"textFormat": {"bold": True}})

        log_rows = []
        for c in changes["added"]:
            log_rows.append(
                [date_str, "추가", c["title"], c.get("image", ""),
                 c.get("cta", {}).get("text", ""), c.get("cta", {}).get("url", ""), "신규 추가"]
            )
        for c in changes["removed"]:
            log_rows.append(
                [date_str, "삭제", c["title"], c.get("image", ""),
                 c.get("cta", {}).get("text", ""), c.get("cta", {}).get("url", ""), "제거됨"]
            )
        for item in changes["modified"]:
            c = item["card"]
            log_rows.append(
                [date_str, "수정", c["title"], c.get("image", ""),
                 c.get("cta", {}).get("text", ""), c.get("cta", {}).get("url", ""),
                 ", ".join(item["diff"]) + " 변경"]
            )
        if log_rows:
            ws_log.append_rows(log_rows)
        log("  [Sheets] 변경 이력 추가 완료")

    except Exception as e:
        log(f"  [Sheets] 오류: {e}")


# ── Slack ─────────────────────────────────────────────────────────────────────
def _card_block(card: dict, label: str, diff: list | None = None, old_card: dict | None = None) -> list:
    """카드 한 장에 대한 Slack Block Kit 블록 목록 반환 (이미지 accessory 포함)"""
    cta_url   = card.get("cta", {}).get("url", "")
    cta_text  = card.get("cta", {}).get("text", "")
    img_url   = card.get("image", "")
    title     = card.get("title", "(제목 없음)")

    # 본문 텍스트 조립
    lines = [f"*{label}* `{title}`"]

    if diff and old_card:
        # 수정된 카드: 변경 항목별 before → after 표시
        if "이미지" in diff:
            lines.append(f"  📷 이미지 변경")
        if "CTA 텍스트" in diff:
            old_t = old_card.get("cta", {}).get("text", "")
            lines.append(f"  🔤 버튼: _{old_t}_ → *{cta_text}*")
        if "CTA URL" in diff:
            old_u = old_card.get("cta", {}).get("url", "")
            lines.append(f"  🔗 URL 변경:\n      이전: {old_u}\n      현재: {cta_url}")
        elif cta_url:
            lines.append(f"  🔗 <{cta_url}|{cta_text or '상세 보기'}>")
    else:
        # 추가/삭제 카드: 전체 정보 표시
        if cta_url:
            lines.append(f"  🔗 <{cta_url}|{cta_text or '상세 보기'}>")
        if img_url:
            lines.append(f"  📷 <{img_url}|이미지 원본>")

    section: dict = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    }

    # 이미지가 있으면 오른쪽 accessory 썸네일로 첨부
    if img_url:
        section["accessory"] = {
            "type": "image",
            "image_url": img_url,
            "alt_text": title,
        }

    return [section, {"type": "divider"}]


def send_slack(changes: dict, cards: list, date_str: str, folder: Path, drive_link: str | None = None):
    if not SLACK_WEBHOOK_URL:
        log("  [Slack] SLACK_WEBHOOK_URL 미설정 — 건너뜀")
        return

    added, removed, modified = changes["added"], changes["removed"], changes["modified"]

    blocks: list = [
        # 헤더
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔔 네스프레소 이벤트 카드 변경  |  {date_str}"},
        },
        # 요약
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"현재 *{len(cards)}개* 카드  ·  "
                    f"✅ 추가 *{len(added)}*  "
                    f"❌ 삭제 *{len(removed)}*  "
                    f"✏️ 수정 *{len(modified)}*"
                ),
            },
        },
        {"type": "divider"},
    ]

    # ── 추가 ──────────────────────────────────────────────────────────────────
    if added:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*✅ 추가된 카드 ({len(added)}개)*"},
        })
        for c in added:
            blocks.extend(_card_block(c, "추가"))

    # ── 삭제 ──────────────────────────────────────────────────────────────────
    if removed:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*❌ 삭제된 카드 ({len(removed)}개)*"},
        })
        for c in removed:
            blocks.extend(_card_block(c, "삭제"))

    # ── 수정 ──────────────────────────────────────────────────────────────────
    if modified:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*✏️ 수정된 카드 ({len(modified)}개)*"},
        })
        for item in modified:
            blocks.extend(
                _card_block(item["card"], "수정", diff=item["diff"], old_card=item["old"])
            )

    # ── 푸터 ──────────────────────────────────────────────────────────────────
    footer_parts = []
    if drive_link:
        footer_parts.append(f"<{drive_link}|📁 Drive 폴더 ({date_str})>")
    else:
        footer_parts.append(f"📁 스크린샷: `{folder.resolve()}`")
    footer_parts.append(f"<https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}|📊 Sheets 바로가기>")

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "  |  ".join(footer_parts)}],
    })

    # Slack API 한 메시지 최대 블록 수 50개 제한 처리
    if len(blocks) > 50:
        blocks = blocks[:49]
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_(블록 수 초과로 일부 생략됨)_"}],
        })

    try:
        resp = http_req.post(
            SLACK_WEBHOOK_URL,
            json={"blocks": blocks},
            timeout=10,
        )
        if resp.ok:
            log("  [Slack] 알림 전송 완료")
        else:
            log(f"  [Slack] 전송 실패 (HTTP {resp.status_code}): {resp.text[:120]}")
    except Exception as e:
        log(f"  [Slack] 오류: {e}")


# ── 메인 ──────────────────────────────────────────────────────────────────────
async def main():
    now      = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"네스프레소 이벤트 모니터  {date_str}  {now.strftime('%H:%M:%S')}")
    print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 크롤링
    log("[1] 크롤링 실행...")
    try:
        current = await crawl()
    except Exception as e:
        log(f"크롤링 실패: {e}")
        return
    log(f"     {len(current)}개 카드 수집 완료")

    # 2. 이전 결과 로드
    is_first = not LAST_RESULTS_FILE.exists()
    old = []
    if not is_first:
        try:
            old = json.loads(LAST_RESULTS_FILE.read_text(encoding="utf-8"))
            log(f"[2] 이전 데이터: {len(old)}개 카드")
        except Exception:
            is_first = True

    # 3. 변경 감지
    changes = detect_changes(old, current)
    changed = has_any_change(changes)

    if is_first:
        log("[초기 실행] 이전 데이터 없음 — 모든 카드를 신규로 처리")
        # 첫 실행도 전체 카드를 "추가"로 간주해 알림 발송
        changes = {"added": current, "removed": [], "modified": []}
        changed = True
    elif not changed:
        log("변경사항 없음. 종료.")
        LAST_RESULTS_FILE.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return
    else:
        log(
            f"[3] 변경 감지 — "
            f"추가 {len(changes['added'])} / "
            f"삭제 {len(changes['removed'])} / "
            f"수정 {len(changes['modified'])}"
        )

    # 4. 스크린샷
    folder = DATA_DIR / date_str
    log(f"[4] 스크린샷 저장 → {folder}")
    await capture_all(current, folder)

    # 5. Google Drive 업로드
    log("[5] Google Drive 업로드...")
    drive_link = upload_to_drive(folder, date_str)

    # 6. Sheets
    log("[6] Google Sheets 업데이트...")
    update_sheets(current, changes, date_str)

    # 7. Slack
    log("[7] Slack 알림 전송...")
    send_slack(changes, current, date_str, folder, drive_link)

    # 6. 기준 데이터 저장
    LAST_RESULTS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"기준 데이터 저장: {LAST_RESULTS_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
