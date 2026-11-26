import { toApiError } from "./api";

/** Событие из потока ответа ассистента. */
export interface ChatEvent {
  type: string;
  [key: string]: unknown;
}

/** Читает ответ SSE по мере поступления. EventSource не подходит: он умеет только GET. */
export async function* readEvents(url: string, body: unknown): AsyncGenerator<ChatEvent> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) throw await toApiError(response);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += value;
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? ""; // последний кусок может быть неполным
    for (const part of parts) {
      if (part.startsWith("data: ")) yield JSON.parse(part.slice(6)) as ChatEvent;
    }
  }
}
