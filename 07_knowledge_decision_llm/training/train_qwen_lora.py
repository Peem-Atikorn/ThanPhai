"""QLoRA fine-tuning for Qwen2.5-7B-Instruct / 3B on Kaggle GPU T4 x2 or local RTX.

Orchestrates multi-GPU fine-tuning:
  GPU 0: Intent & Area Extraction Model
  GPU 1: Safe Facts Explainer Model

Usage on Kaggle T4 x2:
  # Train Intent on GPU 0
  CUDA_VISIBLE_DEVICES=0 python train_qwen_lora.py --task intent --model Qwen/Qwen2.5-7B-Instruct
  # Train Explainer on GPU 1
  CUDA_VISIBLE_DEVICES=1 python train_qwen_lora.py --task explainer --model Qwen/Qwen2.5-7B-Instruct
"""

import argparse
import json
import os
import sys
from pathlib import Path


def stale_explainer_data(train_file: Path) -> str | None:
    """Why this explainer dataset is from an old generator, or None.

    The current generator (PR #49 and later) answers English questions in English. A file made by the old
    one answers them in Thai, and a model trained on it does too (seen on 4 Nov 2026).
    """
    english = with_english_answer = empty = 0
    for line in train_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        question = row["input"].split("\n", 1)[0].replace("คำถามผู้ใช้: ", "")
        empty += not row["output"].strip()
        if question.isascii():
            english += 1
            with_english_answer += row["output"][:1].isascii()
    if empty:
        return f"{empty} samples have an empty answer"
    if english == 0:
        return "no English questions"
    if with_english_answer < english:
        return f"{english - with_english_answer} of {english} English questions are answered in Thai"
    return None


def make_trainer(model, tokenizer, train_file: Path, out_path: Path, max_seq_length: int, **overrides):
    """SFTTrainer on the ChatML text of each sample. `overrides` change training settings (tests use CPU, 2 steps).

    Written for the trl that `pip install unsloth` brings (0.18.2 to 1.15.0 in Nov 2026): the sequence length,
    text field and tokenizer go through SFTConfig / processing_class; older trl took them as SFTTrainer arguments.
    """
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    dataset = load_dataset("json", data_files=str(train_file), split="train")

    def format_prompts(batch):
        formatted = []
        for inst, inp, out in zip(batch["instruction"], batch["input"], batch["output"]):
            text = (
                f"<|im_start|>system\n{inst}<|im_end|>\n"
                f"<|im_start|>user\n{inp}<|im_end|>\n"
                f"<|im_start|>assistant\n{out}<|im_end|>"
            )
            formatted.append(text)
        return {"text": formatted}

    dataset = dataset.map(format_prompts, batched=True, remove_columns=dataset.column_names)

    # Checkpoint every hour (roughly 200-300 steps on T4)
    settings = dict(
        output_dir=str(out_path),
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=20,
        max_steps=500,
        learning_rate=2e-4,
        fp16=True,
        logging_steps=25,
        save_strategy="steps",
        save_steps=150,  # Hourly checkpoints
        save_total_limit=3,
        seed=42,
        max_length=max_seq_length,
        dataset_text_field="text",
        packing=False,
        report_to="none",
    )
    settings.update(overrides)
    return SFTTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=SFTConfig(**settings))


# Explainer prompts carry the agent's real facts: up to ~2,200 Qwen tokens with the system prompt and answer.
# A shorter limit cuts the end of the text, which is the answer the model should learn.
def train(task: str, model_name: str, data_dir: str, output_dir: str, max_seq_length: int = 2560):
    print(f"=== Starting QLoRA training for task: {task} ===")
    print(f"Base model: {model_name}")
    print(f"CUDA_VISIBLE_DEVICES: {os.getenv('CUDA_VISIBLE_DEVICES', 'default')}")

    train_file = Path(data_dir) / f"train_{task}.jsonl"
    if not train_file.exists():
        print(f"Error: Dataset {train_file} does not exist.")
        print(f"Run generate_{task}_dataset.py first!")
        sys.exit(1)
    if task == "explainer" and (problem := stale_explainer_data(train_file)):
        print(f"Error: {train_file} looks like it was made by an old generator: {problem}.")
        print("Pull the latest develop and run generate_explainer_dataset.py again before training.")
        sys.exit(1)

    try:
        from unsloth import FastLanguageModel
        use_unsloth = True
    except ImportError:
        print("Note: Unsloth not installed, falling back to standard PEFT + Transformers")
        use_unsloth = False

    out_path = Path(output_dir) / f"qwen_{task}_lora"
    out_path.mkdir(parents=True, exist_ok=True)

    if use_unsloth:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_name,
            max_seq_length=max_seq_length,
            dtype=None,
            load_in_4bit=True,
        )

        model = FastLanguageModel.get_peft_model(
            model,
            r=16,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            lora_alpha=32,
            lora_dropout=0,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=42,
        )
    else:
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="auto",
        )
        peft_config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, peft_config)

    trainer = make_trainer(model, tokenizer, train_file, out_path, max_seq_length)

    print("Beginning training...")
    trainer.train()

    print(f"Saving fine-tuned LoRA adapter to {out_path}...")
    model.save_pretrained(str(out_path))
    tokenizer.save_pretrained(str(out_path))
    print("Done!")


def main():
    parser = argparse.ArgumentParser(description="Fine-tune Qwen with QLoRA")
    parser.add_argument("--task", type=str, choices=["intent", "explainer"], required=True)
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-7B-Instruct", help="Base model")
    parser.add_argument("--data-dir", type=str, default="07_knowledge_decision_llm/training/data")
    parser.add_argument("--output-dir", type=str, default="07_knowledge_decision_llm/training/models")
    parser.add_argument("--max-seq-length", type=int, default=2560)
    args = parser.parse_args()

    train(args.task, args.model, args.data_dir, args.output_dir, args.max_seq_length)


if __name__ == "__main__":
    main()
