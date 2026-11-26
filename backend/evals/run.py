"""Прогон эталонных вопросов на демо-данных. Запускается вручную перед релизом, не в CI.

    docker compose exec backend python -m evals.run                         # модели из настроек
    docker compose exec backend python -m evals.run --only plan_job no_data  # часть кейсов
    docker compose exec backend python -m evals.run --compare gpt-5.4-mini gpt-6-luna
    docker compose exec backend python -m evals.run --repeat 3 --no-judge --label speed   # замер скорости

Отчёт: evals/reports/<label>.md (и .json), по умолчанию latest. Машинные проверки отделены от оценки
LLM-судьи, скорость и цена — от тех и других.
"""

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, func, select

from app.assistant import chat, files
from app.assistant.agent import Agent, AssistantDeps, Turn
from app.assistant.llm.base import LLMError, LLMRequest, TextDelta, UserText
from app.assistant.llm.openai_provider import OpenAIContainers, OpenAIProvider, make_client
from app.assistant.llm.router import ModelRouter
from app.assistant.masking import Masker
from app.assistant.prompts import load_text
from app.clock import now
from app.config import get_settings
from app.db import SessionFactory, engine
from app.models import Batch, Conversation, Job, MessageStatus, PendingAction, User, UserMemory
from app.services.fx import FxClient
from evals.checks import Result, run_checks
from evals.expected import compute

HERE = Path(__file__).parent
REPORTS = HERE / "reports"
# Доллары за 1 млн токенов (вход, выход): developers.openai.com/api/docs/pricing, 24.11.2026.
PRICES = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-5.6-luna": (0.20, 1.20),
}
CACHE_READ, CACHE_WRITE = 0.1, 1.25  # доля цены входа: чтение из кэша и запись в него (gpt-5.6 и новее)
TIER_FACTOR = {"fast": 2, "priority": 2}
# Верхняя граница: тариф за 20-минутную сессию. При поминутном счёте (минимум 5 минут) — вчетверо меньше.
CONTAINER_PRICE = 0.03


async def db_state() -> tuple:
    """Отпечаток учётных данных: эвалы не должны менять в них ничего."""
    async with SessionFactory() as session:
        statuses = await session.execute(select(Job.id, Job.status).order_by(Job.id))
        totals = await session.execute(
            select(func.count(Batch.id), func.sum(Batch.qty_on_hand), func.sum(Batch.qty_reserved))
        )
        return (tuple(statuses.tuples().all()), tuple(totals.one()))


async def run_case(case: dict[str, Any], deps: AssistantDeps) -> Result:
    result = Result()
    before = await db_state()
    async with SessionFactory() as session:
        user = await session.scalar(select(User).where(User.login == case["user"]))
        # Факты пользователя на время кейса убираем: ответ не должен зависеть от прошлых разговоров.
        saved = [
            m.fact for m in await session.scalars(select(UserMemory).where(UserMemory.user_id == user.id))
        ]
        await session.execute(delete(UserMemory).where(UserMemory.user_id == user.id))
        await session.commit()
    chats: list[int] = []
    try:
        for index, question in enumerate(case["ask"]):
            if not chats or case.get("new_chat_each"):
                async with SessionFactory() as session:
                    conversation = await chat.create(session, user)
                    if index == 0 and (upload := case.get("upload")):
                        await files.save_upload(
                            session, user, conversation, upload["name"], upload["text"].encode()
                        )
                    await session.commit()
                    chats.append(conversation.id)
            await ask(result, deps, user, chats[-1], question)
    finally:
        async with SessionFactory() as session:
            proposals = await session.scalars(
                select(PendingAction.tool).where(PendingAction.conversation_id.in_(chats))
            )
            result.proposals = list(proposals)
            await session.execute(delete(Conversation).where(Conversation.id.in_(chats)))
            await session.execute(delete(UserMemory).where(UserMemory.user_id == user.id))
            session.add_all(UserMemory(user_id=user.id, fact=f) for f in saved)
            await session.commit()
    result.db_changed = await db_state() != before
    return result


async def ask(result: Result, deps: AssistantDeps, user: User, chat_id: int, question: str) -> None:
    async with SessionFactory() as session:
        _, answer = await chat.start_answer(session, user, chat_id, question, [])
        await session.commit()
    turn = Turn()
    started, first_word = time.monotonic(), None
    try:
        async for event in Agent(deps, user, chat_id, answer.id, turn).run():
            if event["type"] == "text" and first_word is None:
                first_word = time.monotonic() - started
            if event["type"] == "file":
                result.files.append(event["file"]["filename"])
        turn.status = MessageStatus.COMPLETE
    except LLMError as exc:
        turn.status, turn.error = MessageStatus.ERROR, exc.reason
        result.error = f"{exc.reason} {exc.status or ''} {exc.detail}"
    result.times.append(time.monotonic() - started)
    result.first_words.append(first_word or result.times[-1])
    await chat._save(deps, answer.id, turn)
    result.answer = turn.text  # проверяется ответ на последний вопрос
    result.statuses.append(turn.status.value)
    result.tools += [t["name"] for t in turn.tools]
    result.sources += [t["result"] for t in turn.tools] + [t["args"] for t in turn.tools]
    result.sources += [c["logs"] for c in turn.code]
    result.code_runs += len(turn.code)
    result.models.add(turn.model or "—")
    result.usage.update(turn.meta()["usage"])


