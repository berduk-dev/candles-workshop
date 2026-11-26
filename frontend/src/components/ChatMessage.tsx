import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, CircleStop, Code, FileText, TriangleAlert, Wrench } from "lucide-react";
import Markdown, { defaultUrlTransform } from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";

import { api } from "../lib/api";
import type { Action, ChatFile, ChatMessage as Message } from "../lib/types";
import { Badge, Button, ErrorBox, linkClass, Logo } from "./ui";

export function ChatMessage({ message, busy }: { message: Message; busy?: string | null }) {
  if (message.role === "user") {
    return (
      <div className="ml-auto max-w-[80%]">
        <div className="ml-auto w-fit rounded-2xl rounded-br-md bg-paper px-4 py-2.5 whitespace-pre-wrap">
          {message.content}
        </div>
        <Files files={message.files} />
      </div>
    );
  }
  // Итог действия уже виден в карточке; запись в диалоге нужна только самому ассистенту.
  if (message.meta.note) return null;
  const { tools = [], code = [], legend = {}, error } = message.meta;
  return (
    <div className="flex gap-3.5" data-testid="answer">
      <Logo className="size-7 shrink-0" />
      <div className="min-w-0 flex-1 space-y-3">
        <div className="markdown pt-0.5">
          {message.content ? (
            <AnswerText text={message.content} files={message.files} />
          ) : (
            !error && <p className="text-muted">{message.status === "streaming" ? "Думаю…" : "Нет ответа"}</p>
          )}
          {busy && <p className="animate-pulse text-sm text-muted">{busy}</p>}
          {message.status === "interrupted" && (
            <p className="flex items-center gap-1.5 text-sm text-warn">
              <CircleStop className="size-4" aria-hidden="true" />
              Ответ прерван — показана только его часть.
            </p>
          )}
        </div>
        {error && <ErrorBox error={new Error(error)} />}
        <Files files={message.files} legend={legend} />
        {message.actions.map((a) => (
          <ActionCard key={a.id} action={a} />
        ))}
        <footer className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {message.model && (
            <Badge tone={message.used_fallback ? "warn" : "muted"}>
              {message.model}
              {message.used_fallback && " · запасная"}
            </Badge>
          )}
          {tools.map((t, i) => (
            <details key={i} className="min-w-0 [&[open]]:basis-full">
              <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                <Badge tone={t.ok ? "muted" : "bad"}>
                  {t.ok ? (
                    <Wrench className="size-3.5" aria-hidden="true" />
                  ) : (
                    <TriangleAlert className="size-3.5" aria-hidden="true" />
                  )}
                  {t.label}
                  <ChevronDown className="size-3.5 opacity-60" aria-hidden="true" />
                </Badge>
              </summary>
              <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-paper p-3 whitespace-pre-wrap">
                {t.name}({t.args}){"\n"}
                {t.result}
              </pre>
            </details>
          ))}
          {code.map((c, i) => (
            <details key={`c${i}`} className="min-w-0 [&[open]]:basis-full">
              <summary className="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                <Badge tone={c.failed ? "bad" : "muted"}>
                  <Code className="size-3.5" aria-hidden="true" />
                  {c.failed ? "Код с ошибкой" : "Показать код"}
                  <ChevronDown className="size-3.5 opacity-60" aria-hidden="true" />
                </Badge>
              </summary>
              <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-ink p-3 text-paper">{c.code}</pre>
              {c.logs && (
                <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-paper p-3 whitespace-pre-wrap">
                  {c.logs}
                </pre>
              )}
            </details>
          ))}
        </footer>
      </div>
    </div>
  );
}

/** Модель ссылается на файлы внутри своего контейнера (sandbox:/mnt/data/…). Такие ссылки
 *  ведём на наш файл с тем же именем. Картинки из интернета не показываем вовсе: адрес картинки
 *  мог бы унести данные наружу. */
function AnswerText({ text, files }: { text: string; files: ChatFile[] }) {
  const local = (url: string) => {
    const name = decodeURIComponent(url.split("/").pop() ?? "");
    return files.find((f) => f.filename === name)?.url;
  };
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => (url.startsWith("sandbox:") ? (local(url) ?? "") : defaultUrlTransform(url))}
      components={{
        img: () => null,
        a: ({ href, children }) =>
          href ? (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ) : (
            <span>{children}</span>
          ),
      }}
    >
      {text}
    </Markdown>
  );
}

