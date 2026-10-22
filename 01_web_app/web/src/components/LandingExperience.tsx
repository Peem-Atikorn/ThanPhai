"use client";

import { useRef, useState } from "react";
import Dashboard from "@/components/Dashboard";
import Icon from "@/components/Icon";
import { groupOf, GROUPS, itemTh, type MapItem, type MapLayerId } from "@/lib/api";

type MapView = "hazard" | "dams" | "rivers";

export default function LandingExperience() {
  const [hazard, setHazardState] = useState<MapLayerId>("overview");
  const [view, setViewState] = useState<MapView>("hazard");
  const [riverViewRequest, setRiverViewRequest] = useState(0);
  // The last item opened in each group (here or in the dashboard's sub-tabs), so the group button goes back there.
  const lastInGroup = useRef<Record<string, MapItem>>({});
  const currentItem: MapItem = view === "hazard" ? hazard : view;
  const currentGroup = groupOf(currentItem);

  function setHazard(value: MapLayerId) {
    lastInGroup.current[groupOf(value).id] = value;
    setHazardState(value);
  }

  function setView(value: MapView) {
    if (value !== "hazard") lastInGroup.current[groupOf(value).id] = value;
    setViewState(value);
  }

  function openGroup(group: (typeof GROUPS)[number]) {
    const item = lastInGroup.current[group.id] ?? group.items[0];
    if (item === "dams") setView("dams");
    else if (item === "rivers") { setRiverViewRequest((value) => value + 1); setView("rivers"); }
    else { setHazard(item); setView("hazard"); }
  }
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
    <div className="app-shell journey-shell journey-live-shell" data-active-category={currentItem}>
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
            {GROUPS.map((group) => {
              const active = group.id === currentGroup.id;
              return <button key={group.id} data-category={group.id} className={"journey-category-button" + (active ? " is-active" : "")} type="button" aria-pressed={active} aria-controls="risk-map" onClick={() => openGroup(group)}><Icon name={group.icon} /><span><strong>{group.th}</strong><small>{group.items.map(itemTh).join(" · ")}</small></span></button>;
            })}
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
        <ol><li><strong>ค้นหาพื้นที่ของคุณ</strong><p>กด “ค้นหาพื้นที่” ในกล่องข้อมูลข้างแผนที่ พิมพ์ชื่อจังหวัด อำเภอ หรือตำบล แล้วเลือกจากผลการค้นหาในหน้าต่างค้นหา</p></li><li><strong>เลือกสิ่งที่อยากรู้</strong><p>เลือกกลุ่มภัยด้านบนแผนที่ เช่น น้ำ ฝนและพายุ ไฟและฝุ่น แล้วเลือกภัยย่อยในกลุ่ม เช่น เขื่อน แม่น้ำ หรือฝุ่น PM2.5</p></li><li><strong>อ่านข้อมูลพร้อมแหล่งอ้างอิง</strong><p>ตรวจเวลาปรับปรุงข้อมูลและข้อจำกัดทุกครั้ง หากข้อมูลไม่พร้อม ระบบจะแสดงสถานะตามจริง</p></li></ol>
        <p className="journey-guide-notice"><Icon name="info" /> ทันภัยเป็นเครื่องมือประกอบการตัดสินใจ โปรดยึดประกาศเตือนจากหน่วยงานทางการเป็นหลัก</p><button className="journey-primary" type="button" onClick={() => { guide.current?.close(); scrollToMap(); }}>เริ่มสำรวจ <Icon name="arrow" /></button>
      </dialog>
    </div>
  );
}