async def judge(router: ModelRouter, case: dict[str, Any], result: Result) -> dict[str, Any]:
    async with SessionFactory() as session:
        masker = await Masker.load(session)
    payload = json.dumps(
        {
            "question": case["ask"],
            "tool_results": [s[:6000] for s in result.sources][:12],
            "files": result.files,
            "proposals": result.proposals,
            "answer": result.answer,
            "criterion": case["judge"],
        },
        ensure_ascii=False,
    )
    request = LLMRequest(load_text("judge.md"), [UserText(masker.mask_text(payload))])
    try:
        text = "".join([e.text async for e in router.stream(request) if isinstance(e, TextDelta)])
        return json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (LLMError, ValueError) as exc:
        return {"score": None, "reason": f"судья не ответил: {exc}"}


def make_deps(models: list[str], client, fx, effort: str) -> AssistantDeps:
    settings = get_settings()
    router = ModelRouter(
        [OpenAIProvider(client, m, settings.llm_service_tier, effort) for m in models],
        call_timeout=settings.llm_call_timeout_seconds,
        first_event_timeout=settings.llm_first_event_timeout_seconds,
    )
    return AssistantDeps(router, OpenAIContainers(client), SessionFactory, fx)


async def expected_numbers(fx) -> dict[str, Any]:
    async with SessionFactory() as session:
        manager = await session.scalar(select(User).where(User.login == "manager"))
        expected = await compute(session, manager, fx)
        await session.commit()
    return expected


async def evaluate(case, deps, expected, judge_router, tag: str = "") -> dict[str, Any]:
    result = await run_case(case, deps)
    checks = run_checks(case, result, expected)
    verdict = {"score": None, "reason": "без судьи"}
    if judge_router:
        verdict = await judge(judge_router, case, result)
    passed = all(ok for _, ok in checks)
    mark = "OK  " if passed else "FAIL"
    print(f"{mark} {tag}{case['id']:<24} {sum(result.times):5.1f} с  судья {verdict['score']}", flush=True)
    for name, ok in checks:
        if not ok:
            print(f"       ✗ {name}")
    return {"case": case, "result": result, "checks": checks, "passed": passed, "judge": verdict}


async def run_all(
    cases: list[dict], models: list[str], judge_model: str | None, client, fx, repeat: int = 1
) -> list[dict]:
    deps = make_deps(models, client, fx, get_settings().llm_reasoning_effort)
    judge_router = (
        ModelRouter([OpenAIProvider(client, judge_model)], call_timeout=60) if judge_model else None
    )
    expected = await expected_numbers(fx)
    return [await evaluate(case, deps, expected, judge_router) for case in cases * repeat]


async def run_ab(cases: list[dict], models: list[str], client, fx, efforts: list[str], repeat: int):
    """Сравнение вариантов «сколько модели думать». Каждый вопрос задаётся всем вариантам подряд,
    порядок чередуется: сеть и нагрузка на OpenAI влияют на все варианты одинаково."""
    deps = {effort: make_deps(models, client, fx, effort) for effort in efforts}
    expected = await expected_numbers(fx)
    rows: dict[str, list[dict]] = {effort: [] for effort in efforts}
    for index, case in enumerate(cases * repeat):
        order = efforts if index % 2 == 0 else efforts[::-1]
        for effort in order:
            row = await evaluate(case, deps[effort], expected, None, tag=f"[{effort or 'default'}] ")
            rows[effort].append(row)
    return rows


def percentile(values: list[float], share: float) -> float:
    """Ближайший ранг: при малом числе ответов p95 — это почти максимум."""
    return sorted(values)[math.ceil(share * len(values)) - 1]


