import os
import re
import json
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")


def get_llm(temperature: float = 0.0) -> ChatOllama:
    return ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_HOST,
        temperature=temperature,
        format="json",
    )


def get_chain(system_prompt: str, llm=None):
    """
    Build a chain that passes system_prompt as a literal string.

    FIX: ChatPromptTemplate.from_messages() parses ALL {curly brace}
    content in the system prompt as template variables — including the
    JSON example in the OUTPUT FORMAT section (e.g. {"decision": "BUY"}).
    This caused:
        'Input to ChatPromptTemplate is missing variables {"decision"}'

    Solution: bypass ChatPromptTemplate entirely for the system message.
    We use a RunnableLambda to inject the fixed system prompt and the
    dynamic {input} text as plain LangChain message objects, so no
    template variable parsing ever touches the system prompt string.
    """
    if llm is None:
        llm = get_llm()

    # Capture system_prompt in closure — it's a fixed string per chain
    _system_msg = SystemMessage(content=system_prompt)

    def build_messages(inputs: dict) -> list:
        return [_system_msg, HumanMessage(content=inputs["input"])]

    return RunnableLambda(build_messages) | llm | JsonOutputParser()


def safe_invoke(chain, input_text: str, retries: int = 2) -> dict:
    """Invoke chain with retry. Returns dict or raises on total failure."""
    last_error = None
    for attempt in range(retries):
        try:
            result = chain.invoke({"input": input_text})

            if isinstance(result, dict):
                return result

            content = str(result).strip()

            # Strip <think>...</think> blocks (DeepSeek / QwQ reasoning models)
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

            # Strip markdown fences
            content = re.sub(r"```json|```", "", content).strip()

            if not content:
                raise ValueError("Empty response after stripping think blocks")

            # Extract first JSON object if surrounded by prose
            start = content.find("{")
            end   = content.rfind("}") + 1
            if start != -1 and end > start:
                content = content[start:end]

            return json.loads(content)

        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                print(f"  [llm] Retry {attempt + 1} after error: {str(e)[:120]}")

    raise last_error
