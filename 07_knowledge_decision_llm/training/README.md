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
QWEN_MODEL=thanphai-qwen
# เมื่อรันผ่าน Docker Compose ให้ใช้ host.docker.internal (ตั้งค่าไว้เป็นค่าเริ่มต้น)
# หากรันโดยตรงบนเครื่อง host ใช้ http://127.0.0.1:11434
QWEN_ENDPOINT=http://host.docker.internal:11434
QWEN_TIMEOUT_MS=15000
```

ระบบจะเชื่อมต่อ `QwenExplainer` อัตโนมัติ หากเครื่องทีมออฟไลน์หรือคำตอบไม่ผ่านตัวตรวจ `validate()` ระบบจะสลับไปตอบด้วยเทมเพลตจากกฎโดยอัตโนมัติ (Zero Degradation)

### 4.1 เว็บรันบน VPS แต่ประมวลผลโมเดลที่เครื่องทีม (Tailscale)

VPS ทั่วไปไม่มี GPU และ Qwen 7B บน CPU ตอบช้าเกินเป้า 3 วินาทีมาก ให้ web + api-backend รันบน VPS แล้วส่งงาน LLM มาที่ Ollama บนเครื่องทีม (RTX 3060) ผ่าน Tailscale:

```
ผู้ใช้ → VPS (web + api-backend) ──Tailscale──→ เครื่องทีม (Ollama + RTX 3060)
```

1. ติดตั้ง [Tailscale](https://tailscale.com/download) ทั้งบน VPS และเครื่องทีม แล้วล็อกอินบัญชีเดียวกัน ดู IP วง `100.x.x.x` ของเครื่องทีมด้วย `tailscale ip -4`
2. บนเครื่องทีม (Windows) ให้ Ollama รับการเชื่อมต่อจากนอกเครื่อง แล้วรีสตาร์ต Ollama:
   ```powershell
   setx OLLAMA_HOST 0.0.0.0:11434
   ```
   และตั้ง Windows Firewall ให้พอร์ต 11434 รับเฉพาะวง Tailscale (รันใน PowerShell แบบ Administrator):
   ```powershell
   New-NetFirewallRule -DisplayName "Ollama (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
3. บน VPS ใส่ใน `.env` (ไม่ต้องใช้ `host.docker.internal` เพราะคอนเทนเนอร์ส่งออกผ่าน host ที่ต่อ Tailscale อยู่แล้ว):
   ```env
   LLM_PROVIDER=qwen
   QWEN_MODEL=thanphai-qwen
   QWEN_ENDPOINT=http://100.x.x.x:11434
   QWEN_TIMEOUT_MS=5000
   ```
4. ทดสอบจาก VPS: `curl http://100.x.x.x:11434/api/tags` ต้องเห็น `thanphai-qwen`

ข้อควรระวัง:
- **ห้ามเปิดพอร์ต 11434 สู่อินเทอร์เน็ตตรงๆ** Ollama ไม่มีการยืนยันตัวตน ใครก็ใช้ GPU ของเราได้
- เครื่องทีมปิดอยู่ = ทุกคำถามรอจนหมด `QWEN_TIMEOUT_MS` แล้วจึงตอบด้วยเทมเพลต จึงตั้ง timeout ให้สั้น (~5 วินาที) ถ้าเครื่องทีมเปิดไม่ตลอด ให้ใช้ `LLM_PROVIDER=gemini` เป็นค่าหลักและสลับเป็น `qwen` เฉพาะช่วงที่เครื่องเปิด
- ทางเลือกอื่น: reverse SSH tunnel (`ssh -R`) ต้องดูแลไม่ให้หลุดเองและตั้งให้คอนเทนเนอร์เข้าถึงพอร์ตได้ ส่วน Cloudflare Tunnel ต้องตั้ง Cloudflare Access กันคนนอกเพิ่ม

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
