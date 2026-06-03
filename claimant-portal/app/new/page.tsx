"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import type {
  ReserveResponse,
  UploadSlot,
} from "@/lib/types";

// ── Types ──────────────────────────────────────────────────

interface FormField {
  id: string;
  label: string;
  type: string;
  required: boolean;
  section: string;
}

interface FormSchema {
  title: string;
  fields: FormField[];
}

interface SchemasResponse {
  refund_types: string[];
  schemas: Record<string, FormSchema>;
  merged_fields: FormField[];
}

// ── Header ─────────────────────────────────────────────────

function PageHeader() {
  return (
    <div
      className="flex items-center gap-4 px-6 py-4 mb-8"
      style={{ background: "var(--navy)" }}
    >
      <div>
        <div
          className="text-white font-bold uppercase tracking-wide text-base"
          style={{ fontFamily: "Montserrat, sans-serif" }}
        >
          County of Riverside
        </div>
        <div className="text-gray-300 text-xs uppercase tracking-widest">
          Office of the Auditor-Controller
        </div>
      </div>
    </div>
  );
}

// ── Success screen ─────────────────────────────────────────

function SuccessScreen({ submissionId }: { submissionId: string }) {
  return (
    <div className="text-center py-10 px-4">
      <div
        className="text-xl font-bold mb-3"
        style={{ fontFamily: "Montserrat, sans-serif", color: "var(--green)" }}
      >
        Claim Received
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--text-muted)" }}>
        Your claim has been submitted to the Riverside County Auditor-Controller&apos;s
        office. Please allow up to 90 days for processing.
      </p>
      <div
        className="inline-block px-6 py-4 border-2 mb-4"
        style={{ borderColor: "var(--navy)", background: "#f0f4ff" }}
      >
        <div
          className="text-xs uppercase tracking-widest mb-1"
          style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
        >
          Your Claim ID
        </div>
        <div
          className="text-2xl font-bold tracking-wider"
          style={{ fontFamily: "Montserrat, sans-serif", color: "var(--navy)" }}
        >
          {submissionId}
        </div>
      </div>
      <p className="text-xs mb-6" style={{ color: "var(--text-muted)" }}>
        Save this Claim ID. You will need it along with your mailing address to
        check your claim status at any time.
      </p>
      <div className="flex gap-3 justify-center flex-wrap">
        <a
          href="/my-claim"
          className="px-5 py-2 border-2 text-sm font-bold uppercase tracking-wide"
          style={{
            fontFamily: "Montserrat, sans-serif",
            borderColor: "var(--navy)",
            color: "var(--navy)",
          }}
        >
          Check claim status
        </a>
        <button
          type="button"
          onClick={() => window.print()}
          className="px-5 py-2 border text-sm"
          style={{ borderColor: "var(--border-light)", color: "var(--text-muted)" }}
        >
          Print this page
        </button>
      </div>
    </div>
  );
}

// ── Signature canvas ───────────────────────────────────────

