import type { ReactNode } from "react";
import { t as tr } from "@/lib/i18n";

// Lays out an assistant answer for reading: paragraphs, lists, level badges, and separate blocks for the
// official warning, the emergency numbers and the sources. Only the layout changes: every word of the answer
// is shown as the backend sent it (the official and emergency lines are copied verbatim there).

const LEVEL_ID: Record<string, string> = { "ปกติ": "NORMAL", "เฝ้าระวัง": "WATCH", "เตือนภัย": "WARNING", "วิกฤต": "CRITICAL" };
const LEVEL_WORD = "ปกติ|เฝ้าระวัง|เตือนภัย|วิกฤต";
// "• ฝนหนัก/พายุ: เฝ้าระวัง — ฝนสะสม …" and "• บ้านท่างิ้ว (แม่น้ำปิง) จ.นครสวรรค์: ปกติ — น้ำไหลผ่าน …"
const ITEM = new RegExp(`^(.+?): (${LEVEL_WORD})(?: — (.*))?$`);
// "น้ำท่วมระดับเตือนภัย" / "ระดับ: วิกฤต" inside a sentence.
const INLINE_LEVEL = new RegExp(`(ระดับ\\s*:?\\s*)(${LEVEL_WORD})`, "g");
const PHONE = /(\d{3,4})( \([^)]+\))/g;

type Block =
  | { kind: "para" | "heading" | "note"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "warning" | "emergency" | "sources"; text: string };

function blocks(text: string): Block[] {
  const out: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const last = out[out.length - 1];
    if (line.startsWith("•") || (raw.startsWith("  ") && last?.kind === "list")) {
      // An indented line continues the item above it (dam details); a bullet starts a new one.
      const item = line.replace(/^•\s*/, "");
      if (last?.kind === "list" && !line.startsWith("•")) last.items[last.items.length - 1] += "\n" + item;
      else if (last?.kind === "list") last.items.push(item);
      else out.push({ kind: "list", items: [item] });
    } else if (line.startsWith("⚠")) out.push({ kind: "warning", text: line.replace(/^⚠\s*/, "") });
    else if (line.startsWith("หากอยู่ในอันตราย") || /^โทร \d/.test(line)) out.push({ kind: "emergency", text: line });
    else if (line.startsWith("ที่มา:")) out.push({ kind: "sources", text: line.slice("ที่มา:".length).trim() });
    else if (line.endsWith(":")) out.push({ kind: "heading", text: line });
    else if (/^(ข้อมูลราย|ข้อมูลยังไม่ครบ)/.test(line)) out.push({ kind: "note", text: line });
    else out.push({ kind: "para", text: line });
  }
  return out;
}

function withLevels(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(INLINE_LEVEL)) {
    const start = m.index ?? 0;
    parts.push(tr(text.slice(at, start)), tr(m[1]));
    parts.push(<span key={start} className={"answer-level level-" + LEVEL_ID[m[2]]}>{tr(m[2])}</span>);
    at = start + m[0].length;
  }
  parts.push(tr(text.slice(at)));
  return parts;
}

/** "a, b, c" after a lead becomes a short list; one part stays a sentence. */
function details(text: string, separator: RegExp): ReactNode {
  const parts = text.split(separator).map(p => p.trim()).filter(Boolean);
  if (parts.length < 2) return <span>{withLevels(text)}</span>;
  return <ul className="answer-details">{parts.map((p, i) => <li key={i}>{withLevels(p)}</li>)}</ul>;
}

function paragraph(text: string): ReactNode {
  // "อ.เสนา …: น้ำท่วมระดับเตือนภัย — ระดับน้ำสูงกว่าตลิ่ง 0.43 ม., ดาวเทียมพบ …": the lead, then the readings.
  const dash = text.indexOf(" — ");
  if (dash > 0 && text.slice(dash + 3).includes(", ")) {
    return <><p className="answer-lead">{withLevels(text.slice(0, dash))}</p>{details(text.slice(dash + 3), /, (?![^()]*\))/)}</>;
  }
  return <p>{withLevels(text)}</p>;
}

function listItem(item: string): ReactNode {
  const [first, ...rest] = item.split("\n");
  const m = ITEM.exec(first);
  if (m) {
    return <>
      <strong>{tr(m[1])}</strong>{" "}<span className={"answer-level level-" + LEVEL_ID[m[2]]}>{tr(m[2])}</span>
      {m[3] && details(m[3], /, (?![^()]*\))| · /)}
      {rest.length > 0 && <ul className="answer-details">{rest.map((r, i) => <li key={i}>{withLevels(r)}</li>)}</ul>}
    </>;
  }
  if (rest.length) {
    // Dam: "เขื่อนภูมิพล จ.ตาก: น้ำพอใช้ มีน้ำ 71% …" with its details on the following lines.
    const colon = first.indexOf(": ");
    return <>
      {colon > 0 ? <><strong>{tr(first.slice(0, colon))}</strong>{tr(first.slice(colon))}</> : withLevels(first)}
      <ul className="answer-details">{rest.map((r, i) => <li key={i}>{withLevels(r)}</li>)}</ul>
    </>;
  }
  return withLevels(item);
}

function emergency(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(PHONE)) {
    const start = m.index ?? 0;
    parts.push(tr(text.slice(at, start)));
    parts.push(<a key={start} href={"tel:" + m[1]}>{m[1]}</a>, tr(m[2]));
    at = start + m[0].length;
  }
  parts.push(tr(text.slice(at)));
  return parts;
}

export default function AnswerText({ text }: { text: string }) {
  return <div className="answer-body">{blocks(text).map((b, i) => {
    switch (b.kind) {
      case "list": return <ul key={i} className="answer-list">{b.items.map((item, j) => <li key={j}>{listItem(item)}</li>)}</ul>;
      case "heading": return <p key={i} className="answer-heading">{withLevels(b.text)}</p>;
      case "note": return <p key={i} className="answer-note">{tr(b.text)}</p>;
      case "warning": {
        // "ประกาศกรมอุตุนิยมวิทยา: <title as issued>": the label in bold, the title untouched.
        const colon = b.text.indexOf(": ");
        return <div key={i} className="answer-callout answer-warning" role="note">
          <span aria-hidden="true">⚠</span>
          <p>{colon > 0 ? <><strong>{tr(b.text.slice(0, colon + 1))}</strong>{" "}{tr(b.text.slice(colon + 2))}</> : tr(b.text)}</p>
        </div>;
      }
      case "emergency": return <div key={i} className="answer-callout answer-emergency" role="note"><p>{emergency(b.text)}</p></div>;
      case "sources": return <p key={i} className="answer-sources"><span>{tr("ที่มา:")}</span> {tr(b.text)}</p>;
      default: return <div key={i}>{paragraph(b.text)}</div>;
    }
  })}</div>;
}
