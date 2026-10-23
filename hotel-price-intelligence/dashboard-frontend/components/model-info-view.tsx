"use client";
import { getModelInfo } from "@/lib/api";
import { longDate, pct, vnDateTime } from "@/lib/format";
import { horizonLabel, L, modeText } from "@/lib/labels";
import { useApi } from "@/lib/use-api";
import type { ClassifierHorizonModelInfo, HorizonModelInfo, ModelInfoResponse } from "@/types";
import { ErrorBlock, LoadingBlock } from "./state-views";

const triple = (v: number[]) => `${pct(v[0])} (CI95 theo khách sạn ${pct(v[1])} đến ${pct(v[2])})`;

export function ModelInfoView() {
  const state = useApi<ModelInfoResponse>("model-info", (signal) => getModelInfo(signal));
  return <div className="dashboard">
    <section className="page-heading"><div><div className="eyebrow"><span className="small-line" />VỀ MÔ HÌNH</div><h1>Bằng chứng và giới hạn.</h1>
      <p>Mô hình thử nghiệm (dev) · số liệu sao chép từ báo cáo huấn luyện đã ghim hash, không tính lại.</p></div></section>
    {state.status === "loading" && <LoadingBlock />}
    {state.status === "error" && <ErrorBlock error={state.error} onRetry={state.reload} />}
    {state.status === "ready" && <ModelInfoBody info={state.data} />}
  </div>;
}

function ModelInfoBody({ info }: { info: ModelInfoResponse }) {
  return <div className="model-stack">
    <section className="panel"><div className="section-title"><div><span className="section-kicker">DỮ LIỆU</span><h2>{info.snapshot.label}</h2></div></div>
      <table className="kv-table"><tbody>
        <tr><th scope="row">Mã snapshot</th><td>{info.snapshot.snapshotId}</td></tr>
        <tr><th scope="row">Ngày dữ liệu</th><td>{longDate(info.snapshot.dataAsOf)}</td></tr>
        <tr><th scope="row">Sinh lúc</th><td>{vnDateTime(info.snapshot.generatedAt)} (giờ Việt Nam)</td></tr>
        <tr><th scope="row">Mức bằng chứng</th><td>{info.snapshot.claimLevel}</td></tr>
        <tr><th scope="row">Dataset</th><td>{Object.entries(info.snapshot.datasetVersions).map(([k, v]) => `${k}: ${v}`).join(" · ")}</td></tr>
      </tbody></table>
      <ul className="note-list">{info.notes.map((n) => <li key={n}>{n}</li>)}</ul></section>
    {info.horizons.map((h) => <HorizonCard key={h.horizon} h={h} />)}
    <section className="panel"><div className="section-title"><div><span className="section-kicker">MỐC CHƯA HỖ TRỢ</span><h2>{L.unsupportedPanel}</h2></div></div>
      <ul className="note-list">{info.unsupportedHorizons.map((u) => <li key={u.horizon}>Chưa có mô hình cho {u.horizon} ngày.</li>)}</ul></section>
    <section className="panel"><div className="section-title"><div><span className="section-kicker">{L.classifierTitle.toUpperCase()}</span><h2>Lớp hướng giá (phụ)</h2></div></div>
      <p>{info.classifier.note}</p>
      {info.classifier.available && info.classifier.horizons.map((c) => <ClassifierCard key={c.horizon} c={c} />)}</section>
    <section className="panel"><div className="section-title"><div><span className="section-kicker">LUẬT</span><h2>Quy tắc hiển thị</h2></div></div>
      <ul className="note-list">{info.rules.map((r) => <li key={r.name}>{r.description}</li>)}</ul></section>
  </div>;
}

function HorizonCard({ h }: { h: HorizonModelInfo }) {
  return <section className="panel"><div className="section-title"><div><span className="section-kicker">HỒI QUY · {horizonLabel(h.horizon).toUpperCase()}</span><h2>{h.modelName}</h2><p>{h.family} · {h.datasetVersion} · {h.claimLevel}</p></div></div>
    <table className="kv-table"><tbody>
      <tr><th scope="row">Lift so với giữ nguyên giá — kiểm định (mô hình thuần)</th><td>{triple(h.evidence.validationLiftPct)}</td></tr>
      <tr><th scope="row">Lift — test (mô hình thuần, đã lộ)</th><td>{triple(h.evidence.testLiftPct)}</td></tr>
      <tr><th scope="row">Bản phục vụ (routing)</th><td>{h.routing.map((r) => `${modeText(r.mode)}: ${r.useModel ? "dùng mô hình" : "giữ nguyên giá"} (${r.reason})`).join(" · ")}</td></tr>
      <tr><th scope="row">Tương thích môi trường</th><td>{h.compatibility.status}: {h.compatibility.rows.toLocaleString("vi-VN")} hàng, lệch tối đa {h.compatibility.maxAbsDiffVnd.toExponential(2)} VND (dung sai {h.compatibility.toleranceVnd} VND), {h.compatibility.rowsOverTolerance} hàng vượt</td></tr>
      <tr><th scope="row">Môi trường huấn luyện</th><td>{Object.entries(h.trainedEnvironment).filter(([k]) => ["python", "scikit-learn", "numpy", "pandas", "joblib"].includes(k)).map(([k, v]) => `${k} ${v}`).join(" · ")}</td></tr>
      <tr><th scope="row">Môi trường đã kiểm</th><td>{Object.entries(h.validatedRuntime).map(([k, v]) => `${k} ${v}`).join(" · ")}</td></tr>
      <tr><th scope="row">Mã bundle (sha256)</th><td className="mono">{h.bundleSha256.slice(0, 16)}…</td></tr>
      <tr><th scope="row">Nguồn số liệu</th><td className="mono">{h.evidence.source} · {h.evidence.sourceSha256.slice(0, 12)}…</td></tr>
    </tbody></table>
    <ul className="note-list">{h.limitations.map((x) => <li key={x}>{x}</li>)}</ul></section>;
}

function ClassifierCard({ c }: { c: ClassifierHorizonModelInfo }) {
  return <div className="classifier-card">
    <h3>{horizonLabel(c.horizon)}: {c.modelName}</h3>
    <p>{c.family} · {c.datasetVersion} · mức bằng chứng: {c.claimLevel}</p>
    <table className="kv-table"><tbody>
      <tr><th scope="row">Ánh xạ lớp</th><td>{Object.entries(c.classMapping).map(([k, v]) => `${k} → ${v}`).join(" · ")}</td></tr>
      <tr><th scope="row">Tương thích môi trường</th><td>{c.compatibility.status}: {c.compatibility.rows.toLocaleString("vi-VN")} hàng, lệch xác suất tối đa {c.compatibility.maxAbsProbabilityDiff.toExponential(2)} (dung sai {c.compatibility.toleranceProbability}), {c.compatibility.labelMismatchCount} hàng lệch lớp</td></tr>
      <tr><th scope="row">Độ phủ trong snapshot</th><td>{c.coverage.snapshotRecords.toLocaleString("vi-VN")} bản ghi, trùng đúng tập bản ghi của mô hình hồi quy cùng mốc</td></tr>
      <tr><th scope="row">Môi trường đã kiểm</th><td>{Object.entries(c.validatedRuntime).map(([k, v]) => `${k} ${v}`).join(" · ")}</td></tr>
      <tr><th scope="row">Mã bundle (sha256)</th><td className="mono">{c.bundleSha256.slice(0, 16)}…</td></tr>
    </tbody></table>
    <ul className="note-list">{c.limitations.map((x) => <li key={x}>{x}</li>)}</ul>
  </div>;
}
