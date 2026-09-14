import { execFile } from 'node:child_process';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { CREDENTIALS } from './credentials.mjs';

export const userInterfaces = [
  ['contents', '我的创作', '/api/v1/user/contents'],
  ['followees', '我的关注', '/api/v1/user/followees'],
  ['favlists', '收藏夹', '/api/v1/user/favlists'],
  ['favlist_contents', '收藏内容', '/api/v1/user/favlist_contents'],
  ['collections', '近期收藏', '/api/v1/user/collections'],
].map(([id, name, endpoint]) => ({ id, name, endpoint }));

function keychain(service, account) {
  return new Promise((resolve) => {
    execFile('/usr/bin/security', ['find-generic-password', '-s', service, '-a', account, '-w'], (error, stdout) => {
      resolve(!error ? stdout.toString().trim() : null);
    });
  });
}

function runCurl(lines) {
  /* 跨平台版：用 Node 内置 fetch 取代外部 curl（Windows 上没有 /usr/bin/curl，spawn 会崩进程） */
  let url = null;
  let method = 'GET';
  const headers = {};
  let data = null;
  for (const line of lines) {
    let m;
    if ((m = line.match(/^url = "(.*)"$/))) url = m[1];
    else if ((m = line.match(/^request = "(.*)"$/))) method = m[1];
    else if ((m = line.match(/^header = "([^:]+): ?(.*)"$/))) headers[m[1]] = m[2];
    else if ((m = line.match(/^data = "(.*)"$/))) data = m[1];
  }
  if (!url) return Promise.reject(new Error('网络请求配置无效'));
  return (async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, { method, headers, body: method === 'GET' ? undefined : data, signal: controller.signal });
      const text = await response.text();
      try { return JSON.parse(text); }
      catch { throw new Error('知乎开放平台返回了无法解析的响应'); }
    } catch (error) {
      if (error instanceof Error && error.message.includes('无法解析')) throw error;
      if (error.name === 'AbortError') throw new Error('网络请求超时（30 秒）');
      throw new Error(error.cause?.message || error.message || '网络请求失败');
    } finally {
      clearTimeout(timer);
    }
  })();
}

