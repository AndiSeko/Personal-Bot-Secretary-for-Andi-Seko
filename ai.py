import logging
from groq import Groq

import config

logger = logging.getLogger(__name__)

client: Groq | None = None

SYSTEM_PROMPT = """Ты — личный AI-ассистент секретаря пользователя в Telegram. Ты помогаешь с задачами, отвечаешь на вопросы, планируешь день. Отвечай кратко и по делу на русском.

ФОРМАТИРОВАНИЕ TELEGRAM (HTML):
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Спойлер: <tg-spoiler>скрытый текст</tg-spoiler>  (в тексте без тегов это ||скрытый||)
- Код: <code>код</code>
- Ссылка: <a href="https://example.com">текст</a>
Используй эти теги активно, когда уместно. Спойлер делай именно через <tg-spoiler>, а не через markdown.

Если пользователь просит поставить напоминание, подскажи команду /remind или /recurring в боте."""

_conversation_history: list[dict] = []


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
    """Yield chunks для стриминга как в OpenClaw — постепенная печать."""
    if not client:
        yield "AI-ассистент не настроен. Добавьте GROQ_API_KEY в .env"
        return

    _conversation_history.append({"role": "user", "content": user_message})
    if len(_conversation_history) > 20:
        del _conversation_history[:len(_conversation_history) - 20]

    full = ""
    try:
        stream = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *_conversation_history],
            temperature=0.7,
            max_tokens=1024,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full += delta
                yield delta
        _conversation_history.append({"role": "assistant", "content": full})
    except Exception as e:
        err = str(e).lower()
        if any(x in err for x in ["model_not_found", "does not exist", "decommissioned", "unsupported", "not found"]):
            _conversation_history.pop()
            ans = await ask(user_message)
            yield ans
            return
        logger.error("Groq stream error: %s", e)
        _conversation_history.pop()
        yield f"Ошибка AI: {e}"


async def ask(user_message: str) -> str:
    if not client:
        return "AI-ассистент не настроен. Добавьте GROQ_API_KEY в .env"

    _conversation_history.append({"role": "user", "content": user_message})

    if len(_conversation_history) > 20:
        del _conversation_history[:len(_conversation_history) - 20]

    try:
        response = client.chat.completions.create(
            model=config.AI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                *_conversation_history,
            ],
            temperature=0.7,
            max_tokens=1024,
        )
        answer = response.choices[0].message.content
        _conversation_history.append({"role": "assistant", "content": answer})
        return answer
    except Exception as e:
        err = str(e).lower()
        # Groq часто deprecates модели — пробуем фолбэки + авто-список доступных
        if any(x in err for x in ["model_not_found", "does not exist", "decommissioned", "unsupported", "not found", "model_"]):
            # 1) сначала пробуем запросить список доступных моделей
            try:
                models = client.models.list()
                available = [m.id for m in models.data]
                logger.warning("Model %s not found, available: %s", config.AI_MODEL, available)
                for m in available:
                    if m == config.AI_MODEL:
                        continue
                    try:
                        logger.warning("Trying available model %s", m)
                        response = client.chat.completions.create(
                            model=m,
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                *_conversation_history,
                            ],
                            temperature=0.7,
                            max_tokens=1024,
                        )
                        answer = response.choices[0].message.content
                        _conversation_history.append({"role": "assistant", "content": answer})
                        return answer
                    except Exception:
                        continue
            except Exception as le:
                logger.warning("Failed to list Groq models: %s", le)
            # 2) хардкод фолбэки на случай если list не сработал (актуально на 29.08.2026: openai/gpt-oss-20b / 120b)
            for fallback in ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.1-8b-instant", "llama-3.3-70b-versatile"]:
                if fallback == config.AI_MODEL:
                    continue
                try:
                    logger.warning("Model %s not found, trying fallback %s", config.AI_MODEL, fallback)
                    response = client.chat.completions.create(
                        model=fallback,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            *_conversation_history,
                        ],
                        temperature=0.7,
                        max_tokens=1024,
                    )
                    answer = response.choices[0].message.content
                    _conversation_history.append({"role": "assistant", "content": answer})
                    return answer
                except Exception:
                    continue
        logger.error("Groq API error: %s", e)
        _conversation_history.pop()
        return f"Ошибка AI: {e}. Проверь https://console.groq.com/docs/models и задай рабочий AI_MODEL в Environment."


def clear_history():
    _conversation_history.clear()
