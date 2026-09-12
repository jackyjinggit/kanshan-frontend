# kanshan-frontend · 看山创作校准引擎（前端 + 本地网关 + 数据兜底）

知乎黑客松「看山」项目的前端线：三屏校准引擎（轨迹时间轴 → 日/周/月卡 → 校准动作卡）+ 本地数据网关 + cookie+无头浏览器数据兜底。

## 组成

| 文件 | 职责 |
|---|---|
| `index.html` | 前端单页（三屏流程；REMOTE_HOST 双轨：本地打开显示网关指引，公网托管自动切在线提示+演示数据） |
| `server.py` | 本地网关（127.0.0.1:8686）：官方开放平台 API 代理 + 公开 V4 抓取 + **cookie+无头浏览器自动兜底**（裸抓 403 code10003 时起 Playwright 补数据，响应带 `Browser.used` 可追溯） |
| `zhihu_scrape_v4.py` | 独立兜底抓取脚本（网关子进程调用，也可单独跑：`python zhihu_scrape_v4.py --uid <url_token> --cookie <cookie.json>`） |

## 运行

```bash
# 1) 同目录放 secret.txt（知乎开放平台 Access Secret，方式一用，可选）
# 2) 上一级目录放 知乎cookie*.json（浏览器导出 cookie 数组，方式二兜底用，可选）
python server.py
# → http://localhost:8686/
```

## 安全红线

- cookie / secret 文件**永不入库**（.gitignore 已覆盖 `*cookie*.json`、`secret.txt`）
- 凭证只留本机：Access Secret 不进前端，cookie 值不回显不落日志
- 兜底抓取仅只读浏览，不关注/不点赞/不发帖；限个人研究与授权演示用途

## 相关

- 主库（知识库/方法卡）：`kanshan-kb`
- 在线演示：PushWebly 静态托管版（数据读取需本机网关；在线可体验演示数据）