def summary(rows: list[dict]) -> dict[str, Any]:
    scores = [r["judge"]["score"] for r in rows if r["judge"]["score"]]
    checks = [ok for r in rows for _, ok in r["checks"]]
    times = [t for r in rows for t in r["result"].times]
    first_words = [t for r in rows for t in r["result"].first_words]
    usage = sum((r["result"].usage for r in rows), Counter())
    return {
        "passed": sum(r["passed"] for r in rows),
        "total": len(rows),
        "checks": f"{sum(checks)}/{len(checks)}",
        "judge": sum(scores) / len(scores) if scores else 0,
        "answers": len(times),
        "seconds": statistics.mean(times),
        "seconds_p50": statistics.median(times),
        "seconds_p95": percentile(times, 0.95),
        "first_word_p50": statistics.median(first_words),
        "first_word_p95": percentile(first_words, 0.95),
        **{
            k: usage[k]
            for k in (
                "steps",
                "input",
                "cached",
                "written",
                "output",
                "reasoning",
                "containers",
                "llm_ms",
                "tool_ms",
                "files_ms",
            )
        },
        "total_ms": sum(times) * 1000,
    }


def token_cost(model: str, s: dict[str, Any]) -> float | None:
    """Токены с учётом кэша и тарифа. Считается по основной модели: запасная отвечает редко."""
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    plain = s["input"] - s["cached"] - s["written"]
    tokens = (plain + s["cached"] * CACHE_READ + s["written"] * CACHE_WRITE) * price_in
    tokens += s["output"] * price_out
    return tokens / 1_000_000 * TIER_FACTOR.get(get_settings().llm_service_tier, 1)


def cost(model: str, s: dict[str, Any]) -> str:
    tokens = token_cost(model, s)
    containers = f"${s['containers'] * CONTAINER_PRICE:.2f}"
    return f"{'—' if tokens is None else f'${tokens:.4f}'} + {containers}"


def facts(model: str, s: dict[str, Any]) -> list[str]:
    n = s["answers"]
    return [
        f"- Время ответа: p50 {s['seconds_p50']:.1f} с, p95 {s['seconds_p95']:.1f} с, "
        f"среднее {s['seconds']:.1f} с",
        f"- До первого слова: p50 {s['first_word_p50']:.1f} с, p95 {s['first_word_p95']:.1f} с",
        f"- На ответ в среднем: обращений к модели {s['steps'] / n:.1f}, "
        f"токенов на входе {s['input'] / n:.0f} (из кэша {100 * s['cached'] / max(s['input'], 1):.0f}%), "
        f"на выходе {s['output'] / n:.0f} (рассуждения {s['reasoning'] / n:.0f})",
        f"- Куда ушло время: ожидание модели {100 * s['llm_ms'] / s['total_ms']:.0f}% "
        f"(в среднем {s['llm_ms'] / max(s['steps'], 1) / 1000:.1f} с на обращение), "
        f"инструменты и загрузка файлов {100 * s['tool_ms'] / s['total_ms']:.0f}%, "
        f"скачивание созданных файлов {100 * s['files_ms'] / s['total_ms']:.0f}%, "
        f"остальное (база, маскирование) "
        f"{100 * (1 - (s['llm_ms'] + s['tool_ms'] + s['files_ms']) / s['total_ms']):.0f}%",
        f"- Контейнеров Code Interpreter: {s['containers']} на {n} ответов",
        f"- Стоимость прогона: {cost(model, s)} (токены + контейнеры по верхней границе, без судьи)",
    ]


