# Hazard AI Agent (03)

ส่วนนี้รับคำถาม หา intent / ประเภทภัย / พื้นที่ / ช่วงเวลา แล้วเรียกข้อมูลจาก backend
และรวมคำตอบพร้อมหลักฐาน ระดับเตือนภัยมาจากโมดูล 07; Gemini ช่วยเรียบเรียงข้อความเท่านั้น

## ไฟล์ที่ใช้เริ่มงาน

- `hazard_agent/understanding.py`: เข้าใจคำถามด้วยกฎ รองรับภัยทั้ง 10 ประเภทใน `HazardType`
- `hazard_agent/agent.py`: เลือกข้อมูล ประกอบคำตอบ และเรียกคำแนะนำ RAG จากโมดูล 07
- `tests/test_agent.py`: ตัวอย่างคำถามและ regression tests ใช้ข้อมูลจำลอง ไม่ต้องมี API key
- `../02_api_backend/api_backend/main.py`: `POST /v1/ask` ที่เรียก agent ใน process เดียวกัน

ไฟล์ `01_env.txt`, `02_step.txt`, `03_process.txt` เป็นแนวทางออกแบบ บางรายการ เช่น
LLM planner, tool budget และ tracing ยังไม่ได้ implement ใน runtime ปัจจุบัน

## ติดตั้งและทดสอบบน Windows

รันจาก root ของ repository ด้วย Python 3.12 ขึ้นไป:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e shared -e 05_geo_data_integration -e 07_knowledge_decision_llm -e "03_hazard_ai_agent[dev]"
.\.venv\Scripts\python.exe -m pytest 03_hazard_ai_agent/tests -q
```

## ลองผ่าน API จริง

ทำขั้นตอนติดตั้ง backend และสร้าง boundary data ตาม README ที่ root ก่อน
ข้อมูล `data/processed/gazetteer.json` และไฟล์แผนที่ไม่ได้อยู่ใน Git
จากนั้นเปิด API ตามขั้นตอนของ root README แล้วเรียก:

```powershell
$body = @{ question = 'พรุ่งนี้ฝนตกที่อำเภอเสนาไหม' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://localhost:8002/v1/ask' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

ไม่ใส่ `GEMINI_API_KEY` ก็ใช้คำตอบตามกฎได้ ส่วนข้อมูลสดและพยากรณ์ขึ้นกับ provider
และข้อมูลพื้นที่ที่ติดตั้งไว้

## คำถามทั่วไป

- `สวัสดี`: ทักทายพร้อมตัวอย่างคำถาม 3 ข้อ (`greeting`)
- `ทันภัยใช้ข้อมูลจากไหน`: แสดงแหล่งข้อมูลและสถานะจากรายการเดียวกับ `/v1/sources` (`sources`)
- คำถามนอกขอบเขต: บอกรายชื่อภัยที่รองรับจาก `HAZARD_TH`

คำตอบเหล่านี้ใช้แม่แบบ ไม่ส่งให้ LLM เรียบเรียง หากยังไม่มีรายการแหล่งข้อมูลจะบอกว่าไม่มีข้อมูล
คำทักทายที่ตามด้วยคำถามภัย เช่น `สวัสดี อำเภอเสนาน้ำท่วมไหม` ยังตอบคำถามภัยตามปกติ

## การเลือกประกาศเตือน

คำตอบเลือกประกาศที่ตรงประเภทภัย จังหวัดของพื้นที่ที่ถาม และช่วงเวลาของคำถามพร้อมกัน:

- ใช้ `provinces_by_hazard` ก่อน `provinces` เพื่อไม่ให้ประกาศฝนของจังหวัดหนึ่งกลายเป็นประกาศน้ำท่วมด้วย
- ใช้ `impact_by_hazard[hazard][province]` ก่อน `impact_from` / `impact_to`
- วันสุดท้ายของประกาศยังนับรวมทั้งวัน; ช่วงเวลาคำถามใช้เวลาไทยและไม่รวมปลายช่วง
- ถ้าไม่ระบุเวลา ตัว parser ใช้ช่วงเริ่มจากขณะถามไปอีก 3 วัน
- tools รุ่นเก่าที่ไม่มีข้อมูลขอบเขตยังแสดงประกาศตามประเภทภัย จึงยังยืนยันพื้นที่/วันของประกาศเหล่านั้นไม่ได้

การกรองนี้ใช้กับข้อความประกาศ หลักฐาน และแหล่งอ้างอิงในคำตอบ ไม่เปลี่ยนระดับเตือนภัย
ที่ backend และโมดูล 07 คำนวณไว้แล้ว