function safe(value) {
  if (!value || /[\r\n"\\]/.test(value)) throw new Error('凭证格式无效');
  return value;
}

function payloadError(payload, fallback) {
  const data = payload?.data ?? payload?.Data;
  const message = typeof data === 'string' ? data : data?.message || payload?.message || payload?.Message || fallback;
  const error = new Error(String(message).slice(0, 200));
  error.code = payload?.code ?? payload?.Code ?? 'OAUTH_FAILED';
  return error;
}

function cookieId(request) {
  const value = (request.headers.cookie || '').split(';').map((item) => item.trim()).find((item) => item.startsWith('zhihu_hackathon_session='));
  return value ? decodeURIComponent(value.slice(value.indexOf('=') + 1)) : null;
}

function equal(left, right) {
  const a = Buffer.from(String(left || ''));
  const b = Buffer.from(String(right || ''));
  return a.length === b.length && timingSafeEqual(a, b);
}

function firstItem(payload) {
  return Array.isArray(payload?.Data?.Items) ? payload.Data.Items[0] || null : null;
}

function userRequestConfig(accessSecret, oauthToken, url) {
  return [
    'silent', 'show-error', 'max-time = 30', 'request = "GET"', `url = "${url}"`,
    `header = "Authorization: Bearer ${safe(accessSecret)}"`,
    `header = "X-OAuth-Token: ${safe(oauthToken)}"`,
    `header = "X-Request-Timestamp: ${Math.floor(Date.now() / 1000)}"`,
    'header = "Content-Type: application/json"',
  ];
}

function userTokenConfig(token, url) {
  /* OAuth 应用正确姿势：以「用户 access_token」为 Bearer，数据跟授权账号走。
     Access Secret 只标识密钥所属账号（开发者本人），不能替代用户 token（官方文档实证）。 */
  return [
    'silent', 'show-error', 'max-time = 30', 'request = "GET"', `url = "${url}"`,
    `header = "Authorization: Bearer ${safe(token)}"`,
    'header = "Content-Type: application/json"',
  ];
}

async function runUserApi(accessSecret, oauthToken, url) {
  /* 先用用户 token（动态身份），失败回退 Access Secret 口径（保底） */
  if (oauthToken) {
    try { return await runCurl(userTokenConfig(oauthToken, url)); }
    catch (e) { /* fallthrough 到旧口径 */ }
  }
  return runCurl(userRequestConfig(accessSecret, oauthToken, url));
}

export function createOAuth(config) {
  /* 会话持久化：沙箱闲置重启会清内存，token 落盘后重启自动恢复（授权不再「不稳定」） */
  const sessionStore = new URL('../sessions.json', import.meta.url);
  const sessions = new Map();
  try {
    for (const [k, v] of JSON.parse(readFileSync(sessionStore, 'utf8'))) sessions.set(k, v);
  } catch { /* 首次启动无文件 */ }
  function persistSessions() {
    try { writeFileSync(sessionStore, JSON.stringify([...sessions]), 'utf8'); } catch { /* 只读环境忽略 */ }
  }
  const oauthConfig = config.oauth;

  function session(request, response) {
    let id = cookieId(request);
    let current = id ? sessions.get(id) : null;
    if (!current) {
      id = randomBytes(24).toString('base64url');
      current = { id, state: null, token: null, expiresAt: null, profile: null, stateVerified: null, error: null };
      sessions.set(id, current);
      response.setHeader('Set-Cookie', `zhihu_hackathon_session=${id}; HttpOnly; Secure; SameSite=None; Path=/; Max-Age=2592000`);
    }
    return current;
  }

  async function credentials() {
    const [appKey, accessSecret] = await Promise.all([
      process.env.ZHIHU_OAUTH_APP_KEY || CREDENTIALS.appKey || keychain(oauthConfig.credentialService, oauthConfig.credentialAccount),
      process.env.ZHIHU_ACCESS_SECRET || CREDENTIALS.accessSecret || keychain('zhihu-cli', 'access-secret'),
    ]);
    return { appKey, accessSecret };
  }

  async function status(request, response) {
    const current = session(request, response);
    const creds = await credentials();
    if (current.expiresAt && current.expiresAt <= Date.now()) {
      current.token = null;
      current.profile = null;
      current.error = { code: 'TOKEN_EXPIRED', message: '授权已过期，请重新连接。' };
    }
    return {
      configured: Boolean(creds.appKey && creds.accessSecret && oauthConfig.redirectUri),
      callbackConfigured: Boolean(oauthConfig.redirectUri),
      authorized: Boolean(current.token),
      appId: oauthConfig.appId,
      redirectUri: oauthConfig.redirectUri,
      profile: current.profile,
      stateVerified: current.stateVerified,
      expiresAt: current.expiresAt ? new Date(current.expiresAt).toISOString() : null,
      error: current.error,
      interfaces: userInterfaces,
    };
  }

  async function start(request, response) {
    const current = session(request, response);
    if (!oauthConfig.redirectUri) {
      throw Object.assign(new Error('本地地址无法完成知乎登录。请先部署应用并配置公网回调地址。'), { code: 'DEPLOYMENT_REQUIRED' });
    }
    const { appKey } = await credentials();
    if (!appKey) throw Object.assign(new Error('OAuth app_key 尚未配置'), { code: 'APP_KEY_REQUIRED' });
    current.state = randomBytes(24).toString('base64url');
    current.error = null;
    const url = new URL('https://openapi.zhihu.com/authorize');
    url.searchParams.set('redirect_uri', oauthConfig.redirectUri);
    url.searchParams.set('app_id', oauthConfig.appId);
    url.searchParams.set('response_type', 'code');
    url.searchParams.set('state', current.state);
    return url.toString();
  }

  async function callback(request, response, url) {
    const current = session(request, response);
    const code = url.searchParams.get('authorization_code') || url.searchParams.get('code');
    const returnedState = url.searchParams.get('state');
    if (!code) throw Object.assign(new Error('回调缺少 authorization_code'), { code: 'CODE_MISSING' });
    if (returnedState && !equal(returnedState, current.state)) {
      throw Object.assign(new Error('state 校验失败'), { code: 'STATE_MISMATCH' });
    }
    const { appKey, accessSecret } = await credentials();
    if (!appKey || !accessSecret) throw new Error('后端凭证配置不完整');
    const form = new URLSearchParams({
      app_id: oauthConfig.appId,
      app_key: safe(appKey),
      grant_type: 'authorization_code',
      redirect_uri: oauthConfig.redirectUri,
      code: safe(code),
    }).toString();
    const payload = await runCurl([
      'silent', 'show-error', 'max-time = 20', 'request = "POST"',
      'url = "https://openapi.zhihu.com/access_token"',
      'header = "Content-Type: application/x-www-form-urlencoded"', `data = "${form}"`,
    ]);
    const token = payload?.access_token || payload?.data?.access_token || payload?.Data?.access_token;
    if (!token) throw payloadError(payload, '未获得 OAuth access token');
    const expiresIn = Number(payload?.expires_in ?? payload?.data?.expires_in ?? payload?.Data?.expires_in);
    current.token = token;
    current.expiresAt = Number.isFinite(expiresIn) ? Date.now() + expiresIn * 1000 : null;
    current.stateVerified = Boolean(returnedState);
    current.state = null;
    current.error = null;
    persistSessions();

    try {
      const profilePayload = await runUserApi(accessSecret, token, 'https://openapi.zhihu.com/user');
      const source = profilePayload?.data || profilePayload?.Data || profilePayload?.user || null;
      if (source && typeof source === 'object') {
        current.profile = {
          name: source.name || source.Fullname || source.fullname || null,
          avatarUrl: source.avatar_url || source.AvatarUrl || null,
          headline: source.headline || source.Headline || null,
          url: source.url || source.Url || null,
        };
      }
    } catch { current.profile = null; }
  }

  async function runAll(request, response) {
    const current = session(request, response);
    if (!current.token) throw Object.assign(new Error('请先完成知乎账号授权'), { code: 'LOGIN_REQUIRED' });
    const { accessSecret } = await credentials();
    if (!accessSecret) throw new Error('开放平台 Access Secret 未配置');
    const context = {};
    const results = [];
    for (const definition of userInterfaces) {
      let query = { Limit: '1' };
      if (definition.id === 'contents') query = { ...query, ContentType: 'all', Offset: '0', SortField: 'ts', SortOrder: 'desc' };
      if (definition.id === 'followees') query.Offset = '0';
      if (definition.id === 'favlist_contents') {
        if (!context.favlistToken) {
          results.push({ ...definition, status: 'empty', item: null, message: '账号没有可用于测试的收藏夹。' });
          continue;
        }
        query = { ...query, FavlistUrlToken: String(context.favlistToken), Offset: '0' };
      }
      try {
        const payload = await runUserApi(
          accessSecret,
          current.token,
          `https://developer.zhihu.com${definition.endpoint}?${new URLSearchParams(query)}`,
        );
        if (payload?.Code !== 0) throw payloadError(payload, '用户数据接口失败');
        const item = firstItem(payload);
        if (definition.id === 'favlists' && item?.UrlToken) context.favlistToken = item.UrlToken;
        results.push({ ...definition, status: item ? 'success' : 'empty', item, message: item ? null : '接口成功但没有数据。' });
      } catch (error) {
        results.push({ ...definition, status: 'error', item: null, message: error.message });
      }
    }
    return results;
  }

  function logout(request, response) {
    const current = session(request, response);
    current.token = null; current.expiresAt = null; current.profile = null; current.state = null; current.stateVerified = null; current.error = null;
    persistSessions();
  }

  function record(request, response, error) {
    session(request, response).error = { code: String(error.code || 'OAUTH_FAILED'), message: String(error.message).slice(0, 200) };
  }

  async function fetchContents(request, response, limit) {
    const current = session(request, response);
    if (!current.token) throw Object.assign(new Error('请先完成知乎账号授权'), { code: 'LOGIN_REQUIRED' });
    const { accessSecret } = await credentials();
    if (!accessSecret) throw new Error('开放平台 Access Secret 未配置');
    const n = Math.min(100, Math.max(1, Number(limit) || 50));
    const lines = userRequestConfig(accessSecret, current.token,
      `https://openapi.zhihu.com/api/v1/user/contents?ContentType=all&Limit=${n}&Offset=0&SortField=ts&SortOrder=desc`);
    const payload = await runUserApi(accessSecret, current.token, `https://openapi.zhihu.com/api/v1/user/contents?ContentType=all&Limit=${n}&Offset=0&SortField=ts&SortOrder=desc`);
    const data = payload && (payload.data || payload.Data) || null;
    const items = data && Array.isArray(data.Items) ? data.Items : [];
    return { items, total: data ? (data.Paging && data.Paging.Totals) || items.length : items.length };
  }

  return { status, start, callback, runAll, logout, record, fetchContents };
}
