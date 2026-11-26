import { Link } from "react-router";

import { linkClass } from "./ui";

/** Номера документов в тексте: З-000017 — задание, П-000019 — партия. Буквы кириллические;
 *  ПТ-… (поступления) не совпадает: после «П» сразу идёт дефис. Код вроде SUP-001 тоже не совпадает. */
const DOC_RE = /(?<![\p{L}\p{N}-])([ЗП])-(\d{1,6})(?!\p{N})/gu;

const PAGES: Record<string, string> = { З: "jobs", П: "batches" };

/** Ссылка ведёт на страницу-переход: ассистент знает номер, а адрес карточки строится по id. */
export const docHref = (prefix: string, digits: string) => `/${PAGES[prefix]}/n/${prefix}-${digits}`;

type Piece = string | { text: string; href: string };

export function splitDocs(text: string): Piece[] {
  const pieces: Piece[] = [];
  let last = 0;
  for (const match of text.matchAll(DOC_RE)) {
    const [whole, prefix = "", digits = ""] = match;
    if (match.index > last) pieces.push(text.slice(last, match.index));
    pieces.push({ text: whole, href: docHref(prefix, digits) });
    last = match.index + whole.length;
  }
  if (last < text.length) pieces.push(text.slice(last));
  return pieces;
}

/** Обычный текст (не Markdown) с кликабельными номерами. */
export function LinkedText({ text }: { text: string }) {
  return (
    <>
      {splitDocs(text).map((piece, i) =>
        typeof piece === "string" ? (
          piece
        ) : (
          <Link key={i} className={linkClass} to={piece.href}>
            {piece.text}
          </Link>
        ),
      )}
    </>
  );
}

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

/** Плагин для react-markdown: номера в тексте ответа становятся ссылками.
 *  Внутри готовых ссылок и кода текст не трогаем. */
export function remarkDocLinks() {
  const walk = (node: MdNode) => {
    if (!node.children || node.type === "link" || node.type === "inlineCode" || node.type === "code") return;
    node.children = node.children.flatMap((child): MdNode[] => {
      if (child.type !== "text" || !child.value) {
        walk(child);
        return [child];
      }
      return splitDocs(child.value).map((piece) =>
        typeof piece === "string"
          ? { type: "text", value: piece }
          : { type: "link", url: piece.href, children: [{ type: "text", value: piece.text }] },
      );
    });
  };
  return (tree: unknown) => walk(tree as MdNode);
}