function SignatureCanvas({
  onSigned,
}: {
  onSigned: (dataUrl: string | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawingRef = useRef(false);
  const signedRef = useRef(false);

  function clearSig() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    signedRef.current = false;
    onSigned(null);
  }

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#002f87";

    function getPoint(e: MouseEvent | TouchEvent) {
      const r = canvas!.getBoundingClientRect();
      const t = "touches" in e ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    }
    function start(e: MouseEvent | TouchEvent) {
      drawingRef.current = true;
      const p = getPoint(e);
      ctx!.beginPath();
      ctx!.moveTo(p.x, p.y);
      signedRef.current = true;
      e.preventDefault();
    }
    function move(e: MouseEvent | TouchEvent) {
      if (!drawingRef.current) return;
      const p = getPoint(e);
      ctx!.lineTo(p.x, p.y);
      ctx!.stroke();
      onSigned(canvas!.toDataURL("image/png"));
      e.preventDefault();
    }
    function end() {
      drawingRef.current = false;
    }

    canvas.addEventListener("mousedown", start);
    canvas.addEventListener("mousemove", move);
    canvas.addEventListener("mouseup", end);
    canvas.addEventListener("mouseleave", end);
    canvas.addEventListener("touchstart", start, { passive: false });
    canvas.addEventListener("touchmove", move, { passive: false });
    canvas.addEventListener("touchend", end);

    return () => {
      canvas.removeEventListener("mousedown", start);
      canvas.removeEventListener("mousemove", move);
      canvas.removeEventListener("mouseup", end);
      canvas.removeEventListener("mouseleave", end);
      canvas.removeEventListener("touchstart", start);
      canvas.removeEventListener("touchmove", move);
      canvas.removeEventListener("touchend", end);
    };
  }, [onSigned]);

  return (
    <div>
      <div
        className="border-2 border-dashed p-1"
        style={{ borderColor: "var(--border-light)", background: "#fafbfc" }}
      >
        <canvas
          ref={canvasRef}
          style={{
            display: "block",
            width: "100%",
            height: "100px",
            background: "#fff",
            cursor: "crosshair",
          }}
        />
      </div>
      <div className="flex justify-end mt-1">
        <button
          type="button"
          onClick={clearSig}
          className="text-xs px-3 py-1 border"
          style={{ borderColor: "var(--border-light)", color: "var(--text-muted)" }}
        >
          Clear
        </button>
      </div>
      <label
        className="block text-xs uppercase tracking-widest mt-1"
        style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
      >
        Signature (Payee / Business Claimant)
      </label>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────

export default function NewClaimPage() {
  const [phase, setPhase] = useState<
    "loading" | "mini-form" | "form" | "submitting" | "success" | "error"
  >("loading");

  // URL params (bot handoff)
  const [urlName, setUrlName] = useState("");
  const [urlType, setUrlType] = useState("");
  const [urlAddress, setUrlAddress] = useState("");

  // Mini-form state (no URL params case)
  const [miniName, setMiniName] = useState("");
  const [miniAddress, setMiniAddress] = useState("");
  const [miniType, setMiniType] = useState<string[]>([]);

  // Full form
  const [schemas, setSchemas] = useState<SchemasResponse | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [sigDataUrl, setSigDataUrl] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [submissionId, setSubmissionId] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  // Reserve-on-load state (bot handoff)
  const [reservedId, setReservedId] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const name = params.get("name") ?? "";
    const type = params.get("type") ?? "";
    const address = params.get("address") ?? "";

    setUrlName(name);
    setUrlType(type);
    setUrlAddress(address);

    if (name && type && address) {
      // Bot handoff: reserve immediately then load form
      (async () => {
        try {
          setPhase("loading");
          const res = await apiFetch<ReserveResponse>("/claimant/reserve", {
            method: "POST",
            body: JSON.stringify({ name, refundType: type, address }),
          });
          setReservedId(res.submissionId);
          // Pre-fill values
          setFormValues({ name, address });
          await loadSchemas(type.split(",").filter(Boolean));
          setPhase("form");
        } catch (e) {
          setErrorMsg(e instanceof Error ? e.message : String(e));
          setPhase("error");
        }
      })();
    } else {
      setPhase("mini-form");
    }
  }, []);

  async function loadSchemas(types: string[]) {
    const data = await apiFetch<SchemasResponse>(
      `/form-schemas?types=${encodeURIComponent(types.join(","))}`,
    );
    setSchemas(data);
    // Pre-fill amounts/ids from URL if available (bot handoff)
    const params = new URLSearchParams(window.location.search);
    const amounts = (params.get("amount") ?? "").split(",");
    const ids = (params.get("id") ?? "").split(",");
    const pre: Record<string, string> = {};
    types.forEach((rt, i) => {
      if (rt === "PROPERTY_TAX") {
        if (amounts[i]) pre["refund_amount"] = amounts[i];
      } else {
        if (amounts[i]) pre["warrant_amount"] = amounts[i];
        if (ids[i]) pre["warrant_number"] = ids[i];
      }
    });
    if (params.get("assessment")) pre["assessment_number"] = params.get("assessment")!;
    if (params.get("taxyear")) pre["tax_year"] = params.get("taxyear")!;
    setFormValues((v) => ({ ...v, ...pre }));
  }

  async function handleMiniSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!miniName.trim() || !miniAddress.trim() || miniType.length === 0) {
      setErrorMsg("Please fill in all fields and select at least one claim type.");
      return;
    }
    setErrorMsg("");
    try {
      setPhase("loading");
      const res = await apiFetch<ReserveResponse>("/claimant/reserve", {
        method: "POST",
        body: JSON.stringify({
          name: miniName,
          refundType: miniType.join(","),
          address: miniAddress,
        }),
      });
      setReservedId(res.submissionId);
      setFormValues({ name: miniName, address: miniAddress });
      setUrlName(miniName);
      setUrlAddress(miniAddress);
      setUrlType(miniType.join(","));
      await loadSchemas(miniType);
      setPhase("form");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase("mini-form");
    }
  }

  function toggleType(t: string) {
    setMiniType((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
    );
  }

  function handleFieldChange(id: string, value: string) {
    setFormValues((v) => ({ ...v, [id]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!sigDataUrl) {
      setStatusMsg("Please sign the form before submitting.");
      return;
    }
    if (!schemas) return;

    // Validate required fields
    const missing: string[] = [];
    for (const f of schemas.merged_fields) {
      if (f.required && f.type !== "checkbox") {
        const val = (formValues[f.id] ?? "").trim();
        if (!val) missing.push(f.label);
      }
    }
    if (missing.length > 0) {
      setStatusMsg(`Please fill required fields: ${missing.join(", ")}`);
      return;
    }

    setPhase("submitting");
    setStatusMsg("Submitting your claim…");

    try {
      const types = (urlType || miniType.join(",")).split(",").filter(Boolean);
      const name = formValues["name"] || urlName || miniName;
      const refundType = types.join(",");
      const address = formValues["address"] || urlAddress || miniAddress;

      // Build unified form JSON blob
      const unifiedPayload = {
        formData: formValues,
        refundTypes: types,
        signature: sigDataUrl,
        submittedAt: new Date().toISOString(),
      };
      const unifiedBlob = new Blob([JSON.stringify(unifiedPayload)], {
        type: "application/json",
      });

      // Build file list: unified form + any user attachments
      const fileList: { filename: string; contentType: string }[] = [
        { filename: "unified-form.json", contentType: "application/json" },
        ...files.map((f) => ({ filename: f.name, contentType: f.type || "application/octet-stream" })),
      ];

      // POST /upload to get presigned URLs, passing the reserved ID so the
      // backend reuses it instead of generating a new one.
      const uploadBody: Record<string, unknown> = { name, refundType, address, files: fileList };
      if (reservedId) uploadBody.submissionId = reservedId;
      const uploadRes = await apiFetch<{ submissionId: string; uploads: UploadSlot[] }>(
        "/upload",
        {
          method: "POST",
          body: JSON.stringify(uploadBody),
        },
      );
      const sid = uploadRes.submissionId;
      const uploadSlots: UploadSlot[] = uploadRes.uploads;

      // Upload files to S3 presigned URLs
      const unifiedSlot = uploadSlots.find((u) => u.filename === "unified-form.json");
      if (unifiedSlot) {
        await fetch(unifiedSlot.uploadUrl, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: unifiedBlob,
        });
      }
      for (const file of files) {
        const slot = uploadSlots.find((u) => u.filename === file.name);
        if (slot) {
          await fetch(slot.uploadUrl, {
            method: "PUT",
            headers: { "Content-Type": file.type || "application/octet-stream" },
            body: file,
          });
        }
      }

      // Notify backend upload complete
      await apiFetch("/upload-complete", {
        method: "POST",
        body: JSON.stringify({
          submissionId: sid,
          filenames: fileList.map((f) => f.filename),
        }),
      });

      setSubmissionId(reservedId || sid);
      setPhase("success");
    } catch (e) {
      const msg = e instanceof ApiError
        ? `Submission failed (${e.status}): ${e.message}`
        : `Submission failed: ${e instanceof Error ? e.message : String(e)}`;
      setStatusMsg(msg);
      setPhase("form");
    }
  }

  // ── Render ─────────────────────────────────────────────

  const typeLabels: Record<string, string> = {
    STALE_WARRANT: "Stale-Dated Warrant",
    PAYROLL: "Payroll Warrant",
    PROPERTY_TAX: "Property Tax",
  };

  return (
    <div style={{ background: "var(--bg)", minHeight: "100vh" }}>
      <div
        className="max-w-2xl mx-auto mt-6 mb-6"
        style={{ background: "var(--surface)", border: "1px solid #999", boxShadow: "0 2px 12px rgba(0,0,0,0.12)" }}
      >
        <PageHeader />

        <div className="px-8 pb-8">
          {/* ── Loading ── */}
          {phase === "loading" && (
            <div
              className="px-4 py-3 text-sm border-l-4"
              style={{ background: "#e3f2fd", borderColor: "#0c71ca", color: "#1565c0" }}
            >
              Loading your claim form…
            </div>
          )}

          {/* ── Error ── */}
          {phase === "error" && (
            <div>
              <div
                className="px-4 py-3 text-sm border-l-4 mb-4"
                style={{ background: "#fce4ec", borderColor: "var(--red)", color: "#c62828" }}
              >
                {errorMsg || "An unexpected error occurred. Please try again."}
              </div>
              <a
                href="/"
                className="text-sm underline"
                style={{ color: "var(--navy)" }}
              >
                Return to home
              </a>
            </div>
          )}

          {/* ── Success ── */}
          {phase === "success" && <SuccessScreen submissionId={submissionId} />}

          {/* ── Mini form (manual entry) ── */}
          {phase === "mini-form" && (
            <form onSubmit={handleMiniSubmit}>
              <h2
                className="text-base font-bold uppercase tracking-wide mb-4 pb-2 border-b"
                style={{
                  fontFamily: "Montserrat, sans-serif",
                  borderColor: "var(--border)",
                  color: "var(--navy)",
                }}
              >
                Start Your Claim
              </h2>

              {errorMsg && (
                <div
                  className="px-4 py-3 text-sm border-l-4 mb-4"
                  style={{ background: "#fce4ec", borderColor: "var(--red)", color: "#c62828" }}
                >
                  {errorMsg}
                </div>
              )}

              <div className="mb-4">
                <label
                  htmlFor="mini-name"
                  className="block text-xs uppercase tracking-widest mb-1"
                  style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
                >
                  Full Name <span style={{ color: "var(--red)" }}>*</span>
                </label>
                <input
                  id="mini-name"
                  type="text"
                  required
                  value={miniName}
                  onChange={(e) => setMiniName(e.target.value)}
                  className="w-full border-b bg-transparent outline-none py-2 text-sm"
                  style={{ borderColor: "var(--border)" }}
                />
              </div>

              <div className="mb-4">
                <label
                  htmlFor="mini-address"
                  className="block text-xs uppercase tracking-widest mb-1"
                  style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
                >
                  Mailing Address <span style={{ color: "var(--red)" }}>*</span>
                </label>
                <textarea
                  id="mini-address"
                  required
                  value={miniAddress}
                  onChange={(e) => setMiniAddress(e.target.value)}
                  rows={2}
                  className="w-full border-b bg-transparent outline-none py-2 text-sm resize-none"
                  style={{ borderColor: "var(--border)" }}
                  placeholder="Street, City, State ZIP"
                />
              </div>

              <div className="mb-6">
                <span
                  className="block text-xs uppercase tracking-widest mb-2"
                  style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
                >
                  Claim Type(s) <span style={{ color: "var(--red)" }}>*</span>
                </span>
                <div className="flex flex-col gap-2">
                  {Object.entries(typeLabels).map(([k, label]) => (
                    <label key={k} className="flex items-center gap-2 text-sm cursor-pointer">
                      <input
                        type="checkbox"
                        checked={miniType.includes(k)}
                        onChange={() => toggleType(k)}
                        className="w-4 h-4"
                        style={{ accentColor: "var(--navy)" }}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </div>

              <button
                type="submit"
                className="w-full py-3 font-bold uppercase tracking-wide text-sm"
                style={{
                  fontFamily: "Montserrat, sans-serif",
                  background: "var(--yellow)",
                  color: "var(--navy-dark)",
                  border: "none",
                }}
              >
                Continue to Form
              </button>
            </form>
          )}

          {/* ── Full claim form ── */}
          {(phase === "form" || phase === "submitting") && schemas && (
            <form onSubmit={handleSubmit}>
              {/* Show reserved claim ID */}
              {reservedId && (
                <div
                  className="px-4 py-3 text-sm border-l-4 mb-6"
                  style={{ background: "#e8f5e9", borderColor: "var(--green)", color: "var(--green)" }}
                >
                  <strong>Your Claim ID: {reservedId}</strong>
                  <br />
                  <span style={{ color: "var(--text-muted)" }}>
                    Save this ID — you can use it with your address to check your status later.
                  </span>
                </div>
              )}

              {/* Form sections */}
              {(() => {
                const { merged_fields, refund_types, schemas: typeSchemas } = schemas;
                const sectionOrder = ["common", ...refund_types];
                const bySection: Record<string, FormField[]> = {};
                for (const f of merged_fields) {
                  const s = f.section || "common";
                  (bySection[s] = bySection[s] || []).push(f);
                }

                return sectionOrder.map((section) => {
                  const fields = bySection[section];
                  if (!fields || fields.length === 0) return null;

                  const sectionTitle =
                    section === "common"
                      ? "Claimant Information"
                      : typeSchemas[section]?.title ?? section;

                  return (
                    <div key={section}>
                      <div
                        className="text-center text-xs font-bold uppercase tracking-widest py-2 mb-4 border-t border-b"
                        style={{
                          fontFamily: "Montserrat, sans-serif",
                          borderColor: "var(--border)",
                        }}
                      >
                        {sectionTitle}
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                        {fields.map((f) => {
                          const isWide =
                            f.type === "address" || f.type === "textarea" || f.type === "checkbox";

                          if (f.type === "checkbox") {
                            return (
                              <div
                                key={f.id}
                                className="col-span-full flex items-center gap-4 py-1"
                              >
                                <span className="text-sm">{f.label}</span>
                                <div className="flex gap-4 ml-auto">
                                  {["true", "false"].map((val) => (
                                    <label
                                      key={val}
                                      className="flex items-center gap-1 text-sm font-bold cursor-pointer"
                                    >
                                      <input
                                        type="radio"
                                        name={f.id}
                                        value={val}
                                        checked={formValues[f.id] === val}
                                        onChange={(e) =>
                                          handleFieldChange(f.id, e.target.value)
                                        }
                                        style={{ accentColor: "var(--navy)" }}
                                      />
                                      {val === "true" ? "Yes" : "No"}
                                    </label>
                                  ))}
                                </div>
                              </div>
                            );
                          }

                          const inputType: Record<string, string> = {
                            text: "text",
                            email: "email",
                            tel: "tel",
                            date: "date",
                            number: "number",
                          };

                          const input =
                            f.type === "address" || f.type === "textarea" ? (
                              <textarea
                                id={`f_${f.id}`}
                                required={f.required}
                                rows={2}
                                value={formValues[f.id] ?? ""}
                                onChange={(e) => handleFieldChange(f.id, e.target.value)}
                                className="w-full border-b bg-transparent outline-none py-2 text-sm resize-none"
                                style={{ borderColor: "var(--border)" }}
                              />
                            ) : (
                              <input
                                id={`f_${f.id}`}
                                type={inputType[f.type] ?? "text"}
                                required={f.required}
                                value={formValues[f.id] ?? ""}
                                onChange={(e) => handleFieldChange(f.id, e.target.value)}
                                className="w-full border-b bg-transparent outline-none py-2 text-sm"
                                style={{ borderColor: "var(--border)" }}
                              />
                            );

                          return (
                            <div
                              key={f.id}
                              className={isWide ? "col-span-full" : ""}
                            >
                              {input}
                              <label
                                htmlFor={`f_${f.id}`}
                                className="block text-xs uppercase tracking-widest mt-1"
                                style={{
                                  fontFamily: "Montserrat, sans-serif",
                                  color: "var(--text-muted)",
                                }}
                              >
                                {f.label}
                                {f.required && (
                                  <span style={{ color: "var(--red)" }}> *</span>
                                )}
                              </label>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                });
              })()}

              {/* File upload */}
              <div className="mb-6">
                <div
                  className="text-center text-xs font-bold uppercase tracking-widest py-2 mb-3 border-t border-b"
                  style={{ fontFamily: "Montserrat, sans-serif", borderColor: "var(--border)" }}
                >
                  Supporting Documents (optional)
                </div>
                <input
                  type="file"
                  multiple
                  accept=".pdf,.jpg,.jpeg,.png,.heic"
                  onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                  className="text-sm w-full"
                />
                {files.length > 0 && (
                  <ul className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                    {files.map((f, i) => (
                      <li key={i}>{f.name}</li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Signature */}
              <div className="mb-6">
                <div
                  className="text-center text-xs font-bold uppercase tracking-widest py-2 mb-3 border-t border-b"
                  style={{ fontFamily: "Montserrat, sans-serif", borderColor: "var(--border)" }}
                >
                  Signature
                </div>
                <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
                  By signing, you affirm that the information provided is true and
                  complete.
                </p>
                <SignatureCanvas onSigned={setSigDataUrl} />
              </div>

              {/* Status message */}
              {statusMsg && (
                <div
                  className="px-4 py-3 text-sm border-l-4 mb-4"
                  style={{
                    background: phase === "submitting" ? "#e3f2fd" : "#fce4ec",
                    borderColor: phase === "submitting" ? "#0c71ca" : "var(--red)",
                    color: phase === "submitting" ? "#1565c0" : "#c62828",
                  }}
                >
                  {statusMsg}
                </div>
              )}

              <button
                type="submit"
                disabled={phase === "submitting"}
                className="w-full py-3 font-bold uppercase tracking-wide text-sm disabled:opacity-50"
                style={{
                  fontFamily: "Montserrat, sans-serif",
                  background: "var(--yellow)",
                  color: "var(--navy-dark)",
                  border: "none",
                  cursor: phase === "submitting" ? "not-allowed" : "pointer",
                }}
              >
                {phase === "submitting" ? "Submitting…" : "Submit Claim"}
              </button>
            </form>
          )}
        </div>

        <footer
          className="mt-4 border-t pt-4 pb-6 text-center text-xs"
          style={{ borderColor: "var(--border-light)", color: "var(--text-muted)" }}
        >
          County of Riverside · Office of the Auditor-Controller
          <br />
          4080 Lemon Street, 6th Floor · P.O. Box 1326 · Riverside, CA 92502-1326
        </footer>
      </div>
    </div>
  );
}
