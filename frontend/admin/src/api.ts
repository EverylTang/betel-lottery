import { Modal } from "antd";

const base = import.meta.env.VITE_API_BASE ?? '';
const tokenKey = 'betel-admin-token';
const visibleErrorDialogs = new Set<string>();

export const adminToken = () => localStorage.getItem(tokenKey);
export const setAdminToken = (token: string) => localStorage.setItem(tokenKey, token);

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function apiErrorMessage(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      const error = item as { loc?: unknown[]; msg?: string };
      const field = error.loc?.filter((part) => part !== "body").join(".");
      return field ? `${field}：${error.msg || "参数不合法"}` : error.msg || "提交参数不合法";
    }).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  return `请求失败（HTTP ${status}）`;
}

function showApiError(detail: string) {
  if (visibleErrorDialogs.has(detail)) return;
  visibleErrorDialogs.add(detail);
  Modal.error({
    title: "操作未完成",
    content: detail,
    okText: "知道了",
    afterClose: () => visibleErrorDialogs.delete(detail),
  });
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(adminToken() ? { Authorization: `Bearer ${adminToken()}` } : {}), ...init.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = apiErrorMessage(payload, response.status);
    showApiError(detail);
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return undefined as T;
  }
  return response.json();
}

export async function download(path: string, filename: string) {
  const response = await fetch(`${base}${path}`, { headers: adminToken() ? { Authorization: `Bearer ${adminToken()}` } : {} });
  if (!response.ok) {
    const detail = apiErrorMessage(await response.json().catch(() => null), response.status);
    showApiError(detail);
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await response.blob());
  const anchor = Object.assign(document.createElement('a'), { href: url, download: filename });
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function uploadImage(file: File): Promise<{ url: string }> {
  const body = new FormData();
  body.append('file', file);
  const response = await fetch(`${base}/api/admin/uploads/images`, {
    method: 'POST',
    headers: adminToken() ? { Authorization: `Bearer ${adminToken()}` } : {},
    body,
  });
  if (!response.ok) {
    const detail = apiErrorMessage(await response.json().catch(() => null), response.status);
    showApiError(detail);
    throw new Error(detail);
  }
  return response.json();
}
