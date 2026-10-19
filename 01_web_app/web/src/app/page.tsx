import Dashboard from "@/components/Dashboard";
import Icon from "@/components/Icon";
import Link from "next/link";

export default function Home() {
  return (
    <div className="app-shell">
      <a href="#dashboard" className="skip-link">ข้ามไปยังแผนที่และข้อมูลความเสี่ยง</a>
      <header className="site-header">
        <Link className="brand" href="/" aria-label="ทันภัย หน้าหลัก">
          <span className="brand-symbol"><Icon name="shield" width="28" height="28" /></span>
          <span className="brand-name">ทันภัย<span>ThanPhai</span></span>
        </Link>
        <span className="header-description">รู้ความเสี่ยง เพื่อเตรียมพร้อม</span>
        <a className="emergency-link" href="#emergency"><Icon name="phone" />เบอร์ฉุกเฉิน</a>
      </header>
      <main id="dashboard" className="main-content" tabIndex={-1}>
        <div className="page-intro">
          <div><h1>สถานการณ์ภัยใกล้คุณ</h1><p>สำรวจความเสี่ยงรายพื้นที่ จากข้อมูลสภาพอากาศ น้ำ และดาวเทียม</p></div>
          <span className="coverage-label"><Icon name="map" />ครอบคลุมทั่วประเทศไทย</span>
        </div>
        <Dashboard />
      </main>
      <footer className="site-footer" id="emergency">
        <div className="emergency-numbers"><Icon name="phone" /><strong>ฉุกเฉิน</strong><a href="tel:1784">1784 <span>ปภ.</span></a><a href="tel:1669">1669 <span>เจ็บป่วย</span></a><a href="tel:199">199 <span>ดับเพลิง</span></a></div>
        <p>กรมอุตุนิยมวิทยา · สสน. · GISTDA · NASA FIRMS · OCHA HDX</p>
      </footer>
    </div>
  );
}
