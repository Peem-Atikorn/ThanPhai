"use client";

import { t as tr, useLanguage, setLanguage } from "@/lib/i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Dashboard from "@/components/Dashboard";
import DataClock from "@/components/DataClock";
import Icon from "@/components/Icon";
import RainCloudSkeleton from "@/components/RainCloudSkeleton";
import type { RiskFilter } from "@/components/RiskSnapshot";
import { groupOf, GROUPS, itemTh, type MapItem, type MapLayerId, type MapView } from "@/lib/api";
export default function LandingExperience() {
    const language = useLanguage();
    const [hazard, setHazardState] = useState<MapLayerId>("overview");
    const [view, setViewState] = useState<MapView>("hazard");
    const [summaryTarget, setSummaryTarget] = useState<HTMLDivElement | null>(null);
    const [headlineTarget, setHeadlineTarget] = useState<HTMLDivElement | null>(null);
    const [ready, setReady] = useState(false);
    const finishLoading = useCallback(() => setReady(true), []);
    const [dataTime, setDataTime] = useState<string | null>(null);
    const [riskLevel, setRiskLevel] = useState<RiskFilter | null>(null);
    const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
    const navigation = useRef<HTMLElement>(null);
    useEffect(() => {
        function dismiss(event: PointerEvent) {
            if (!navigation.current?.contains(event.target as Node))
                setExpandedGroup(null);
        }
        document.addEventListener("pointerdown", dismiss);
        return () => document.removeEventListener("pointerdown", dismiss);
    }, []);
    const [hoveredNavigation, setHoveredNavigation] = useState<string | null>(null);
    const navigationHover = (key: string) => ({
        "data-hovered": hoveredNavigation === key,
        onPointerEnter: (event: React.PointerEvent<HTMLElement>) => { if (event.pointerType === "mouse" || event.pointerType === "pen")
            setHoveredNavigation(key); },
        onPointerLeave: () => setHoveredNavigation(current => current === key ? null : current),
        onPointerCancel: () => setHoveredNavigation(current => current === key ? null : current),
    });
    // The last item opened in each group (here or in the dashboard's sub-tabs), so the group button goes back there.
    const lastInGroup = useRef<Record<string, MapItem>>({});
    const currentItem: MapItem = view === "hazard" ? hazard : view;
    const currentGroup = groupOf(currentItem);
    function setHazard(value: MapLayerId) {
        setRiskLevel(null);
        lastInGroup.current[groupOf(value).id] = value;
        setHazardState(value);
    }
    function setView(value: MapView) {
        setRiskLevel(null);
        if (value !== "hazard")
            lastInGroup.current[groupOf(value).id] = value;
        setViewState(value);
    }
    function openGroup(group: (typeof GROUPS)[number]) {
        setExpandedGroup(group.items.length > 1 ? group.id : null);
        const item = lastInGroup.current[group.id] ?? group.items[0];
        openItem(item);
    }
    function openItem(item: MapItem) {
        if (item === "water-quality")
            setView("water-quality");
        else if (item === "dams")
            setView("dams");
        else if (item === "rivers")
            setView("rivers");
        else {
            setHazard(item);
            setView("hazard");
        }
    }
    function openRiskLevel(level: RiskFilter | null) {
        setExpandedGroup(null);
        setRiskLevel(level);
        setHazardState("overview");
        setViewState("hazard");
    }
    const guide = useRef<HTMLDialogElement>(null);
    const searchDialog = useRef<HTMLDialogElement>(null);
    const [searchTarget, setSearchTarget] = useState<HTMLDivElement | null>(null);
    const [locationTarget, setLocationTarget] = useState<HTMLDivElement | null>(null);
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
    return (<>
    {tr(!ready && <RainCloudSkeleton onContinue={finishLoading}/>)}
    <div className={"app-shell journey-shell journey-live-shell command-shell landscape-shell"} data-active-category={currentItem} data-risk-level={riskLevel ?? undefined} inert={!ready} aria-busy={!ready}>
      <a className="skip-link" href="#risk-map">{tr("ข้ามไปยังแผนที่ความเสี่ยง")}</a>
      <section className="journey-hero" id="home" aria-labelledby="hero-title">
        <header className="journey-header">
          <Link className="journey-brand" href="/" prefetch={false} onClick={(event) => { event.preventDefault(); window.location.assign("/"); }} aria-label={tr("ทันภัย หน้าแรก")}><span className="journey-brand-mark"><Icon name="shield" width={30} height={30}/></span><span>{tr("ทันภัย")}</span></Link>
          <div className="journey-header-message">
            <h1 id="hero-title">{tr("รู้ภัยก่อน ")}<span>{tr("ปลอดภัยกว่า")}</span></h1>
            <div className="journey-headline" ref={setHeadlineTarget}/>
          </div>
          <div className="header-snapshot" ref={setSummaryTarget}/>
          <div className="journey-header-actions"><div className="header-location-controls" ref={setLocationTarget}/>{tr(!locationTarget && <button className="journey-search-icon" type="button" onClick={focusAreaSearch}><Icon name="search"/><span>{tr("พื้นที่ที่คุณอยากรู้")}</span></button>)}</div>
        </header>
        <div className="journey-bottom">
          <nav className="journey-category-bar" ref={navigation} aria-label={tr("เลือกประเภทข้อมูลบนแผนที่")} onKeyDown={(event) => { if (event.key === "Escape") {
        setExpandedGroup(null);
        navigation.current?.querySelector<HTMLButtonElement>(`[data-category="${currentGroup.id}"]`)?.focus();
    } }}>
            {tr(GROUPS.map((group) => <div key={group.id} className="journey-category-entry" data-group={group.id}>
              <button data-category={group.id} {...navigationHover(`group:${group.id}`)} className={"journey-category-button" + (group.id === currentGroup.id ? " is-active" : "")} type="button" aria-pressed={group.id === currentGroup.id} aria-expanded={group.items.length > 1 ? expandedGroup === group.id : undefined} aria-controls={group.items.length > 1 ? `topics-${group.id}` : undefined} onClick={() => openGroup(group)}><Icon name={group.icon}/><span><strong>{tr(group.th)}</strong><small>{tr(group.items.map(itemTh).join(" · "))}</small></span></button>
              {tr(group.items.length > 1 && <div id={`topics-${group.id}`} className="category-flyout" data-open={expandedGroup === group.id} role="group" aria-label={tr(tr("เลือกหัวข้อในกลุ่ม") + tr(group.th) + tr(""))} aria-hidden={expandedGroup !== group.id} inert={expandedGroup !== group.id}>
                {tr(group.items.map(item => <button key={item} type="button" data-subcategory={item} {...navigationHover(`item:${item}`)} aria-pressed={item === currentItem} className={item === currentItem ? "is-active" : ""} onClick={() => { openItem(item); setExpandedGroup(null); setHoveredNavigation(null); navigation.current?.querySelector<HTMLButtonElement>(`[data-category="${group.id}"]`)?.focus({ preventScroll: true }); }}><Icon name={item === "dams" ? "dam" : item === "rivers" || item === "water-quality" ? "river" : item === "overview" ? "shield" : item}/><span>{tr(itemTh(item))}</span></button>))}
              </div>)}
            </div>))}
          </nav>
          <main className="main-content journey-dashboard journey-hero-dashboard" id="risk-map" tabIndex={-1}>
            <Dashboard activeHazard={hazard} onHazardChange={setHazard} activeView={view} onViewChange={setView} hideToolbar searchInHeader searchTarget={searchTarget} summaryTarget={summaryTarget} headlineTarget={headlineTarget} locationTarget={locationTarget} onOpenSearch={focusAreaSearch} onAreaChosen={finishAreaSearch} onReady={finishLoading} onDataTime={setDataTime} riskLevel={riskLevel} onRiskLevelChange={openRiskLevel}/>
          </main><span className="journey-photo-note">{tr("ภาพประกอบบรรยากาศ \u00B7 ไม่ใช่ภาพสถานการณ์ปัจจุบัน")}</span>
        </div>
      </section>
      <footer className="site-footer journey-footer" id="emergency"><div className="footer-utilities"><div className="language-switch" role="group" aria-label="Language / ภาษา"><button type="button" lang="th" aria-label="ภาษาไทย" aria-pressed={language === "th"} onClick={() => setLanguage("th")}>TH</button><button type="button" lang="en" aria-label="English" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>EN</button></div><button className="text-button" type="button" onClick={() => guide.current?.showModal()}>{tr("วิธีใช้งานทันภัย ")}<Icon name="arrow"/></button></div><Link className="journey-footer-brand" href="/" prefetch={false} onClick={(event) => { event.preventDefault(); window.location.assign("/"); }}><Icon name="shield"/>{tr(" ทันภัย ")}<span>THANPHAI</span></Link><div className="disclaimer"><Icon name="shield"/><p>{tr("ทันภัยเป็นเครื่องมือประเมินความเสี่ยง ไม่ใช่ประกาศทางการ ")}<strong>{tr("ให้ยึดประกาศจากหน่วยงานทางการเป็นหลัก")}</strong></p></div><DataClock updatedAt={dataTime}/><div className="emergency-numbers"><Icon name="phone"/><strong>{tr("ฉุกเฉิน")}</strong><a href="tel:1784">1784 <span>{tr("ปภ.")}</span></a><a href="tel:1669">1669 <span>{tr("แพทย์ฉุกเฉิน")}</span></a><a href="tel:199">199 <span>{tr("แจ้งเหตุเพลิงไหม้")}</span></a></div><p>{tr("ติดตามประกาศจากหน่วยงานทางการเป็นหลัก \u00B7 ThaiWater \u00B7 กรมอุตุนิยมวิทยา \u00B7 GISTDA \u00B7 NASA FIRMS")}</p></footer>
      <dialog className="journey-search-dialog" ref={searchDialog} aria-label={tr("ค้นหาพื้นที่")}>
        <button className="journey-dialog-close" type="button" aria-label={tr("ปิดหน้าค้นหาพื้นที่")} onClick={() => searchDialog.current?.close()}><Icon name="close"/></button>
        <div ref={setSearchTarget}/>
      </dialog>
      <dialog className="journey-guide-dialog" ref={guide} aria-labelledby="guide-title">
        <button className="journey-dialog-close" type="button" aria-label={tr("ปิดคำแนะนำ")} onClick={() => guide.current?.close()}><Icon name="close"/></button>
        <span className="journey-section-label">A LITTLE KNOWLEDGE. MORE CONFIDENCE.</span><h2 id="guide-title">{tr("เริ่มต้นใช้งานทันภัย")}</h2><p>{tr("สำรวจข้อมูลใกล้ตัว เพื่อเตรียมพร้อมอย่างเข้าใจ")}</p>
        <ol><li><strong>{tr("ค้นหาพื้นที่ของคุณ")}</strong><p>{tr("กด \u201Cพื้นที่ที่คุณอยากรู้\u201D ด้านบน พิมพ์ชื่อจังหวัด อำเภอ หรือตำบล แล้วเลือกจากผลการค้นหาในหน้าต่างค้นหา")}</p></li><li><strong>{tr("เลือกสิ่งที่อยากรู้")}</strong><p>{tr("เลือกกลุ่มภัยด้านบนแผนที่ เช่น น้ำ ฝนและพายุ ไฟและฝุ่น แล้วเลือกภัยย่อยในกลุ่ม เช่น เขื่อน แม่น้ำ หรือฝุ่น PM2.5")}</p></li><li><strong>{tr("อ่านข้อมูลพร้อมแหล่งอ้างอิง")}</strong><p>{tr("ตรวจเวลาปรับปรุงข้อมูลและข้อจำกัดทุกครั้ง หากข้อมูลไม่พร้อม ระบบจะแสดงสถานะตามจริง")}</p></li></ol>
        <p className="journey-guide-notice"><Icon name="info"/>{tr(" ทันภัยเป็นเครื่องมือประกอบการตัดสินใจ โปรดยึดประกาศเตือนจากหน่วยงานทางการเป็นหลัก")}</p><button className="journey-primary" type="button" onClick={() => { guide.current?.close(); scrollToMap(); }}>{tr("เริ่มสำรวจ ")}<Icon name="arrow"/></button>
      </dialog>
    </div>
    </>);
}
