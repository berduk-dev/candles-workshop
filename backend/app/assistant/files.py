"""Файлы чата: загрузки пользователя и файлы, созданные Code Interpreter."""

import csv
import io
import re
import uuid
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.masking import Masker
from app.config import get_settings
from app.errors import InvalidInputError, NotFoundError
from app.models import ChatFile, Conversation, User

MAX_UPLOAD = 5 * 1024 * 1024
MAX_CELLS = 300_000
MIME = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _dir() -> Path:
    path = Path(get_settings().storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_content(file: ChatFile) -> bytes:
    path = _dir() / file.storage_name
    if not path.exists():
        raise NotFoundError("Файл больше не доступен.")
    return path.read_bytes()


async def save(
    session: AsyncSession,
    *,
    user_id: int,
    conversation_id: int,
    message_id: int | None,
    source: str,
    filename: str,
    content: bytes,
    container_file_id: str | None = None,
) -> ChatFile:
    suffix = Path(filename).suffix.lower()
    # Имя на диске случайное: имя от пользователя или модели никогда не становится путём.
    storage_name = f"{uuid.uuid4().hex}{suffix}"
    (_dir() / storage_name).write_bytes(content)
    file = ChatFile(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        source=source,
        filename=filename[:200],
        mime=MIME.get(suffix, "application/octet-stream"),
        size=len(content),
        storage_name=storage_name,
        container_file_id=container_file_id,
    )
    session.add(file)
    await session.flush()
    return file


async def get_own(session: AsyncSession, user: User, file_id: int) -> ChatFile:
    file = await session.scalar(select(ChatFile).where(ChatFile.id == file_id, ChatFile.user_id == user.id))
    if file is None:
        raise NotFoundError("Файл не найден.")
    return file


async def save_upload(
    session: AsyncSession, user: User, conversation: Conversation, filename: str, content: bytes
) -> ChatFile:
    """Проверяет файл, заменяет имена кодами в каждой ячейке и сохраняет уже обезличенную копию."""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise InvalidInputError("Можно загрузить только файлы CSV и XLSX.")
    if len(content) > MAX_UPLOAD:
        raise InvalidInputError("Файл больше 5 МБ.")
    if not content:
        raise InvalidInputError("Файл пустой.")
    masker = await Masker.load(session)
    convert = _mask_csv if suffix == ".csv" else _mask_xlsx
    masked = await run_in_threadpool(convert, content, masker)
    safe_stem = re.sub(r"[^\w\- ]", "_", masker.mask_text(Path(filename).stem))[:80] or "file"
    await masker.save(session)
    return await save(
        session,
        user_id=user.id,
        conversation_id=conversation.id,
        message_id=None,
        source="upload",
        filename=f"{safe_stem}{suffix}",
        content=masked,
    )


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InvalidInputError("Не удалось прочитать файл: сохраните CSV в кодировке UTF-8.")


def _mask_csv(content: bytes, masker: Masker) -> bytes:
    text = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if sum(len(r) for r in rows) > MAX_CELLS:
        raise InvalidInputError("В файле слишком много ячеек.")
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter).writerows([masker.mask_text(c) for c in row] for row in rows)
    return out.getvalue().encode("utf-8")


def _mask_xlsx(content: bytes, masker: Masker) -> bytes:
    """Переносит в новую книгу только значения: формулы, примечания, макросы и свойства
    документа (там бывает автор) отбрасываются."""
    try:
        source = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise InvalidInputError("Не удалось прочитать файл Excel: он повреждён или защищён.") from exc
    target = Workbook(write_only=True)
    cells = 0
    for sheet in source.worksheets:
        copy = target.create_sheet(re.sub(r"[\\/*?:\[\]]", "_", masker.mask_text(sheet.title))[:31])
        for row in sheet.iter_rows(values_only=True):
            cells += len(row)
            if cells > MAX_CELLS:
                raise InvalidInputError("В файле слишком много ячеек.")
            copy.append([masker.mask_text(v) if isinstance(v, str) else v for v in row])
    out = io.BytesIO()
    target.save(out)
    return out.getvalue()


def unmask_generated(filename: str, content: bytes, masker: Masker) -> bytes:
    """В таблицах, созданных моделью, коды заменяются обратно на имена. Картинку изменить нельзя —
    для неё под ответом показывается расшифровка кодов."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".csv":
            return masker.unmask_text(content.decode("utf-8-sig")).encode("utf-8-sig")
        if suffix == ".xlsx":
            book = load_workbook(io.BytesIO(content))
            for sheet in book.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str):
                            cell.value = masker.unmask_text(cell.value)
            out = io.BytesIO()
            book.save(out)
            return out.getvalue()
    except Exception:  # непонятный файл отдаём как есть — чат из-за этого падать не должен
        return content
    return content
