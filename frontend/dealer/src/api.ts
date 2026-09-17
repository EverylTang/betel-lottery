import { requestBody } from './response';

const tokenKey = 'betel-dealer-token';
let token = localStorage.getItem(tokenKey) || '';

function headers(input?: HeadersInit) {
  const result = new Headers({ 'Content-Type': 'application/json' });
  if (token) result.set('Authorization', `Bearer ${token}`);
  new Headers(input).forEach((value, key) => result.set(key, value));
  return result;
}

export function currentToken() { return token; }
export function setToken(value: string) { token = value; localStorage.setItem(tokenKey, value); }
export function clearToken() { token = ''; localStorage.removeItem(tokenKey); }
export function api<T>(path: string, init?: RequestInit) { return requestBody<T>(path, { ...init, headers: headers(init?.headers) }); }
