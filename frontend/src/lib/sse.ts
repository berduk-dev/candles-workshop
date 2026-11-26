import { ApiError, toApiError } from "./api";

/** Событие из потока ответа ассистента. */
export interface ChatEvent {
  type: string;
  [key: string]: unknown;
}

/** Читает ответ SSE по мере поступления. EventSource не подходит: он умеет только GET. */
export async function* readEvents(url: string, body: unknown): AsyncGenerator<ChatEvent> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    // Как в api.ts: человеку — понятный текст, а не «Failed to fetch» от браузера.
    throw new ApiError("Нет связи с сервером.", 0);
  }
  if (!response.ok || !response.body) throw await toApiError(response);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    let chunk: ReadableStreamReadResult<string>;
    try {
      chunk = await reader.read();
    } catch {
      throw new ApiError("Связь с сервером оборвалась посреди ответа.", 0);
    }
    if (chunk.done) return;
    buffer += chunk.value;
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? ""; // последний кусок может быть неполным
    for (const part of parts) {
      if (part.startsWith("data: ")) yield JSON.parse(part.slice(6)) as ChatEvent;
    }
  }
}
