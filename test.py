from llama_cpp import Llama

llm = Llama(
    model_path=r"models\ai\gemma-4-E4B-it-Q4_K_M.gguf",
    n_ctx=512,
    n_batch=128,
    n_gpu_layers=-1,   # test CPU trước
    verbose=True,
)

out = llm("Translate to Vietnamese: The weather is beautiful today. Don't thinking, just answer", max_tokens=64)
print(out)