function Files({ files, legend = {} }: { files: ChatFile[]; legend?: Record<string, string> }) {
  if (files.length === 0) return null;
  const codes = Object.entries(legend);
  return (
    <div className="mt-1 space-y-2">
      {files.map((f) =>
        f.mime.startsWith("image/") ? (
          <figure key={f.id} className="max-w-xl">
            <a href={f.url} target="_blank" rel="noreferrer">
              <img src={f.url} alt={f.filename} className="rounded-xl border border-line" />
            </a>
            <figcaption className="mt-1.5 text-xs text-muted">
              <a className="underline underline-offset-2" href={f.url} download={f.filename}>
                Скачать {f.filename}
              </a>
              {codes.length > 0 && (
                <span> · Коды на графике: {codes.map(([c, name]) => `${c} — ${name}`).join("; ")}</span>
              )}
            </figcaption>
          </figure>
        ) : (
          <a
            key={f.id}
            href={f.url}
            download={f.filename}
            className="mr-2 inline-flex items-center gap-2 rounded-lg border border-line bg-card px-3 py-2 text-sm shadow-raised transition-colors hover:bg-paper"
          >
            <FileText className="size-4 text-muted" aria-hidden="true" />
            {f.filename}
          </a>
        ),
      )}
    </div>
  );
}

const ACTION_STATUS = {
  confirmed: ["ok", "Выполнено"],
  rejected: ["muted", "Отклонено"],
  expired: ["muted", "Устарело"],
  failed: ["bad", "Не выполнено"],
} as const;

function ActionCard({ action }: { action: Action }) {
  const queryClient = useQueryClient();
  const decide = useMutation({
    mutationFn: (verb: "confirm" | "reject") => api.post(`/chat/actions/${action.id}/${verb}`),
    onSettled: () => queryClient.invalidateQueries(),
  });
  const { preview } = action;
  return (
    <section
      className="overflow-hidden rounded-xl border border-line shadow-raised"
      aria-label="Предложение ассистента"
    >
      <div className="flex items-center justify-between gap-3 border-b border-line bg-wax-soft/60 px-4 py-3">
        <h3 className="font-semibold">{preview.title}</h3>
        {action.status !== "pending" && (
          <Badge tone={ACTION_STATUS[action.status][0]} dot>
            {ACTION_STATUS[action.status][1]}
          </Badge>
        )}
      </div>
      {preview.components.length > 0 && (
        <div className="overflow-x-auto px-4 py-2">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted [&>th]:py-1.5 [&>th]:pr-4">
                <th className="font-normal">Материал</th>
                <th className="font-normal">Нужно</th>
                <th className="font-normal">Зарезервируется</th>
                <th className="font-normal">Не хватит</th>
              </tr>
            </thead>
            <tbody>
              {preview.components.map((c) => (
                <tr key={c.material} className="border-t border-line/70 tabular-nums [&>td]:py-2 [&>td]:pr-4">
                  <td>{c.material}</td>
                  <td>{c.required}</td>
                  <td>{c.available}</td>
                  <td className={c.deficit ? "font-semibold text-bad" : "text-muted"}>{c.deficit ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {preview.warning && (
        <p className="mx-4 mb-3 flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {preview.warning}
        </p>
      )}
      {action.status === "pending" ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-3">
          <Button disabled={decide.isPending} onClick={() => decide.mutate("confirm")}>
            Подтвердить
          </Button>
          <Button variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
            Отклонить
          </Button>
          <span className="text-xs text-muted">До подтверждения ничего не изменится.</span>
        </div>
      ) : (
        <p className="border-t border-line px-4 py-3 text-sm">
          {action.result}{" "}
          {action.status === "confirmed" && (
            <Link className={linkClass} to="/jobs?period=all">
              К заданиям
            </Link>
          )}
        </p>
      )}
      {decide.error && (
        <div className="px-4">
          <ErrorBox error={decide.error} />
        </div>
      )}
    </section>
  );
}
