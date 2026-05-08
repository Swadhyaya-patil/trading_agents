# # from langchain_ollama import ChatOllama
# # from langchain_core.output_parsers import JsonOutputParser
# # from langchain_core.prompts import ChatPromptTemplate

# # def get_llm(temperature: float = 0.0) -> ChatOllama:
# #     return ChatOllama(
# #         model="qwen2.5:14b",      # swap to llama3.1:8b if VRAM is tight
# #         temperature=temperature,
# #         format="json",            # forces JSON output mode in Ollama
# #     )

# # def get_chain(system_prompt: str, llm=None):
# #     if llm is None:
# #         llm = get_llm()
# #     prompt = ChatPromptTemplate.from_messages([
# #         ("system", system_prompt),
# #         ("human", "{input}"),
# #     ])
# #     return prompt | llm | JsonOutputParser()


# from langchain_ollama import ChatOllama
# from langchain_core.output_parsers import JsonOutputParser
# from langchain_core.prompts import ChatPromptTemplate
# import os

# # Set this in your environment or .env file:
# # OLLAMA_HOST=http://192.168.1.50:11434   ← your LLM machine's IP
# OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# def get_llm(temperature: float = 0.0) -> ChatOllama:
#     return ChatOllama(
#         model=os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
#         base_url=OLLAMA_HOST,          # ← this is what was missing
#         temperature=temperature,
#         format="json",
#     )

# def get_chain(system_prompt: str, llm=None):
#     if llm is None:
#         llm = get_llm()
#     prompt = ChatPromptTemplate.from_messages([
#         ("system", system_prompt),
#         ("human", "{input}"),
#     ])
#     return prompt | llm | JsonOutputParser()



import os
import json
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

load_dotenv()

OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")


def get_llm(temperature: float = 0.0) -> ChatOllama:
    return ChatOllama(
        model=OLLAMA_MODEL,       # ← was incorrectly OLLAMA_HOST
        base_url=OLLAMA_HOST,
        temperature=temperature,
        format="json",
    )


def get_chain(system_prompt: str, llm=None):
    if llm is None:
        llm = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}"),
    ])
    return prompt | llm | JsonOutputParser()


def safe_invoke(chain, input_text: str, retries: int = 2) -> dict:
    """Invoke chain with retry. Returns empty dict on total failure."""
    last_error = None
    for attempt in range(retries):
        try:
            result = chain.invoke({"input": input_text})
            if isinstance(result, dict):
                return result
            # parser returned a string — try parsing manually
            clean = str(result).strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(clean)
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                print(f"  [llm] Retry {attempt + 1} after error: {e}")
    raise last_error