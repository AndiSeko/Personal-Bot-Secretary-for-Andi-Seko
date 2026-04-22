import logging
from groq import Groq

import config

logger = logging.getLogger(__name__)

client: Groq | None = None

SYSTEM_PROMPT = """Ты — личный AI-ассистент секретаря пользователя. Ты помогаешь с задачами, отвечаешь на вопросы, помогаешь планировать день, анализируешь информацию. Отвечай кратко и по делу на русском языке. Если пользователь просит поставить напоминание, подскажи ему использовать команду /remind или /recurring в боте."""

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
        logger.error("Groq API error: %s", e)
        _conversation_history.pop()
        return f"Ошибка AI: {e}"


def clear_history():
    _conversation_history.clear()
