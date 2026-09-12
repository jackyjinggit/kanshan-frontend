# -*- coding: utf-8 -*-
"""
zhihu_scrape_v4.py · 知乎 V4 内容接口抓取（cookie + 无头浏览器）
借鉴 weibo-column-scraper 思路：门控内容只能以生成它的运行时身份求值。
403 code10003 = 缺浏览器运行时签名（__zse_ck/d_c0 + JS 签发的 x-zse-96）；
本脚本不手搓签名，而是打开知乎真实页面，让页面自己的 JS 发签名请求，
用响应监听捕获 /api/v4/members/{uid}/(profile|answers|articles) 的 JSON。

红线：
- cookie 文件仓外传入（--cookie），脚本不读取其 value 到日志、不回显、不落盘到输出。
- 仅抓公开内容（回答/文章列表），只读不改账号状态（不关注/不点赞/不发帖）。
- 限个人研究/黑客松演示用途，控制频率。

用法：
  python zhihu_scrape_v4.py --uid <url_token> [--cookie 知乎cookie0912.json] [--out out.json]
输出：捕获的接口 JSON 合集（不含任何 cookie 值）。
"""
import argparse
import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 捕获目标：成员主页/回答/文章 V4 接口（知乎 web 页自己发的签名请求）
CAPTURE_PATTERNS = [
    re.compile(r"/api/v4/members/[^/]+/answers\?"),
    re.compile(r"/api/v4/members/[^/]+/articles\?"),
    re.compile(r"/api/v4/members/[^/]+\?include="),  # profile
]


def load_cookies(path):
    """浏览器导出的 cookie 数组 → Playwright 格式。
    坑位（skill 实测）：sameSite 'no_restriction'→'None'；sameSite null→剔除该字段。
    """
    raw = json.load(open(path, encoding="utf-8"))
    out = []
    for c in raw:
        item = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
        }
        if c.get("expirationDate"):
            item["expires"] = c["expirationDate"]
        ss = c.get("sameSite")
        if ss == "no_restriction":
            item["sameSite"] = "None"
        elif ss in ("strict", "lax"):
            item["sameSite"] = ss.capitalize()
        # sameSite 为 null/缺失 → 只剔除 sameSite 字段、保留 cookie（skill 坑位3 原意；
        # 2026-09-12 实测：误剔整条会把 z_c0 登录态一起丢掉）
        out.append(item)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True, help="知乎 url_token（主页 zhihu.com/people/xxx 的 xxx）")
    ap.add_argument("--cookie", default="知乎cookie0912.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--scroll", type=int, default=2, help="向下滚动次数（触发更多加载）")
    args = ap.parse_args()

    cookies = load_cookies(args.cookie)
    has_auth = any(c["name"] == "z_c0" for c in cookies)
    has_zseck = any(c["name"] == "__zse_ck" for c in cookies)
    print(f"[i] cookie 注入 {len(cookies)} 条（z_c0 登录态: {'有' if has_auth else '无'}；"
          f"__zse_ck 签名: {'有' if has_zseck else '无'}）——值不回显")

    captured = []

    def on_response(resp):
        url = resp.url
        if any(p.search(url) for p in CAPTURE_PATTERNS):
            try:
                body = resp.json()
                captured.append({"url": url, "status": resp.status, "json": body})
                kind = "answers" if "/answers" in url else ("articles" if "/articles" in url else "profile")
                n = len(body.get("data", [])) if isinstance(body, dict) else "?"
                print(f"[+] 捕获 {kind} HTTP {resp.status}（data {n} 条）")
            except Exception as e:
                print(f"[!] 捕获 {url[:80]} 解析失败: {e}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                                  locale="zh-CN", timezone_id="Asia/Shanghai")
        # 反检测：无头 shell 的 navigator.webdriver 会连累 x-zse-96 签名校验（2026-09-12 实测
        # 登录态正常但 V4 接口仍 10003）——藏掉 webdriver 痕迹再让页面发签名请求
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
            "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()
        page.on("response", on_response)

        url = f"https://www.zhihu.com/people/{args.uid}/answers"
        print(f"[i] 打开 {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)

        # 滚动触发懒加载（只读浏览，不交互不改状态）
        for i in range(args.scroll):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(3500)
            print(f"[i] 滚动 {i + 1}/{args.scroll}")

        # 顺带验证登录态 + SSR 兜底：即便接口 403，回答/文章卡片常已服务端渲染进 HTML，
        # 直接从 DOM 抽（weibo skill 精髓的另一个面：运行时已求值的内容直接拿）
        logged = page.evaluate(
            "() => !!document.querySelector('.AppHeader-profile, .ProfileMain-header')"
        )
        print(f"[i] 页面标题: {page.title()[:60]}")
        ssr_answers = page.evaluate(
            """() => Array.from(document.querySelectorAll('.List-item')).map(el => {
                const t = el.querySelector('h2 .ContentItem-titleMeta, .ContentItem-title');
                const meta = el.querySelector('.ContentItem-actions');
                return { title: t ? t.innerText.trim() : '', meta: meta ? meta.innerText.trim().slice(0, 80) : '' };
            }).filter(x => x.title)
        """
        )
        print(f"[i] SSR 回答卡片: {len(ssr_answers)} 个")
        ssr_out = ssr_answers

        # 文章页也过一遍（若该账号有文章）
        ssr_articles = []
        try:
            page.goto(f"https://www.zhihu.com/people/{args.uid}/posts",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(3000)
            ssr_articles = page.evaluate(
                """() => Array.from(document.querySelectorAll('.List-item')).map(el => {
                    const t = el.querySelector('h2 .ContentItem-titleMeta, .ContentItem-title');
                    return t ? t.innerText.trim() : '';
                }).filter(Boolean)
            """
            )
            print(f"[i] SSR 文章卡片: {len(ssr_articles)} 个")
        except Exception as e:
            print(f"[!] posts 页跳过: {e}")

        browser.close()

    out = {
        "uid": args.uid,
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "logged_in_header": logged,
        "captured_count": len(captured),
        "ssr_answers": ssr_out,
        "ssr_articles": ssr_articles,
        "captured": captured,
    }
    out_path = args.out or f"zhihu_v4_{args.uid}_{time.strftime('%m%d_%H%M')}.json"
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[✓] 完成：捕获 {len(captured)} 个接口响应 → {out_path}")
    # 摘要（不含 cookie 值）
    for c in captured:
        kind = "answers" if "/answers" in c["url"] else ("articles" if "/articles" in c["url"] else "profile")
        data = c["json"].get("data") if isinstance(c["json"], dict) else None
        if kind == "profile":
            print(f"    - profile: HTTP {c['status']}")
        else:
            print(f"    - {kind}: HTTP {c['status']}，{len(data) if isinstance(data, list) else 0} 条")


if __name__ == "__main__":
    sys.exit(main())
