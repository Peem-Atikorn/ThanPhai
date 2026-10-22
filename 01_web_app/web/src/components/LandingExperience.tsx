"use client";

import { useRef, useState } from "react";
import Dashboard from "@/components/Dashboard";
import Icon from "@/components/Icon";
import { HAZARDS, type Hazard } from "@/lib/api";

type MapView = "hazard" | "dams" | "rivers";

export default function LandingExperience() {
  const [hazard, setHazard] = useState<Hazard>("flood");
  const [view, setView] = useState<MapView>("hazard");
  const [riverViewRequest, setRiverViewRequest] = useState(0);
  const guide = useRef<HTMLDialogElement>(null);
  const searchDialog = useRef<HTMLDialogElement>(null);
  const [searchTarget, setSearchTarget] = useState<HTMLDivElement | null>(null);

  function scrollToMap() {
    document.getElementById("risk-map")?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  function focusAreaSearch() {
    searchDialog.current?.showModal();
    const input = document.getElementById("area-search");
    input?.focus({ preventScroll: true });
  }

  function finishAreaSearch() {
    searchDialog.current?.close();
    requestAnimationFrame(scrollToMap);
  }

  return (
    <div className="app-shell journey-shell journey-live-shell" data-active-category={view === "hazard" ? hazard : view}>
      <a className="skip-link" href="#risk-map">ข้ามไปยังแผนที่ความเสี่ยง</a>
      <section className="journey-hero" id="home" aria-labelledby="hero-title">
        <header className="journey-header">
          <a className="journey-brand" href="#home" aria-label="ทันภัย หน้าแรก"><span className="journey-brand-mark"><Icon name="shield" width={30} height={30} /></span><span>ทันภัย<small>THANPHAI</small></span></a>
          <div className="journey-header-message">
            <h1 id="hero-title">รู้ภัยก่อน ปลอดภัยกว่า</h1>
          </div>
          <div className="journey-header-actions"><button className="journey-search-icon" type="button" onClick={focusAreaSearch}>พื้นที่ที่คุณอยากรู้</button><a href="#risk-map" className="journey-top-cta">สำรวจพื้นที่ <span><Icon name="arrow" /></span></a></div>
        </header>
        <div className="journey-bottom">
          <div className="journey-category-bar" role="group" aria-label="เลือกประเภทข้อมูลบนแผนที่">
            {HAZARDS.map((item) => {
              const active = view === "hazard" && hazard === item.id;
              return <button key={item.id} data-category={item.id} className={"journey-category-button" + (active ? " is-active" : "")} type="button" aria-pressed={active} aria-controls="risk-map" disabled={!item.ready} onClick={() => { setHazard(item.id); setView("hazard"); }}><Icon name={item.id} /><span><strong>{item.th}</strong><small>ความเสี่ยงรายพื้นที่</small></span></button>;
            })}
            <button data-category="dams" className={"journey-category-button" + (view === "dams" ? " is-active" : "")} type="button" aria-pressed={view === "dams"} aria-controls="risk-map" onClick={() => setView("dams")}><Icon name="dam" /><span><strong>เขื่อน</strong><small>สถานการณ์น้ำในอ่าง</small></span></button>
            <button data-category="rivers" className={"journey-category-button" + (view === "rivers" ? " is-active" : "")} type="button" aria-pressed={view === "rivers"} aria-controls="risk-map" onClick={() => { setRiverViewRequest((value) => value + 1); setView("rivers"); }}><Icon name="river" /><span><strong>แม่น้ำ</strong><small>ระดับน้ำและการไหล</small></span></button>
          </div>
          <main className="main-content journey-dashboard journey-hero-dashboard" id="risk-map" tabIndex={-1}>
            <Dashboard activeHazard={hazard} onHazardChange={setHazard} activeView={view} onViewChange={setView} hideToolbar searchInHeader searchTarget={searchTarget} onAreaChosen={finishAreaSearch} onOpenAreaSearch={focusAreaSearch} riverViewRequest={riverViewRequest} /> <button className="text-button" type="button" onClick={() => guide.current?.showModal()}>วิธีใช้งานทันภัย <Icon name="arrow" /></button>
          </main><span className="journey-photo-note">ภาพประกอบบรรยากาศ · ไม่ใช่ภาพสถานการณ์ปัจจุบัน</span>
        </div>
      </section>
      <footer className="site-footer journey-footer" id="emergency"><a className="journey-footer-brand" href="#home"><Icon name="shield" /> ทันภัย <span>THANPHAI</span></a><div className="emergency-numbers"><Icon name="phone" /><strong>ฉุกเฉิน</strong><a href="tel:1784">1784 <span>ปภ.</span></a><a href="tel:1669">1669 <span>แพทย์ฉุกเฉิน</span></a><a href="tel:199">199 <span>แจ้งเหตุเพลิงไหม้</span></a></div><p>ติดตามประกาศจากหน่วยงานทางการเป็นหลัก · ThaiWater · กรมอุตุนิยมวิทยา · GISTDA · NASA FIRMS</p></footer>
      <dialog className="journey-search-dialog" ref={searchDialog} aria-label="ค้นหาพื้นที่">
        <button className="journey-dialog-close" type="button" aria-label="ปิดหน้าค้นหาพื้นที่" onClick={() => searchDialog.current?.close()}><Icon name="close" /></button>
        <div ref={setSearchTarget} />
      </dialog>
      <dialog className="journey-guide-dialog" ref={guide} aria-labelledby="guide-title">
        <button className="journey-dialog-close" type="button" aria-label="ปิดคำแนะนำ" onClick={() => guide.current?.close()}><Icon name="close" /></button>
        <span className="journey-section-label">A LITTLE KNOWLEDGE. MORE CONFIDENCE.</span><h2 id="guide-title">เริ่มต้นใช้งานทันภัย</h2><p>สำรวจข้อมูลใกล้ตัว เพื่อเตรียมพร้อมอย่างเข้าใจ</p>
        <ol><li><strong>ค้นหาพื้นที่ของคุณ</strong><p>กด “ค้นหาพื้นที่” ในกล่องข้อมูลข้างแผนที่ พิมพ์ชื่อจังหวัด อำเภอ หรือตำบล แล้วเลือกจากผลการค้นหาในหน้าต่างค้นหา</p></li><li><strong>เลือกสิ่งที่อยากรู้</strong><p>ดูความเสี่ยงฝนและพายุ น้ำท่วม ภัยแล้ง ไฟป่า หรือเปลี่ยนเป็นมุมมองเขื่อนและแม่น้ำ</p></li><li><strong>อ่านข้อมูลพร้อมแหล่งอ้างอิง</strong><p>ตรวจเวลาปรับปรุงข้อมูลและข้อจำกัดทุกครั้ง หากข้อมูลไม่พร้อม ระบบจะแสดงสถานะตามจริง</p></li></ol>
        <p className="journey-guide-notice"><Icon name="info" /> ทันภัยเป็นเครื่องมือประกอบการตัดสินใจ โปรดยึดประกาศเตือนจากหน่วยงานทางการเป็นหลัก</p><button className="journey-primary" type="button" onClick={() => { guide.current?.close(); scrollToMap(); }}>เริ่มสำรวจ <Icon name="arrow" /></button>
      </dialog>
    </div>
  );
}
