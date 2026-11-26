from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from app.assistant import actions, chat, files, memory
from app.assistant.agent import AssistantDeps, action_view, file_view
from app.deps import CurrentUser, SessionDep
from app.schemas.common import StrictModel

router = APIRouter(prefix="/chat", tags=["Ассистент"])


def get_assistant(request: Request) -> AssistantDeps:
    return request.app.state.assistant


AssistantDep = Annotated[AssistantDeps, Depends(get_assistant)]


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime


class QuestionIn(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    file_ids: list[int] = Field(default_factory=list, max_length=5)


class FactOut(BaseModel):
    id: int
    fact: str
    created_at: datetime


@router.get("/conversations", response_model=list[ConversationOut], summary="Мои диалоги")
async def list_conversations(session: SessionDep, user: CurrentUser):
    return await chat.list_conversations(session, user)


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=201,
    summary="Новый диалог (или уже имеющийся пустой)",
)
async def create_conversation(session: SessionDep, user: CurrentUser):
    conversation = await chat.create(session, user)
    await session.commit()
    await session.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204, summary="Удалить диалог")
async def delete_conversation(conversation_id: int, session: SessionDep, user: CurrentUser) -> None:
    await chat.remove(session, user, conversation_id)
    await session.commit()


@router.get("/conversations/{conversation_id}/messages", summary="Сообщения диалога")
async def list_messages(conversation_id: int, session: SessionDep, user: CurrentUser) -> list[dict[str, Any]]:
    return await chat.messages_view(session, user, conversation_id)


@router.post("/conversations/{conversation_id}/messages", summary="Задать вопрос (ответ — поток SSE)")
async def ask(
    conversation_id: int,
    data: QuestionIn,
    session: SessionDep,
    user: CurrentUser,
    assistant: AssistantDep,
) -> StreamingResponse:
    _, answer = await chat.start_answer(session, user, conversation_id, data.text, data.file_ids)
    await session.commit()
    return StreamingResponse(
        chat.launch(assistant, user, conversation_id, answer.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversation_id}/stop", summary="Остановить ответ")
async def stop(conversation_id: int, session: SessionDep, user: CurrentUser) -> dict[str, bool]:
    await chat.get_own(session, user, conversation_id)
    return {"stopped": chat.stop(conversation_id)}


@router.post("/conversations/{conversation_id}/files", status_code=201, summary="Загрузить CSV или XLSX")
async def upload_file(
    conversation_id: int, file: UploadFile, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    conversation = await chat.get_own(session, user, conversation_id)
    content = await file.read(files.MAX_UPLOAD + 1)
    saved = await files.save_upload(session, user, conversation, file.filename or "file", content)
    await session.commit()
    return file_view(saved)


@router.get("/files/{file_id}", summary="Скачать файл из чата")
async def download_file(file_id: int, session: SessionDep, user: CurrentUser) -> Response:
    file = await files.get_own(session, user, file_id)
    disposition = "inline" if file.mime.startswith("image/") else "attachment"
    return Response(
        files.read_content(file),
        media_type=file.mime,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file.filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/actions/{action_id}/confirm", summary="Подтвердить действие ассистента")
async def confirm_action(action_id: int, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    action = await actions.confirm(session, user, action_id)
    await chat.add_note(session, action.conversation_id, action.result)
    await session.commit()
    return action_view(action)


@router.post("/actions/{action_id}/reject", summary="Отклонить действие ассистента")
async def reject_action(action_id: int, session: SessionDep, user: CurrentUser) -> dict[str, Any]:
    action = await actions.reject(session, user, action_id)
    await chat.add_note(session, action.conversation_id, action.result)
    await session.commit()
    return action_view(action)


@router.get("/memory", response_model=list[FactOut], summary="Что ассистент помнит обо мне")
async def list_memory(session: SessionDep, user: CurrentUser):
    return await memory.list_facts(session, user)


@router.delete("/memory/{fact_id}", status_code=204, summary="Удалить факт")
async def delete_memory(fact_id: int, session: SessionDep, user: CurrentUser) -> None:
    await memory.delete_fact(session, user, fact_id)
    await session.commit()
