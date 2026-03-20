"""
Nespresso Korea 홈페이지 "최신 소식" 이벤트 카드 크롤러
- URL: https://www.nespresso.com/kr/ko/
- 대상: "최신 소식 / 신제품 소식과 특별 혜택을 만나보세요" 섹션 (Splide 캐러셀 첫 번째, 5개 카드)
- 추출: title, image, cta (text + url)
- 출력: nespresso_events.json
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from playwright.async_api import async_playwright, Page

# GitHub Actions 등 headless 환경에서는 HEADLESS=true 로 설정
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TARGET_URL = "https://www.nespresso.com/kr/ko/"
OUTPUT_FILE = "nespresso_events.json"


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.nespresso.com" + url
    return url


def best_srcset(srcset: str) -> str:
    candidates = []
    for part in srcset.split(","):
        tokens = part.strip().split()
        if not tokens:
            continue
        url = tokens[0]
        width = int(tokens[1].rstrip("w")) if len(tokens) > 1 else 0
        candidates.append((width, url))
    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else ""


async def extract_image(slide) -> str:
    """<picture> > <source> srcset에서 PC용 최고화질 이미지 추출"""
    # <picture> 내 source 탐색 (데스크탑 기준 최대 해상도)
    sources = slide.locator("picture source")
    count = await sources.count()
    best_url = ""
    for i in range(count):
        src = sources.nth(i)
        srcset = await src.get_attribute("srcset") or ""
        media = await src.get_attribute("media") or ""
        url = best_srcset(srcset) if srcset else ""
        if url:
            best_url = url  # 마지막(가장 넓은) source 우선
            if "min-width: 1024px" in media and "2x" not in media and "192dpi" not in media:
                break  # 1024px 1x 기준이면 즉시 사용

    if best_url:
        return normalize_url(best_url)

    # 폴백: <img> src/srcset
    img = slide.locator("img").first
    if await img.count() > 0:
        srcset = await img.get_attribute("srcset") or ""
        src = await img.get_attribute("src") or ""
        url = best_srcset(srcset) if srcset else src
        if url and not url.startswith("data:"):
            return normalize_url(url)

    return ""


async def extract_title(slide) -> str:
    """슬라이드 카드에서 제목 추출 (h1-h4 우선)"""
    for selector in ["h1", "h2", "h3", "h4",
                     "[class*='title']", "[class*='Title']",
                     "[class*='headline']", "[class*='Headline']"]:
        el = slide.locator(selector).first
        if await el.count() > 0:
            text = (await el.inner_text()).strip()
            if len(text) > 2 and re.search(r"[가-힣a-zA-Z]", text):
                return text
    return ""


async def click_and_capture_url(page: Page, btn) -> str:
    """버튼 클릭 → URL 변경 감지(commit) → 홈 재접속"""
    try:
        # wait_until="commit" : URL이 바뀌는 순간 즉시 감지 (페이지 완전 로드 불필요)
        async with page.expect_navigation(timeout=20_000, wait_until="commit"):
            await btn.click(timeout=5_000)
        url = page.url
    except Exception:
        # 20초 내 URL 변화 없음 → 현재 URL 체크
        await page.wait_for_timeout(1_000)
        url = page.url

    if url.rstrip("/") == TARGET_URL.rstrip("/"):
        return ""

    # 홈으로 직접 재접속 (go_back 미사용 — Splide 완전 재초기화 보장)
    await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1_500)
    return url


async def dismiss_cookie_popup(page: Page):
    for sel in ["#onetrust-accept-btn-handler", "button[id*='accept']", "button[class*='accept']"]:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=3_000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass


async def crawl() -> list:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
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
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await ctx.new_page()

        print(f"접속 중: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60_000)
        await dismiss_cookie_popup(page)

        print("렌더링 대기...")
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            await page.wait_for_timeout(5_000)

        # lazy-load 트리거
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(500)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(800)

        # "최신 소식" 섹션 = Splide 캐러셀 첫 번째 (prevSibling에 '최신 소식' 포함)
        # 검증: 첫 번째 .splide의 이전 형제에 '최신 소식' 텍스트가 있어야 함
        first_carousel = page.locator(".splide").first
        prev_text = await page.evaluate(
            "el => el.previousElementSibling ? el.previousElementSibling.textContent.trim() : ''",
            await first_carousel.element_handle(),
        )
        print(f"첫 번째 캐러셀 이전 형제 텍스트: {prev_text[:60]!r}")

        if "최신 소식" not in prev_text:
            print("  ⚠ '최신 소식' 섹션 인접 캐러셀을 찾지 못했습니다. 첫 번째 캐러셀로 진행합니다.")

        slides = first_carousel.locator(".splide__slide:not(.splide__slide--clone)")
        slide_count = await slides.count()
        print(f"슬라이드 {slide_count}개 발견\n")

        # 1단계: title + image 수집
        raw = []
        for i in range(slide_count):
            slide = slides.nth(i)
            title = await extract_title(slide)
            image = await extract_image(slide)
            # CTA 버튼 텍스트
            btn = slide.locator("button,a").first
            cta_text = (await btn.inner_text()).strip() if await btn.count() > 0 else ""
            raw.append({"title": title, "image": image, "cta_text": cta_text})
            print(f"  [{i}] title={title[:40]!r}  image={'O' if image else 'X'}")

        # 2단계: CTA URL (버튼 클릭으로 추적)
        print("\nCTA URL 추적 중...")
        results = []
        for i, r in enumerate(raw):
            cta_url = ""
            try:
                # 홈 복귀 후 캐러셀·슬라이드 버튼이 클릭 가능할 때까지 대기
                await page.wait_for_selector(
                    ".splide__slide:not(.splide__slide--clone) button",
                    state="visible",
                    timeout=10_000,
                )
                await page.wait_for_timeout(800)   # JS 이벤트 바인딩 여유

                slide = slides.nth(i)
                btn = slide.locator("button,a").first
                if await btn.count() > 0:
                    cta_url = await click_and_capture_url(page, btn)
                    # 실패 시 1회 재시도 (홈 재접속 후 버튼 재탐색)
                    if not cta_url:
                        await page.wait_for_timeout(2_000)
                        cta_url = await click_and_capture_url(page, slide.locator("button,a").first)
            except Exception as e:
                print(f"  [{i}] URL 추적 실패: {e}")

            card = {
                "title": r["title"],
                "image": r["image"],
                "cta": {"text": r["cta_text"], "url": cta_url},
            }
            results.append(card)
            print(f"  [{i}] {r['title'][:35]!r} → {cta_url or '(없음)'}")

        await browser.close()
        return results


async def main():
    print("=" * 60)
    print("Nespresso Korea - 최신 소식 이벤트 카드 크롤러")
    print("=" * 60)

    results = await crawl()

    out = Path(OUTPUT_FILE)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n완료: {len(results)}개 카드 → {out.resolve()}")
    print("=" * 60)
    print("\n[결과 전체]")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
