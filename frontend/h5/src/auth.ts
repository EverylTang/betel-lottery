import { ref } from 'vue';
import { errorMessage, requestBody, responseBody } from './response';

export const token = ref(localStorage.getItem('betel-h5-token') || '');

function authHeaders(input?: HeadersInit) {
  const headers = new Headers({ 'Content-Type': 'application/json', Authorization: `Bearer ${token.value}` });
  new Headers(input).forEach((value, key) => headers.set(key, value));
  return headers;
}

export async function login() {
  const hashParams = new URLSearchParams(location.hash.replace(/^#/, ''));
  const hashToken = hashParams.get('wechat_token');
  const queryToken = new URLSearchParams(location.search).get('wechat_token');
  if (hashToken || queryToken) {
    token.value = hashToken || queryToken || '';
    localStorage.setItem('betel-h5-token', token.value);
    const query = new URLSearchParams(location.search);
    query.delete('wechat_token');
    history.replaceState(null, '', `${location.pathname}${query.size ? `?${query}` : ''}`);
    return;
  }
  const oauthError = hashParams.get('wechat_error');
  if (oauthError) {
    history.replaceState(null, '', `${location.pathname}${location.search}`);
    throw new Error(oauthError);
  }
  let mode = { oauth_enabled: false };
  try { mode = await requestBody<{ oauth_enabled: boolean }>('/api/h5/auth/mode'); } catch { }
  if (mode.oauth_enabled) {
    const currentTarget = `${location.pathname}${location.search}`;
    const authorizeUrl = currentTarget && currentTarget !== '/'
      ? `/api/h5/auth/wechat/authorize?return_to=${encodeURIComponent(currentTarget)}`
      : '/api/h5/auth/wechat/authorize';
    location.assign(authorizeUrl);
    throw new Error('正在跳转微信授权');
  }
  const response = await fetch('/api/h5/auth/dev-login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ openid: 'h5-preview-user', nickname: '扫码用户' }),
  });
  if (!response.ok) throw new Error('登录状态创建失败');
  const data = await responseBody<{ access_token: string }>(response);
  if (typeof data.access_token !== 'string' || !data.access_token) throw new Error('登录状态创建失败');
  token.value = data.access_token;
  localStorage.setItem('betel-h5-token', token.value);
}

export async function consumerFetch(path: string, init: RequestInit = {}) {
  let response = await fetch(path, { ...init, headers: authHeaders(init.headers) });
  if (response.status !== 401) return response;
  const method = (init.method || 'GET').toUpperCase();
  if (method !== 'GET') return response;
  token.value = '';
  localStorage.removeItem('betel-h5-token');
  await login();
  response = await fetch(path, { ...init, headers: authHeaders(init.headers) });
  return response;
}

export async function consumerRequest<T>(path: string, init: RequestInit = {}) {
  const response = await consumerFetch(path, init);
  const data = await responseBody<T>(response);
  if (!response.ok) throw new Error(errorMessage(data, '请求失败'));
  return data;
}

