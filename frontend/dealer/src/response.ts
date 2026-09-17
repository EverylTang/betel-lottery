export async function responseBody<T>(response: Response): Promise<T> {
  const body = await response.text();
  try {
    const data: unknown = JSON.parse(body);
    if (data === null || typeof data !== 'object') throw new Error();
    return data as T;
  } catch {
    throw new Error(response.ok ? '服务返回了无法识别的数据，请稍后重试' : `服务异常（${response.status}），请稍后重试`);
  }
}

export function errorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== 'object' || !('detail' in data)) return fallback;
  const detail = data.detail;
  if (typeof detail === 'string') return detail || fallback;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message || fallback;
  return fallback;
}

export async function requestBody<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const data = await responseBody<T>(response);
  if (!response.ok) throw new Error(errorMessage(data, '请求失败'));
  return data;
}
