# Qwen2.5-7B Local AI Training Pipeline (ThanPhai Module 07)

เอกสารขั้นตอนการเทรน Qwen2.5-7B-Instruct (Apache 2.0) เป็น Local AI ประจำเครื่องทีมเพื่อทำหน้าที่แทน Gemini สำหรับ:
1. **จับใจความ (Question Understanding):** แปลงคำถามภาษาไทยเป็น JSON (intent, areas, hazards, time_window) เมื่อตัวตรวจแบบ regex ไม่สามารถระบุได้
2. **เรียบเรียงคำตอบ (Safe Explainer):** สรุปข้อเท็จจริงตามกฎจากระบบเตือนภัยโดยคงตัวเลข ระดับเตือนภัย และข้อเท็จจริง 100%

---

## 1. การสร้างชุดข้อมูล (Dataset Generation)

ชุดข้อมูลสร้างขึ้นตามกฎของระบบ และคัดกรองด้วย `validate()` เพื่อให้ได้ Zero Defects ก่อนนำไปเทรน:

```bash
# 1. สร้างชุดข้อมูลจับใจความ (Intent & Areas)
PYTHONPATH=shared:01_web_app:02_api_backend:03_hazard_ai_agent:04_external_data_services:05_geo_data_integration:06_hazard_risk_models:07_knowledge_decision_llm:08_alert_feedback_monitoring \
python 07_knowledge_decision_llm/training/generate_intent_dataset.py --count 10000 --test-count 500

# 2. สร้างชุดข้อมูลเรียบเรียงคำตอบ (Explainer)
PYTHONPATH=shared:01_web_app:02_api_backend:03_hazard_ai_agent:04_external_data_services:05_geo_data_integration:06_hazard_risk_models:07_knowledge_decision_llm:08_alert_feedback_monitoring \
python 07_knowledge_decision_llm/training/generate_explainer_dataset.py --count 5000 --test-count 500
```

*ไฟล์ชุดข้อมูลจะถูกบันทึกลงใน `07_knowledge_decision_llm/training/data/` ซึ่งถูกระบุใน `.gitignore` ไม่ถูก commit เข้า git*

---

## 2. การเทรนบน Kaggle (GPU T4 x2)

ใช้ Unsloth QLoRA 4-bit เพื่อประหยัด VRAM และเพิ่มความเร็วในการเทรน โดยแยก 2 งานลง 2 GPU พร้อมกัน:

```bash
# บน GPU 0: เทรนงานจับใจความ
CUDA_VISIBLE_DEVICES=0 python 07_knowledge_decision_llm/training/train_qwen_lora.py \
    --task intent \
    --model Qwen/Qwen2.5-7B-Instruct

# บน GPU 1: เทรนงานเรียบเรียงคำตอบ
CUDA_VISIBLE_DEVICES=1 python 07_knowledge_decision_llm/training/train_qwen_lora.py \
    --task explainer \
    --model Qwen/Qwen2.5-7B-Instruct
```

*บันทึก Checkpoint ทุกๆ ~1 ชั่วโมง (~150 steps) และดาวน์โหลด LoRA adapters กลับมารวมบนเครื่องทีม*

---

## 3. แปลงเป็น GGUF Q4_K_M และรันบน Ollama (RTX 3060 12 GB)

เนื่องจาก Kaggle จำกัดดิสก์ 20 GB การแปลงเป็น GGUF และรันจะทำบนเครื่องทีม:

```bash
# รวม LoRA กับ Base model และแปลงเป็น GGUF
bash 07_knowledge_decision_llm/training/export_ollama.sh

# ตรวจสอบการทำงานของ Ollama
ollama run thanphai-qwen "สวัสดีครับ"
```

---

## 4. การเปิดใช้งานในระบบทันภัย

ใน `.env` ของโปรเจกต์:
```env
LLM_PROVIDER=qwen
QWEN_ENDPOINT=http://127.0.0.1:11434
QWEN_MODEL=thanphai-qwen
QWEN_TIMEOUT_MS=3000
```

ระบบจะเชื่อมต่อ `QwenExplainer` อัตโนมัติ หากเครื่องทีมออฟไลน์หรือคำตอบไม่ผ่านตัวตรวจ `validate()` ระบบจะสลับไปตอบด้วยเทมเพลตจากกฎโดยอัตโนมัติ (Zero Degradation)

---

## 5. การประเมินผล (Benchmarking)

```bash
PYTHONPATH=shared:01_web_app:02_api_backend:03_hazard_ai_agent:04_external_data_services:05_geo_data_integration:06_hazard_risk_models:07_knowledge_decision_llm:08_alert_feedback_monitoring \
python 07_knowledge_decision_llm/training/benchmark_eval.py
```
เกณฑ์การผ่าน (SOTA Acceptance Gate):
- จับ intent และพื้นที่ถูกต้องมากกว่า baseline บนชุดทดสอบ
- คำตอบที่เปลี่ยนตัวเลขหรือระดับภัย = 0 ข้อ (Pass rate 100%)
- Latency ในการตอบเฉลี่ย < 3.0 วินาทีบนเครื่องทีม
