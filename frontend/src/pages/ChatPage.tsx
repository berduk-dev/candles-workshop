import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  CalendarPlus,
  Calculator,
  ChartColumn,
  FileSpreadsheet,
  Paperclip,
  Plus,
  Square,
  TriangleAlert,
  X,
} from "lucide-react";
import { type FormEvent, type KeyboardEvent, type RefObject, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router";

import { ChatMessage } from "../components/ChatMessage";
import { Button, ErrorBox, Logo } from "../components/ui";
import { api } from "../lib/api";
import { readEvents } from "../lib/sse";
import type { Action, ChatFile, ChatMessage as Message, CodeRun, Conversation, ToolUse } from "../lib/types";

const EXAMPLES = [
  [TriangleAlert, "Чего не хватит на задания этой недели?"],
  [CalendarPlus, "Запланируй 200 свечей «Лаванда» на пятницу"],
  [ChartColumn, "Построй график расхода воска по неделям за квартал"],
  [Calculator, "Как изменится себестоимость свечи «Лаванда», если воск подорожает на 15%?"],
] as const;

export function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: conversations } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.get<Conversation[]>("/chat/conversations"),
  });
  const input = useRef<HTMLTextAreaElement>(null);
  const create = useMutation({
    // Второй пустой диалог сервер не создаёт, а возвращает имеющийся. Если он уже открыт — ставим курсор.
    mutationFn: () => api.post<Conversation>("/chat/conversations"),
    onSuccess: async (c) => {
      if (String(c.id) === id) return input.current?.focus();
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      await navigate(`/chat/${c.id}`);
    },
  });
  const remove = useMutation({
    mutationFn: (chatId: number) => api.delete(`/chat/conversations/${chatId}`),
    onSuccess: async (_, chatId) => {
      await queryClient.invalidateQueries({ queryKey: ["conversations"] });
      if (String(chatId) === id) await navigate("/chat");
    },
  });

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line md:flex">
        <div className="flex items-center justify-between py-3 pr-3 pl-5">
          <h1 className="font-semibold">Диалоги</h1>
          <Button
            variant="secondary"
            icon={Plus}
            aria-label="Новый диалог"
            title="Новый диалог"
            className="w-9 px-0!"
            disabled={create.isPending}
            onClick={() => create.mutate()}
          />
        </div>
        <div className="flex-1 space-y-0.5 overflow-auto px-3 pb-3">
          <ErrorBox error={create.error ?? remove.error} />
          {conversations?.length === 0 && (
            <p className="px-2 text-xs text-muted">Здесь появятся ваши диалоги.</p>
          )}
          {conversations?.map((c) => (
            <div key={c.id} className="group relative">
              <NavLink
                to={`/chat/${c.id}`}
                title={c.title}
                className={({ isActive }) =>
                  `block truncate rounded-lg px-2.5 py-2 pr-8 transition-colors ${isActive ? "bg-paper font-medium" : "text-ink/75 hover:bg-paper"}`
                }
              >
                {c.title}
              </NavLink>
              <button
                type="button"
                aria-label={`Удалить диалог «${c.title}»`}
                className="absolute inset-y-0 right-1.5 my-auto grid size-6 place-items-center rounded-md text-muted opacity-0 group-hover:opacity-100 hover:bg-line hover:text-bad focus:opacity-100"
                onClick={() => remove.mutate(c.id)}
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      </aside>
      {id ? (
        <Dialog key={id} conversationId={Number(id)} input={input} />
      ) : (
        <div className="m-auto flex max-w-md flex-col items-center p-6 text-center">
          <Logo className="size-12" />
          <h2 className="mt-5 text-2xl font-semibold tracking-tight">Ассистент мастерской</h2>
          <p className="mt-2 mb-6 text-muted">
            Спросите об остатках, заданиях, себестоимости — или попросите запланировать задание.
          </p>
          <Button icon={Plus} disabled={create.isPending} onClick={() => create.mutate()}>
            Начать диалог
          </Button>
        </div>
      )}
    </div>
  );
}

function Dialog({
  conversationId,
  input,
}: {
  conversationId: number;
  input: RefObject<HTMLTextAreaElement | null>;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["messages", conversationId];
  // Вопрос и ответ, которые идут прямо сейчас; после завершения их заменяет версия из базы.
  const [live, setLive] = useState<[Message, Message] | null>(null);
  const loadMessages = () => api.get<Message[]>(`/chat/conversations/${conversationId}/messages`);
  const { data: saved, error } = useQuery({
    queryKey,
    queryFn: loadMessages,
    enabled: !live, // пока ответ идёт, сообщения из базы задвоили бы его
    // Вернулись в диалог, где ответ ещё готовится: подождём его сохранения.
    refetchInterval: (q) => (q.state.data?.some((m) => m.status === "streaming") ? 2000 : false),
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [problem, setProblem] = useState<unknown>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [live, saved]);

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.post<ChatFile>(`/chat/conversations/${conversationId}/files`, form);
    },
    onSuccess: (file) => setFiles((all) => [...all, file]),
  });

  const patch = (change: (answer: Message) => Message) =>
    setLive((pair) => pair && [pair[0], change(pair[1])]);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || live) return;
    setProblem(null);
    setText("");
    const blank = { model: null, used_fallback: false, meta: {}, files: [], actions: [] };
    setLive([
      { ...blank, id: -1, role: "user", content: trimmed, status: "complete", files },
      { ...blank, id: -2, role: "assistant", content: "", status: "streaming" },
    ]);
    setFiles([]);
    try {
      const url = `/api/chat/conversations/${conversationId}/messages`;
      const body = { text: trimmed, file_ids: files.map((f) => f.id) };
      for await (const event of readEvents(url, body)) {
        switch (event.type) {
          case "model":
            patch((m) => ({
              ...m,
              model: event.model as string,
              used_fallback: event.fallback as boolean,
            }));
            break;
          case "text":
            setBusy(null);
            patch((m) => ({ ...m, content: m.content + (event.delta as string) }));
            break;
          case "tool_started":
            setBusy(`${event.label as string}…`);
            break;
          case "tool":
            setBusy(null);
            patch((m) => ({
              ...m,
              meta: { ...m.meta, tools: [...(m.meta.tools ?? []), event as unknown as ToolUse] },
            }));
            break;
          case "code_started":
            setBusy("Выполняю расчёт…");
            break;
          case "code":
            setBusy(null);
            patch((m) => ({
              ...m,
              meta: { ...m.meta, code: [...(m.meta.code ?? []), event as unknown as CodeRun] },
            }));
            break;
          case "file":
            patch((m) => ({ ...m, files: [...m.files, event.file as ChatFile] }));
            break;
          case "legend":
            patch((m) => ({
              ...m,
              meta: { ...m.meta, legend: event.legend as Record<string, string> },
            }));
            break;
          case "action":
            patch((m) => ({ ...m, actions: [...m.actions, event.action as Action] }));
            break;
          case "notice":
            setProblem(new Error(event.text as string));
            break;
          case "error":
            patch((m) => ({ ...m, status: "error", meta: { ...m.meta, error: event.text as string } }));
            break;
        }
      }
    } catch (e) {
      setProblem(e);
    }
    setBusy(null);
    await queryClient.fetchQuery({ queryKey, queryFn: loadMessages }).catch(setProblem);
    setLive(null); // сразу после загрузки: позже ответ успел бы показаться дважды
    await queryClient.invalidateQueries({ queryKey: ["conversations"] });
    await queryClient.invalidateQueries({ queryKey: ["memory"] });
  }

  const stop = () => void api.post(`/chat/conversations/${conversationId}/stop`).catch(setProblem);

  function onKey(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(text);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(text);
  }

  const shown = [...(saved ?? []), ...(live ?? [])];
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex-1 overflow-auto" aria-live="polite">
        <div className="mx-auto max-w-3xl space-y-7 px-4 py-6">
          <ErrorBox error={error} />
          {saved && shown.length === 0 && (
            <div className="pt-[12vh]">
              <h2 className="mb-5 text-center text-2xl font-semibold tracking-tight">С чего начнём?</h2>
              <div className="grid gap-2.5 sm:grid-cols-2">
                {EXAMPLES.map(([Icon, example]) => (
                  <button
                    key={example}
                    type="button"
                    className="flex gap-3 rounded-xl border border-line p-4 text-left shadow-raised transition-colors hover:border-wax/40 hover:bg-wax-soft/50"
                    onClick={() => void ask(example)}
                  >
                    <Icon className="mt-0.5 size-4 shrink-0 text-wax" aria-hidden="true" />
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}
          {shown.map((m) => (
            <ChatMessage key={m.id} message={m} busy={m.id === -2 ? busy : null} />
          ))}
          <ErrorBox error={problem ?? upload.error} />
          <div ref={bottom} />
        </div>
      </div>
      <div className="px-4 pb-4">
        <form
          onSubmit={submit}
          className="mx-auto max-w-3xl rounded-2xl border border-line bg-card p-2 shadow-[0_4px_16px_rgb(27_26_24/0.06)] transition-colors focus-within:border-muted/50"
        >
          {files.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2 text-xs">
              {files.map((f) => (
                <span key={f.id} className="flex items-center gap-1.5 rounded-md bg-paper px-2 py-1">
                  <FileSpreadsheet className="size-3.5 text-ok" aria-hidden="true" />
                  {f.filename}
                </span>
              ))}
            </div>
          )}
          <div className="flex items-end gap-1.5">
            <input
              ref={fileInput}
              type="file"
              accept=".csv,.xlsx"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) upload.mutate(file);
                e.target.value = "";
              }}
            />
            <Button
              variant="ghost"
              icon={Paperclip}
              className="w-9 px-0!"
              disabled={upload.isPending || !!live}
              onClick={() => fileInput.current?.click()}
              aria-label="Прикрепить CSV или XLSX"
              title="Прикрепить CSV или XLSX"
            />
            <textarea
              ref={input}
              autoFocus
              className="field-sizing-content max-h-40 min-h-9 flex-1 resize-none px-1 py-2 outline-none! placeholder:text-muted"
              rows={1}
              placeholder="Спросите ассистента…"
              aria-label="Вопрос ассистенту"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
            />
            {live ? (
              <Button
                variant="danger"
                icon={Square}
                aria-label="Остановить"
                title="Остановить"
                className="w-9 rounded-xl px-0!"
                onClick={stop}
              />
            ) : (
              <Button
                type="submit"
                icon={ArrowUp}
                aria-label="Отправить"
                title="Отправить"
                className="w-9 rounded-xl px-0!"
                disabled={!text.trim()}
              />
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
