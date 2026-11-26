/** Ошибка с человеческим текстом от бэкенда — её и показываем пользователю. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function toApiError(response: Response): Promise<ApiError> {
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  const text =
    typeof body?.detail === "string" ? body.detail : "Сервер не отвечает. Попробуйте ещё раз.";
  return new ApiError(text, response.status);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: body !== undefined && !isForm ? { "Content-Type": "application/json" } : undefined,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Нет связи с сервером.", 0);
  }
  if (!response.ok) throw await toApiError(response);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  delete: (path: string) => request<void>("DELETE", path),
};

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Что-то пошло не так.";
}
