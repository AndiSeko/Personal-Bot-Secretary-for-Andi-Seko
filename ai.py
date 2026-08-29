import logging
from groq import Groq

import config

logger = logging.getLogger(__name__)

client: Groq | None = None

SYSTEM_PROMPT = """Ты — личный AI-ассистент секретаря Andi Seko в Telegram. Отвечай кратко и по делу на русском.

ТВОИ РЕАЛЬНЫЕ ВОЗМОЖНОСТИ — не выдумывай других:
- Разовые напоминания: /remind 5m/2h/1d или 22.04.2026 15:30 текст
- Цикличные: /recurring 30m/1h/1d текст
- /list, /delete <id>, /deleteall
- /app — веб-кабинет (https://secretary-bot-9azn.onrender.com)
- Календарь и сообщения в кабинете, пересылка сообщений от других пользователей владельцу
Не упоминай Google Calendar, Evernote, шифрование, переводы если не просят.

ФОРМАТИРОВАНИЕ TELEGRAM (HTML) — используй ТОЛЬКО эти теги:
<b>жирный</b>, <i>курсив</i>, <u>подчеркнутый</u>, <s>зачеркнутый</s>, <tg-spoiler>скрытый</tg-spoiler>, <code>код</code>, <pre>блок кода</pre>, <a href="https://...">ссылка</a>, <blockquote>цитата</blockquote>
ЗАПРЕЩЕНО: <ol>, <ul>, <li>, <p>, <div>, <h1> и т.п. — вместо списков используй • или — и переносы строк \\n, вместо заголовков — <b>Заголовок</b>.
Спойлер делай только <tg-spoiler>, а не ||.
Пиши красиво, но без лишних тегов."""

# Приоритет мощности: от самой умной к самой экономной (актуально 29.08.2026 по Groq deprecations)
MODEL_PRIORITY = [
    "openai/gpt-oss-120b",      # замена llama-3.3-70b, самая мощная
    "qwen/qwen3-32b",            # альтернатива 120b
    "openai/gpt-oss-20b",       # замена llama-3.1-8b, быстрая/дешёвая
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "llama3-8b-8192",
]

_conversation_history: list[dict] = []
_cached_available: list[str] | None = None


def _ordered_available() -> list[str]:
    """Возвращает доступные модели отсортированные по MODEL_PRIORITY (мощность)."""
    global _cached_available
    try:
        models = client.models.list()  # type: ignore
        available = [m.id for m in models.data]
        _cached_available = available
        # сначала приоритетные в порядке мощности, потом остальные
        ordered = [m for m in MODEL_PRIORITY if m in available]
        ordered += [m for m in available if m not in ordered]
        logger.info("Groq available models ordered: %s", ordered)
        return ordered
    except Exception as e:
        logger.warning("Failed to list Groq models: %s", e)
        return MODEL_PRIORITY


def _should_fallback(err: str) -> bool:
    e = err.lower()
    return any(k in e for k in [
        "model_not_found", "does not exist", "decommissioned", "unsupported",
        "not found", "model_", "rate_limit", "quota", "429", "too many requests",
        "limit", "capacity", "overloaded"
    ])


def init():
    global client
    if config.GROQ_API_KEY:
        client = Groq(api_key=config.GROQ_API_KEY)
        logger.info("Groq AI initialized (model: %s)", config.AI_MODEL)
    else:
        client = None
        logger.warning("GROQ_API_KEY not set, AI features disabled")


def is_available() -> bool:
    return client is not None


async def ask_stream(user_message: str):
    """Yield chunks для стриминга как в OpenClaw — постепенная печать с авто-фолбэком по мощности."""
    if not client:
        yield "AI-ассистент не настроен. Добавьте GROQ_API_KEY в .env"
        return

    _conversation_history.append({"role": "user", "content": user_message})
    if len(_conversation_history) > 20:
        del _conversation_history[:len(_conversation_history) - 20]

    # пробуем модели по приоритету мощности
    candidates = [config.AI_MODEL] + [m for m in _ordered_available() if m != config.AI_MODEL]
    last_err = None
    for model in candidates:
        full = ""
        try:
            logger.info("AI stream trying %s", model)
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *_conversation_history],
                temperature=0.7,
                max_tokens=1024,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""  # type: ignore
                if delta:
                    full += delta
                    yield delta
            _conversation_history.append({"role": "assistant", "content": full})
            if model != config.AI_MODEL:
                logger.warning("Auto-switched AI model %s -> %s", config.AI_MODEL, model)
            return
        except Exception as e:
            last_err = e
            if not _should_fallback(str(e)):
                break
            logger.warning("Model %s failed (%s), trying next", model, e)
            continue

    logger.error("Groq stream error: %s", last_err)
    _conversation_history.pop()
    yield f"Ошибка AI: {last_err}. Проверь https://console.groq.com/docs/models"


async def ask(user_message: str) -> str:
    if not client:
        return "AI-ассистент не настроен. Добавьте GROQ_API_KEY в .env"

    _conversation_history.append({"role": "user", "content": user_message})

    if len(_conversation_history) > 20:
        del _conversation_history[:len(_conversation_history) - 20]

    candidates = [config.AI_MODEL] + [m for m in _ordered_available() if m != config.AI_MODEL]
    last_err = None
    for model in candidates:
        try:
            logger.info("AI trying %s", model)
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *_conversation_history],
                temperature=0.7,
                max_tokens=1024,
            )
            answer = response.choices[0].message.content
            _conversation_history.append({"role": "assistant", "content": answer})
            if model != config.AI_MODEL:
                logger.warning("Auto-switched AI model %s -> %s", config.AI_MODEL, model)
            return answer
        except Exception as e:
            last_err = e
            if not _should_fallback(str(e)):
                break
            logger.warning("Model %s failed (%s), trying next", model, e)
            continue

    logger.error("Groq API error: %s", last_err)
    _conversation_history.pop()
    return f"Ошибка AI: {last_err}. Проверь https://console.groq.com/docs/models"


def clear_history():
    _conversation_history.clear()
