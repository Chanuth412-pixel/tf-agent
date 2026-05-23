from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate


def call_cloud_llm(prompt_template: str, input_variables: dict) -> str:
    llm = ChatOllama(
        model="qwen2.5-coder:1.5b",
        temperature=0.0,
        base_url="http://localhost:11434",
        num_ctx=2048,
    )
    prompt = ChatPromptTemplate.from_template(prompt_template)
    chain = prompt | llm
    print("    [LLM] Sending request to local Ollama (qwen2.5-coder:1.5b) with num_ctx=2048...")
    response = chain.invoke(input_variables)
    return response.content
