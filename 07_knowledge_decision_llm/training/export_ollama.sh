#!/usr/bin/env bash
set -e

# Export LoRA adapter, convert to GGUF Q4_K_M, and register with Ollama.
# Designed for the team machine with RTX 3060 (12 GB VRAM).

ADAPTER_DIR="${1:-07_knowledge_decision_llm/training/models/qwen_explainer_lora}"
BASE_MODEL="${2:-Qwen/Qwen2.5-7B-Instruct}"
OUTPUT_DIR="${3:-07_knowledge_decision_llm/training/models/gguf}"

mkdir -p "$OUTPUT_DIR"

echo "=== 1. Merging LoRA weights with Base Model ==="
python3 - <<EOF
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

base_model_name = "$BASE_MODEL"
adapter_path = "$ADAPTER_DIR"
output_path = "$OUTPUT_DIR/merged_fp16"

print(f"Loading base model {base_model_name}...")
tokenizer = AutoTokenizer.from_pretrained(base_model_name)
base = AutoModelForCausalLM.from_pretrained(base_model_name, torch_dtype=torch.float16, device_map="cpu")

print(f"Merging adapter from {adapter_path}...")
model = PeftModel.from_pretrained(base, adapter_path)
merged = model.merge_and_unload()

print(f"Saving merged model to {output_path}...")
merged.save_pretrained(output_path)
tokenizer.save_pretrained(output_path)
print("Merge complete!")
EOF

echo "=== 2. Quantizing to GGUF Q4_K_M with llama.cpp ==="
if [ -d "llama.cpp" ]; then
    python3 llama.cpp/convert_hf_to_gguf.py "$OUTPUT_DIR/merged_fp16" --outfile "$OUTPUT_DIR/qwen-fp16.gguf"
    ./llama.cpp/llama-quantize "$OUTPUT_DIR/qwen-fp16.gguf" "$OUTPUT_DIR/qwen2.5-7b-thanphai-q4_k_m.gguf" Q4_K_M
    rm -f "$OUTPUT_DIR/qwen-fp16.gguf"
    echo "Quantized GGUF saved to $OUTPUT_DIR/qwen2.5-7b-thanphai-q4_k_m.gguf"
else
    echo "llama.cpp not found locally. To quantize:"
    echo "  git clone https://github.com/ggerganov/llama.cpp && cd llama.cpp && make"
    echo "  python convert_hf_to_gguf.py $OUTPUT_DIR/merged_fp16 --outfile $OUTPUT_DIR/qwen.gguf"
fi

echo "=== 3. Registering Model with Ollama ==="
if command -v ollama &> /dev/null; then
    ollama create thanphai-qwen -f 07_knowledge_decision_llm/training/Modelfile
    echo "Model registered in Ollama as 'thanphai-qwen'!"
else
    echo "Ollama CLI not in PATH. Run inside Docker with GPU passthrough."
fi
