# from langchain_ollama import ChatOllama
# from langchain_core.output_parsers import JsonOutputParser
# from langchain_core.prompts import ChatPromptTemplate

# def get_llm(temperature: float = 0.0) -> ChatOllama:
#     return ChatOllama(
#         model="qwen2.5:14b",      # swap to llama3.1:8b if VRAM is tight
#         temperature=temperature,
#         format="json",            # forces JSON output mode in Ollama
#     )

# def get_chain(system_prompt: str, llm=None):
#     if llm is None:
#         llm = get_llm()
#     prompt = ChatPromptTemplate.from_messages([
#         ("system", system_prompt),
#         ("human", "{input}"),
#     ])
#     return prompt | llm | JsonOutputParser()


from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
import os

# Set this in your environment or .env file:
# OLLAMA_HOST=http://192.168.1.50:11434   ← your LLM machine's IP
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

def get_llm(temperature: float = 0.0) -> ChatOllama:
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
        base_url=OLLAMA_HOST,          # ← this is what was missing
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