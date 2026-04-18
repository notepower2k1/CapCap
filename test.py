from llama_cpp import Llama

llm = Llama(
    model_path=r"models\ai\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
    n_ctx=512,
    n_batch=128,
    n_gpu_layers=-1,   # test CPU trước
    verbose=True,
)
print("loaded")