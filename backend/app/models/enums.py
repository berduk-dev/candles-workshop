from enum import StrEnum


class Role(StrEnum):
    STOREKEEPER = "storekeeper"
    TECHNOLOGIST = "technologist"
    MANAGER = "manager"

    @property
    def label(self) -> str:
        return {"storekeeper": "Кладовщик", "technologist": "Технолог", "manager": "Руководитель"}[self.value]


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class BatchStatus(StrEnum):
    ON_CHECK = "on_check"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

    @property
    def label(self) -> str:
        return {"on_check": "На проверке", "accepted": "Принята", "rejected": "Брак"}[self.value]


class MovementKind(StrEnum):
    """Движения по партии. Знак количества: приход и возврат «+», выдача и досписание «−»."""

    RECEIPT = "receipt"
    ISSUE = "issue"
    OVERUSE = "overuse"
    RETURN = "return"

    @property
    def label(self) -> str:
        return {
            "receipt": "Поступление",
            "issue": "Выдача в цех",
            "overuse": "Досписание перерасхода",
            "return": "Возврат экономии",
        }[self.value]


class JobStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "draft": "Черновик",
            "planned": "Запланировано",
            "in_progress": "В работе",
            "completed": "Завершено",
            "cancelled": "Отменено",
        }[self.value]


class MarkingStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {"pending": "Отправляется", "sent": "Отправлен", "failed": "Не удалось отправить"}[self.value]


class MessageStatus(StrEnum):
    STREAMING = "streaming"
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"  # пользователь остановил или оборвалась связь
    ERROR = "error"


class ActionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"
