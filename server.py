# -*- coding: utf-8 -*-
"""
看山 · 本地数据网关 v2（对接知乎官方开放平台 API）
================================================================
职责：
  1) 托管网页：http://localhost:8686/ → index.html
  2) 代理官方 API（developer.zhihu.com）—— Access Secret 只留本机，绝不进前端
  3) 可选代理知乎公开 V4 接口（无需密钥，可能受官方风控限制）

密钥配置（二选一）：
  a) 在本文件同目录建 secret.txt，把 Access Secret 粘贴进去保存（推荐）
  b) 环境变量 ZHIHU_ACCESS_SECRET

Access Secret 获取：https://developer.zhihu.com/profile → 生成
⚠️ secret.txt 不要上传 GitHub / 不要截图 / 不要发群（官方红线）
仅用 Python 标准库，零依赖。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8686
HERE = os.path.dirname(os.path.abspath(__file__))
SECRET_FILE = os.path.join(HERE, "secret.txt")
OFFICIAL = "https://developer.zhihu.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 简单缓存：contents 60s / quota 300s / public 60s
# （官方红线：禁止批量高频调用；公开账号一次流程最多抓三路接口）
_cache = {"contents": (0, ""), "quota": (0, ""), "public": {}}


def load_secret():
    s = os.environ.get("ZHIHU_ACCESS_SECRET", "").strip()
    if s:
        return s.strip("'\"").strip()
    try:
        if os.path.exists(SECRET_FILE):
            txt = open(SECRET_FILE, "r", encoding="utf-8-sig").read().strip()
            return txt.strip("'\"").strip()
    except Exception:
        pass
    return ""


SECRET = load_secret()


# ---- OAuth（知乎开放平台 · 授权登录 · 协议照官方 hello-world-oauth 参考实现）----
OAUTH_APP_ID = os.environ.get("ZHIHU_OAUTH_APP_ID", "").strip()
OAUTH_APP_KEY = os.environ.get("ZHIHU_OAUTH_APP_KEY", "").strip()
OAUTH_REDIRECT_URI = os.environ.get("ZHIHU_OAUTH_REDIRECT_URI", "").strip()
_OAUTH_CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oauth.credentials.local")
if os.path.exists(_OAUTH_CRED_FILE):
    try:
        with open(_OAUTH_CRED_FILE, encoding="utf-8") as _f:
            _c = json.load(_f)
        OAUTH_APP_ID = OAUTH_APP_ID or str(_c.get("app_id", "")).strip()
        OAUTH_APP_KEY = OAUTH_APP_KEY or str(_c.get("app_key", "")).strip()
        OAUTH_REDIRECT_URI = OAUTH_REDIRECT_URI or str(_c.get("redirect_uri", "")).strip()
    except Exception:
        pass
if not OAUTH_REDIRECT_URI:
    OAUTH_REDIRECT_URI = "http://127.0.0.1:%d/oauth/callback" % PORT
OAUTH_STATE = {"value": None}
OAUTH_TOKEN = {"value": None, "expires": None, "profile": None}

def oauth_configured():
    return bool(OAUTH_APP_ID and OAUTH_APP_KEY and OAUTH_REDIRECT_URI)

def oauth_status_payload():
    return {
        "ok": True,
        "configured": oauth_configured(),
        "appId": OAUTH_APP_ID,
        "redirectUri": OAUTH_REDIRECT_URI,
        "authorized": bool(OAUTH_TOKEN["value"]),
        "profile": OAUTH_TOKEN["profile"],
        "note": ("已授权" if OAUTH_TOKEN["value"] else
                 "未授权——点「用知乎账号登录」完成授权；回调需与开放平台登记一致"),
    }

def _oauth_safe(v):
    if not v or re.search(r"[\r\n\"\\]", v):
        raise ValueError("凭证格式无效")
    return v

def _oauth_exchange(code):
    """code 换 access_token（POST openapi.zhihu.com/access_token，form-urlencoded）"""
    form = urllib.parse.urlencode({
        "app_id": OAUTH_APP_ID,
        "app_key": _oauth_safe(OAUTH_APP_KEY),
        "grant_type": "authorization_code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "code": _oauth_safe(code),
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openapi.zhihu.com/access_token", data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def _oauth_profile(oauth_token):
    """双头鉴权取用户公开信息：Secret 头 + X-OAuth-Token 头"""
    req = urllib.request.Request(
        "https://openapi.zhihu.com/user", method="GET",
        headers={
            "Authorization": "Bearer " + SECRET,
            "X-OAuth-Token": _oauth_safe(oauth_token),
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.loads(r.read().decode("utf-8"))
        src = payload.get("data") or payload.get("Data") or payload.get("user") or {}
        if not isinstance(src, dict):
            return None
        return {
            "name": src.get("name") or src.get("Fullname") or None,
            "avatarUrl": src.get("avatar_url") or src.get("AvatarUrl") or None,
            "headline": src.get("headline") or src.get("Headline") or None,
            "url": src.get("url") or src.get("Url") or None,
        }
    except Exception:
        return None


def find_cli():
    """定位官方 zhihu-cli，不依赖 PATH，也不把凭证交给浏览器。"""
    candidates = []
    configured = os.environ.get("ZHIHU_CLI_PATH", "").strip()
    if configured:
        candidates.append(configured)

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(os.path.join(local_app_data, "ZhihuCLI", "current", "zhihu-cli.exe"))

    for name in ("zhihu-cli.exe", "zhihu-cli"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return ""


def cli_error(code, message, detail=""):
    error = {"code": code, "message": message}
    if detail:
        error["detail"] = detail[:240]
    return {"ok": False, "error": error}


def run_cli_json(args, timeout=10):
    """
    用参数数组调用官方 CLI，禁止 shell 拼接。
    stdout 只解析 JSON，stderr/完整响应不写入日志，避免内容与凭证泄露。
    """
    binary = find_cli()
    if not binary:
        return cli_error("CLI_NOT_INSTALLED", "未找到官方知乎 CLI")

    env = os.environ.copy()
    # CLI 官方支持 ZHIHU_ACCESS_SECRET。仅在服务端进程环境中传入，
    # 不返回、不写日志、不放入 URL；浏览器永远拿不到密钥。
    if SECRET and not env.get("ZHIHU_ACCESS_SECRET"):
        env["ZHIHU_ACCESS_SECRET"] = SECRET

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [binary] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return cli_error("TIMEOUT", "知乎 CLI 响应超时")
    except OSError as exc:
        return cli_error("CLI_UNAVAILABLE", "知乎 CLI 无法启动", str(exc))

    raw = (proc.stdout or "").strip()
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None

    if proc.returncode != 0:
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            err = parsed["error"]
            return cli_error(
                str(err.get("code") or "CLI_ERROR"),
                str(err.get("message") or "知乎 CLI 调用失败"),
                "",
            )
        return cli_error("CLI_ERROR", "知乎 CLI 调用失败")

    if parsed is None:
        return cli_error("CLI_PROTOCOL", "知乎 CLI 返回的不是 JSON")
    return {"ok": True, "data": parsed}


def extract_items(payload):
    """兼容官方 CLI/开放平台响应的大小写与 Data.Items 包装。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("Data", payload.get("data", payload))
    if isinstance(data, dict):
        for key in ("Items", "items", "Results", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    if isinstance(data, list):
        return data
    return []


def normalize_public_uid(raw):
    """只接受知乎 people ID 或主页链接，避免把任意 URL 交给后端。"""
    value = urllib.parse.unquote(str(raw or "").strip())
    if not value:
        return ""
    if "://" in value:
        parsed = urllib.parse.urlparse(value)
        if parsed.netloc not in ("www.zhihu.com", "zhihu.com"):
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        try:
            value = parts[parts.index("people") + 1]
        except (ValueError, IndexError):
            return ""
    value = value.lstrip("@").split("?", 1)[0].split("#", 1)[0].strip()
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", value) else ""


def public_get_json(url, timeout=12):
    status, body = http_get(url, {
        "User-Agent": UA,
        "Referer": "https://www.zhihu.com/",
        "Accept": "application/json, text/plain, */*",
    }, timeout=timeout)
    try:
        return status, json.loads(body)
    except Exception:
        return status, None


# ---------- cookie + 无头浏览器兜底（2026-09-12 实测打通） ----------
# 公开 V4 answers/articles 常被反爬 403(code10003)。借 weibo-column-scraper 思路：
# 注入 cookie 的无头浏览器打开真实页面，让页面自身 JS 发签名请求，监听捕获响应。
# 红线：cookie 文件不进 git（.gitignore 已覆盖）、值不回显不落日志；只读浏览不改账号状态。
_BROWSER_FB_TIMEOUT = 100
_browser_fb_lock = threading.Lock()

# 网关自身可能跑在没有 Playwright 的解释器上（启动.bat 用系统 python）——
# 兜底子进程按候选顺序找第一个能 import playwright 的解释器，结果缓存
_BROWSER_FB_PY_CANDIDATES = [
    sys.executable,
    r"C:\Users\Q1070\.workbuddy\binaries\python\envs\default\Scripts\python.exe",
]
_fb_python_cache = {"value": None, "resolved": False}


def _fb_python_exe():
    if _fb_python_cache["resolved"]:
        return _fb_python_cache["value"] or None
    for exe in _BROWSER_FB_PY_CANDIDATES:
        if not exe or not os.path.exists(exe):
            continue
        try:
            probe = subprocess.run([exe, "-c", "import playwright"],
                                   timeout=20, capture_output=True)
            if probe.returncode == 0:
                _fb_python_cache.update(value=exe, resolved=True)
                return exe
        except Exception:
            continue
    _fb_python_cache.update(value="", resolved=True)
    print("[browser-fb] 候选解释器均无 playwright，兜底不可用")
    return None


def _find_cookie_file():
    """在网关上一级目录（知乎黑松客/）找最新的 知乎cookie*.json（换新 cookie 自动跟进）"""
    root = os.path.dirname(HERE)
    best = None
    try:
        for name in os.listdir(root):
            if name.startswith("知乎cookie") and name.endswith(".json"):
                p = os.path.join(root, name)
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    except Exception:
        return None
    return best


def browser_fallback_bundle(uid, limit=20):
    """cookie+无头浏览器抓回答/文章/profile。任何失败返回 None（调用方走原路）。"""
    script = os.path.join(os.path.dirname(HERE), "zhihu_scrape_v4.py")
    if not os.path.exists(script):
        return None
    cookie = _find_cookie_file()
    if not cookie:
        print("[browser-fb] 未找到 知乎cookie*.json，跳过兜底")
        return None
    out_path = os.path.join(HERE, "_browser_fb_out.json")
    pyexe = _fb_python_exe()
    if not pyexe:
        return None
    cmd = [pyexe, script, "--uid", uid, "--cookie", cookie,
           "--out", out_path, "--scroll", "1"]
    if not _browser_fb_lock.acquire(blocking=False):
        return None  # 已有兜底在跑，本次直接放弃（前端可重试）
    try:
        print("[browser-fb] 启动无头浏览器抓取 uid=", uid)
        proc = subprocess.run(cmd, timeout=_BROWSER_FB_TIMEOUT,
                              capture_output=True, text=True,
                              cwd=os.path.dirname(script))
        if proc.returncode != 0 or not os.path.exists(out_path):
            print("[browser-fb] 失败:", (proc.stderr or proc.stdout or "")[-200:])
            return None
        data = json.load(open(out_path, encoding="utf-8"))
    except Exception as e:
        print("[browser-fb] 异常:", e)
        return None
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass
        _browser_fb_lock.release()

    ans, art, profile = [], [], None
    seen_a, seen_r = set(), set()
    for c in data.get("captured", []):
        j = c.get("json")
        u = c.get("url", "")
        if not isinstance(j, dict):
            continue
        if "/answers" in u and isinstance(j.get("data"), list):
            for it in j["data"]:
                if isinstance(it, dict) and it.get("id") not in seen_a:
                    seen_a.add(it.get("id"))
                    ans.append(it)
        elif "/articles" in u and isinstance(j.get("data"), list):
            for it in j["data"]:
                if isinstance(it, dict) and it.get("id") not in seen_r:
                    seen_r.add(it.get("id"))
                    art.append(it)
        elif profile is None and re.search(r"/members/[^/?]+\?", u) \
                and isinstance(j.get("follower_count"), int):
            profile = j
    if not ans and not art:
        print("[browser-fb] 未捕获到内容数据")
        return None
    return {
        "profile": profile,
        "answers": {"data": ans[:limit]},
        "articles": {"data": art[:limit]},
    }


def fetch_public_bundle(uid, limit=20):
    """
    公开账号链路：
      1) 先走官方 zhihu-cli search，记录 CLI 是否真正可用；
      2) 再按 ID 抓取公开主页/回答/文章，保证账号归属准确；
      3) 一次性把抓取结果返回给前端，前端本地做方法论分析。

    官方 CLI 当前公开能力是搜索/热榜/本人数据，并没有“按任意 people ID
    列出全部内容”的命令。因此这里不伪造 CLI 能力：CLI 负责官方搜索接入，
    账号精确列表由同一官方公开 V4 端点兜底，页面会标明实际来源。
    """
    uid = normalize_public_uid(uid)
    if not uid:
        return {"Code": 10002, "Message": "知乎 ID 或主页链接格式不正确"}
    try:
        limit = min(20, max(1, int(limit)))
    except Exception:
        limit = 20

    cache_key = uid + ":" + str(limit)
    cached = _cache["public"].get(cache_key)
    if cached and time.time() - cached[0] < 60:
        return cached[1]

    cli_result = run_cli_json(
        ["search", "zhihu", "--query", uid, "--count", "10"],
        timeout=8,
    )
    cli_items = extract_items(cli_result.get("data")) if cli_result.get("ok") else []
    if cli_result.get("ok"):
        cli_meta = {
            "available": True,
            "used": True,
            "status": "used",
            "result_count": len(cli_items),
        }
    else:
        code = (cli_result.get("error") or {}).get("code", "CLI_ERROR")
        cli_meta = {
            "available": code != "CLI_NOT_INSTALLED",
            "used": False,
            "status": "missing" if code == "CLI_NOT_INSTALLED" else "fallback",
            "error_code": code,
        }

    include = "follower_count,voteup_count,thanked_count,favorite_count,answer_count,articles_count,headline"
    profile_url = (
        "https://www.zhihu.com/api/v4/members/"
        + urllib.parse.quote(uid, safe="")
        + "?include="
        + urllib.parse.quote(include, safe="")
    )
    base_url = "https://www.zhihu.com/api/v4/members/" + urllib.parse.quote(uid, safe="")
    profile_status, profile = public_get_json(profile_url)
    if not isinstance(profile, dict) or profile.get("error"):
        profile_status, profile = public_get_json(base_url)

    answers_status, answers = public_get_json(
        base_url + "/answers?offset=0&limit=" + str(limit) + "&sort_by=created"
    )
    articles_status, articles = public_get_json(
        base_url + "/articles?offset=0&limit=" + str(limit) + "&sort_by=created"
    )

    has_profile = (
        isinstance(profile, dict)
        and not profile.get("error")
        and not profile.get("Code")
        and any(key in profile for key in ("id", "name", "url_token", "follower_count"))
    )
    has_answers = isinstance(answers, dict) and isinstance(answers.get("data"), list)
    has_articles = isinstance(articles, dict) and isinstance(articles.get("data"), list)

    # cookie+无头浏览器兜底：裸抓 403（code10003）时启用，补齐缺失的路（2026-09-12 实测打通）
    fb_used = False
    if not (has_profile and has_answers and has_articles):
        fb = browser_fallback_bundle(uid, limit)
        if fb:
            if not has_profile and isinstance(fb.get("profile"), dict):
                profile, profile_status, has_profile = fb["profile"], 200, True
                fb_used = True
                print("[browser-fb] profile 已补齐")
            if not has_answers and fb.get("answers"):
                answers, answers_status, has_answers = fb["answers"], 200, True
                fb_used = True
                print("[browser-fb] answers 已补齐", len(fb["answers"]["data"]), "条")
            if not has_articles and fb.get("articles"):
                articles, articles_status, has_articles = fb["articles"], 200, True
                fb_used = True
                print("[browser-fb] articles 已补齐", len(fb["articles"]["data"]), "条")

    if not (has_profile or has_answers or has_articles):
        return {
            "Code": 90002,
            "Message": "知乎公开接口暂时没有返回该账号数据",
            "CLI": cli_meta,
            "HttpStatus": {
                "profile": profile_status,
                "answers": answers_status,
                "articles": articles_status,
            },
        }

    result = {
        "Code": 0,
        "Data": {
            "UID": uid,
            "Profile": profile if has_profile else None,
            "Answers": answers if has_answers else None,
            "Articles": articles if has_articles else None,
            "CLISearch": cli_meta,
            # 数据来源可追溯：used=true 表示该次结果有走 cookie+无头浏览器兜底
            "Browser": {"used": fb_used},
        },
    }
    # 只缓存真正抓到内容的结果——兜底失败（如浏览器起不来）不缓存，
    # 否则 60 秒内重试都拿到同样的空数据
    if has_answers or has_articles:
        _cache["public"][cache_key] = (time.time(), result)
    return result


def http_get(url, headers, timeout=15):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, json.dumps({"Code": 90001, "Message": "网络不可达: " + str(e)}, ensure_ascii=False)


def official_headers():
    return {
        "Authorization": "Bearer " + SECRET,
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
        "User-Agent": UA,
    }


class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass  # 静默日志

    # ---- OAuth 回调落地页（服务端渲染，简单直接）----
    def _oauth_callback_page(self, q):
        def page(body_html, status_icon):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>看山 · 授权结果</title></head>
<body style="font-family:system-ui,'Microsoft YaHei',sans-serif;background:#0b1b2b;color:#e8f1fa;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0">
<div style="max-width:520px;text-align:center;padding:40px 28px;background:#12283d;border-radius:16px">
<div style="font-size:34px">""" + status_icon + """</div>""" + body_html + """
<p style="margin-top:26px"><a href="/" style="color:#5aa9ff">← 回到看山首页</a></p>
</div></body></html>""").encode("utf-8"))

        def fail(msg):
            page('<h2 style="margin:8px 0">授权未完成</h2><p style="color:#9fb4c7;line-height:1.7">' + msg + '</p>', "⚠️")

        try:
            if not oauth_configured():
                return fail("OAuth 凭证未配置：oauth.credentials.local 缺失或字段不全。")
            code = (q.get("authorization_code") or q.get("code") or [""])[0]
            if not code:
                err = (q.get("error") or ["授权被取消"])[0]
                return fail("知乎返回：" + str(err)[:120])
            state = (q.get("state") or [None])[0]
            if state and OAUTH_STATE["value"] and state != OAUTH_STATE["value"]:
                return fail("state 校验失败——请从看山首页重新发起授权。")
            payload = _oauth_exchange(code)
            token = payload.get("access_token") or (payload.get("data") or {}).get("access_token") if isinstance(payload, dict) else None
            if not token:
                msg = str(payload)[:160] if not isinstance(payload, dict) else str(payload.get("message") or payload.get("error") or payload)[:160]
                return fail("未获得 access token：" + msg)
            import time as _t
            expires = payload.get("expires_in")
            OAUTH_TOKEN["value"] = token
            OAUTH_TOKEN["expires"] = (int(_t.time()) + int(expires) * 1000) if str(expires).isdigit() else None
            OAUTH_TOKEN["profile"] = _oauth_profile(token)
            prof = OAUTH_TOKEN["profile"] or {}
            who = prof.get("name") or "知乎用户"
            avatar = ('<img src="' + str(prof.get("avatarUrl")) + '" style="width:64px;height:64px;border-radius:50%">') if prof.get("avatarUrl") else ""
            page(avatar + '<h2 style="margin:10px 0">授权成功</h2><p style="color:#9fb4c7">已连接：' + who +
                 '</p><p style="color:#9fb4c7;font-size:13px">回看山首页即可开始分析；授权 token 只存在本机内存，不上传。</p>', "✅")
        except Exception as exc:
            fail("授权处理异常：" + str(exc)[:180])

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _passthrough(self, body):
        """统一 HTTP 200 透传，业务码在 JSON 的 Code 字段里（官方协议）"""
        try:
            json.loads(body)
            self._send(200, body)
        except Exception:
            self._send(200, json.dumps({"Code": 90001, "Message": "网关收到非 JSON 响应"}, ensure_ascii=False))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        # ---- 网关状态（前端状态灯用） ----
        if path == "/api/status":
            cli_ready = bool(find_cli())
            return self._send(200, json.dumps({
                "ok": True,
                "secret": bool(SECRET),
                "version": "v3-cli-bridge",
                "cli": {
                    "available": cli_ready,
                    "name": "zhihu-cli",
                    "mode": "official-search-bridge",
                },
            }, ensure_ascii=False))

        # ---- OAuth（授权登录 · 官方开放平台）----
        if path == "/api/oauth/status":
            return self._send(200, json.dumps(oauth_status_payload(), ensure_ascii=False))
        if path == "/api/oauth/url":
            if not oauth_configured():
                return self._send(200, json.dumps({"ok": False, "error": "OAuth 凭证未配置（oauth.credentials.local 缺失或字段不全）"}, ensure_ascii=False))
            import secrets as _sec
            OAUTH_STATE["value"] = _sec.token_urlsafe(24)
            u = ("https://openapi.zhihu.com/authorize?redirect_uri=" + urllib.parse.quote(OAUTH_REDIRECT_URI, safe="")
                 + "&app_id=" + urllib.parse.quote(OAUTH_APP_ID)
                 + "&response_type=code&state=" + OAUTH_STATE["value"])
            return self._send(200, json.dumps({"ok": True, "url": u}, ensure_ascii=False))
        if path == "/oauth/callback":
            return self._oauth_callback_page(q)

        # ---- 官方 API：本人创作内容 ----
        if path == "/api/me/contents":
            if not SECRET:
                return self._send(200, json.dumps({
                    "Code": 20001,
                    "Message": "本机未配置 Access Secret：把密钥粘贴到 frontend/secret.txt 后重启 启动.bat（密钥只留本机，不进前端）"
                }, ensure_ascii=False))
            now = time.time()
            if now - _cache["contents"][0] < 60:
                return self._passthrough(_cache["contents"][1])
            limit, offset, ctype = 50, 0, "all"
            try:
                limit = min(50, max(1, int((q.get("limit") or ["50"])[0])))
                offset = max(0, int((q.get("offset") or ["0"])[0]))
                ctype = (q.get("ctype") or ["all"])[0]
            except Exception:
                pass
            url = (OFFICIAL + "/api/v1/user/contents?ContentType=%s&Limit=%d&Offset=%d&SortField=ts&SortOrder=desc"
                   % (urllib.parse.quote(ctype), limit, offset))
            st, body = http_get(url, official_headers())
            _cache["contents"] = (time.time(), body)
            return self._passthrough(body)

        # ---- 官方 API：额度（查询本身不耗业务额度） ----
        if path == "/api/quota":
            if not SECRET:
                return self._send(200, json.dumps({"Code": 20001, "Message": "未配置密钥"}, ensure_ascii=False))
            now = time.time()
            if now - _cache["quota"][0] < 300:
                return self._passthrough(_cache["quota"][1])
            st, body = http_get(OFFICIAL + "/api/v1/quota", official_headers())
            _cache["quota"] = (time.time(), body)
            return self._passthrough(body)

        # ---- 公开账号：官方 zhihu-cli 接入 + 精确账号公开数据 ----
        if path == "/api/cli/public":
            uid = (q.get("uid") or [""])[0]
            limit = (q.get("limit") or ["20"])[0]
            try:
                result = fetch_public_bundle(uid, limit)
            except Exception as exc:
                # 网页端永远收到结构化错误，避免浏览器白屏或闪退。
                result = {
                    "Code": 90003,
                    "Message": "公开抓取服务内部错误",
                    "Detail": str(exc)[:240],
                }
            return self._send(200, json.dumps(result, ensure_ascii=False))

        # ---- 公开 V4 接口代理（任意账号 · 无需密钥 · 可能受限） ----
        if path == "/api/pub":
            target = (q.get("u") or [""])[0]
            if not target.startswith("https://www.zhihu.com/"):
                return self._send(200, json.dumps({"Code": 10001, "Message": "仅允许代理 zhihu.com 公开接口"}, ensure_ascii=False))
            st, body = http_get(target, {
                "User-Agent": UA,
                "Referer": "https://www.zhihu.com/",
                "Accept": "application/json, text/plain, */*",
            }, timeout=12)
            return self._passthrough(body)

        # ---- 心跳（服务自检 · 计划任务每 10 分钟 curl 一次） ----
        if path == "/api/heartbeat":
            engine_ok = os.path.exists(os.path.normpath(os.path.join(HERE, "..", "..", "kanshan-kb", "scripts", "zhihu_diagnose.py")))
            return self._send(200, json.dumps({
                "ok": True,
                "status": "alive",
                "service": "看山·本地数据网关 v2",
                "secret": bool(SECRET),
                "diagnose_engine": "ready" if engine_ok else "missing",
                "cli": bool(find_cli()),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, ensure_ascii=False))

        # ---- 诊断（拉本人内容 → 调 kanshan-kb 诊断引擎 → 返回诊断 JSON） ----
        if path == "/api/diagnose":
            if not SECRET:
                return self._send(200, json.dumps({
                    "Code": 20001,
                    "Message": "本机未配置 Access Secret：把密钥粘贴到 frontend/secret.txt 后重启网关"
                }, ensure_ascii=False))
            days = 30
            try:
                days = min(90, max(7, int((q.get("days") or ["30"])[0])))
            except Exception:
                pass
            engine = os.path.normpath(os.path.join(HERE, "..", "..", "kanshan-kb", "scripts", "zhihu_diagnose.py"))
            if not os.path.exists(engine):
                return self._send(200, json.dumps({"Code": 90010, "Message": "诊断引擎缺失：kanshan-kb/scripts/zhihu_diagnose.py 不存在"}, ensure_ascii=False))
            # 1) 拉本人内容（复用官方 API + 60s 缓存），转 JSONL 喂诊断引擎
            url = (OFFICIAL + "/api/v1/user/contents?ContentType=all&Limit=50&Offset=0&SortField=ts&SortOrder=desc")
            st, body = http_get(url, official_headers())
            try:
                tmpdir = os.path.join(HERE, "_diagnose_tmp")
                os.makedirs(tmpdir, exist_ok=True)
                data = json.loads(body)
                items = ((data.get("Data") or {}).get("Items")) or []
                if not items:
                    return self._send(200, json.dumps({"Code": 90014, "Message": "官方 API 未返回内容条目", "raw": body[:200]}, ensure_ascii=False))
                src = os.path.join(tmpdir, "contents.jsonl")
                with open(src, "w", encoding="utf-8") as f:
                    for it in items:
                        f.write(json.dumps(it, ensure_ascii=False) + "\n")
                # 2) 调诊断引擎（--out 为目录，产出 diagnose.md）
                outdir = os.path.join(tmpdir, "out")
                r = subprocess.run(
                    [sys.executable, engine, "--in", src, "--out", outdir, "--days", str(days)],
                    capture_output=True, text=True, timeout=120,
                )
                out_md = os.path.join(outdir, "diagnose.md")
                if not os.path.exists(out_md):
                    return self._send(200, json.dumps({
                        "Code": 90011,
                        "Message": "诊断引擎未产出结果",
                        "stderr": (r.stderr or "")[-400:],
                    }, ensure_ascii=False))
                with open(out_md, "r", encoding="utf-8") as f:
                    report = f.read()
                return self._send(200, report, "text/markdown; charset=utf-8")
            except subprocess.TimeoutExpired:
                return self._send(200, json.dumps({"Code": 90012, "Message": "诊断超时（>120s）"}, ensure_ascii=False))
            except Exception as exc:
                return self._send(200, json.dumps({"Code": 90013, "Message": "诊断服务内部错误", "Detail": str(exc)[:240]}, ensure_ascii=False))

        # ---- 静态托管 ----
        fp = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = os.path.normpath(os.path.join(HERE, fp))
        if not fp.startswith(HERE):
            return self._send(404, b"not found")
        try:
            with open(fp, "rb") as f:
                ct = "text/html; charset=utf-8" if fp.endswith(".html") else "application/octet-stream"
                return self._send(200, f.read(), ct)
        except Exception:
            return self._send(404, b"not found")


if __name__ == "__main__":
    print("=" * 52)
    print("  看山 · 本地数据网关 v2（官方开放平台 API）")
    print("  浏览器打开 ->  http://localhost:%d/" % PORT)
    print("  密钥状态  ->  " + ("已配置 ✓" if SECRET else "未配置 ✗（把 Access Secret 存到 secret.txt 后重启）"))
    print("  zhihu-cli ->  " + ("已发现 ✓（公开账号流程会优先调用）" if find_cli() else "未发现（公开账号流程会明确提示并走公开数据兜底）"))
    print("  密钥获取  ->  https://developer.zhihu.com/profile")
    print("  ⚠️ secret.txt 不要上传/截图/发群")
    print("  保持本窗口开着（关闭 = 停止服务）")
    print("=" * 52)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        print("  服务已启动 ✓  http://localhost:%d/" % PORT)
        try:
            webbrowser.open("http://localhost:%d/" % PORT)
        except Exception:
            print("  浏览器未能自动打开，请手动访问上面的地址")
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    except Exception as exc:
        print("\n[X] 网关启动失败：%s" % exc)
        print("    可能是 8686 端口已被占用；关闭旧网关后重试。")
        input("按回车退出…")