def report(rows: list[dict], models: list[str], judge_model: str | None) -> str:
    s = summary(rows)
    out = [
        "# Отчёт прогона эталонных вопросов",
        "",
        f"- Дата: {now():%d.%m.%Y %H:%M}",
        f"- Модели: {' → '.join(models)} (основная → запасная); судья: {judge_model or 'не запускался'}",
        f"- **Машинные проверки: {s['passed']} из {s['total']} вопросов** "
        f"(отдельных проверок пройдено {s['checks']})",
        f"- **Оценка LLM-судьи: {s['judge']:.2f} из 5** (среднее, считается отдельно от машинных проверок)",
        *facts(models[0], s),
        "",
        "## Машинные проверки",
        "",
        "| Вопрос | Роль | Итог | Инструменты | Код | Ответила | Время, с | До слова, с "
        "| Вход (кэш) / выход | Контейнеры |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        res: Result = r["result"]
        out.append(
            f"| {r['case']['id']} | {r['case']['user']} | {'✅' if r['passed'] else '❌'} | "
            f"{', '.join(res.tools) or '—'} | {res.code_runs or '—'} | "
            f"{', '.join(sorted(res.models))} | {' + '.join(f'{t:.1f}' for t in res.times)} | "
            f"{' + '.join(f'{t:.1f}' for t in res.first_words)} | "
            f"{res.usage['input']} ({res.usage['cached']}) / {res.usage['output']} | "
            f"{res.usage['containers'] or '—'} |"
        )
    out += ["", "## Оценка LLM-судьи", "", "| Вопрос | Балл | Почему |", "|---|---|---|"]
    out += [f"| {r['case']['id']} | {r['judge']['score']} | {r['judge']['reason']} |" for r in rows]
    out += ["", "## Подробно"]
    for r in rows:
        res = r["result"]
        out += ["", f"### {r['case']['id']}", ""]
        out += [f"**Вопрос:** {q}  " for q in r["case"]["ask"]]
        out += ["", *(f"- {'✅' if ok else '❌'} {name}" for name, ok in r["checks"])]
        if res.error:
            out.append(f"- ошибка: {res.error}")
        out += ["", "**Ответ:**", "", *("> " + line for line in res.answer.splitlines())]
    return "\n".join(out) + "\n"


def compare_report(results: dict[str, list[dict]], judge_model: str | None) -> str:
    out = [
        "# Сравнение моделей на эталонных вопросах",
        "",
        f"Дата: {now():%d.%m.%Y %H:%M}. Судья: {judge_model or 'не запускался'}. "
        "Запасная модель отключена. Стоимость — токены с учётом кэша + контейнеры Code Interpreter "
        "по верхней границе.",
        "",
        "| Модель | Вопросы | Проверки | Судья | Время на ответ | Токены вход/выход | Стоимость прогона |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, rows in results.items():
        s = summary(rows)
        out.append(
            f"| {model} | {s['passed']}/{s['total']} | {s['checks']} | {s['judge']:.2f} | "
            f"{s['seconds']:.1f} с | {s['input']}/{s['output']} | {cost(model, s)} |"
        )
    out += ["", "## Что не прошло", ""]
    for model, rows in results.items():
        for r in rows:
            failed = [name for name, ok in r["checks"] if not ok]
            if failed:
                out.append(f"- **{model}** · {r['case']['id']}: {'; '.join(failed)}")
    return "\n".join(out) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="id кейсов")
    parser.add_argument("--compare", nargs="*", help="сравнить эти модели между собой")
    parser.add_argument("--judge", default="gpt-5.4")
    parser.add_argument("--no-judge", action="store_true", help="без оценки LLM: дешевле и быстрее")
    parser.add_argument("--repeat", type=int, default=1, help="сколько раз задать каждый вопрос")
    parser.add_argument("--label", default="latest", help="имя отчёта")
    parser.add_argument(
        "--effort-ab",
        nargs="+",
        help="сравнить усилие рассуждений: default low none… (каждый вопрос — всем вариантам по очереди)",
    )
    args = parser.parse_args()
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("Нужен OPENAI_API_KEY.")
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    judge_model = None if args.no_judge else args.judge
    client = make_client(settings.openai_api_key)
    fx = FxClient(settings.fx_base_url, timeout=settings.fx_timeout_seconds)
    REPORTS.mkdir(exist_ok=True)
    try:
        if args.compare:
            results = {}
            for model in args.compare:
                print(f"\n=== {model}")
                results[model] = await run_all(cases, [model], judge_model, client, fx, args.repeat)
            (REPORTS / "compare.md").write_text(compare_report(results, judge_model), encoding="utf-8")
            print((REPORTS / "compare.md").read_text(encoding="utf-8"))
            return
        models = [settings.llm_primary_model, settings.llm_fallback_model]
        if args.effort_ab:
            efforts = ["" if e == "default" else e for e in args.effort_ab]
            results = await run_ab(cases, models, client, fx, efforts, args.repeat)
            for effort, rows in results.items():
                name = f"{args.label}_{effort or 'default'}"
                (REPORTS / f"{name}.md").write_text(report(rows, models, None), encoding="utf-8")
                total = summary(rows)
                print(f"\n=== {effort or 'default'}: {total['passed']}/{total['total']}")
                print("\n".join(facts(models[0], total)))
            return
        rows = await run_all(cases, models, judge_model, client, fx, args.repeat)
        (REPORTS / f"{args.label}.md").write_text(report(rows, models, judge_model), encoding="utf-8")
        total = summary(rows)
        dump = {
            "summary": {**total, "token_cost": token_cost(models[0], total)},
            "cases": [
                {
                    "id": r["case"]["id"],
                    "passed": r["passed"],
                    "checks": r["checks"],
                    "judge": r["judge"],
                    "times": r["result"].times,
                    "first_words": r["result"].first_words,
                    "tools": r["result"].tools,
                    "code_runs": r["result"].code_runs,
                    "usage": r["result"].usage,
                    "answer": r["result"].answer,
                }
                for r in rows
            ],
        }
        text = json.dumps(dump, ensure_ascii=False, indent=2)
        (REPORTS / f"{args.label}.json").write_text(text, encoding="utf-8")
        print(
            "\n".join(
                ["", f"Машинные проверки: {total['passed']}/{total['total']}", *facts(models[0], total)]
            )
        )
        print(f"Отчёт: evals/reports/{args.label}.md")
    finally:
        await fx.aclose()
        await client.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
