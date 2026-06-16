"use client";

import { useEffect, useRef, useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";
import { getToken, storeToken } from "@/lib/types";
import type {
  ClaimantSubmission,
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

/**
 * One refund/claim from the bot handoff. Multiple stale-warrant or payroll
 * refunds for the same person each become an entry — the form renders a
 * warrant block per claim and the admin viewer renders a filled AP-13 per
 * claim. Property-tax claims share the array but have their own field set.
 */
interface Claim {
  type: string;
  // STALE_WARRANT / PAYROLL fields
  warrant_number?: string;
  warrant_amount?: string;
  warrant_date?: string;
  business_unit?: string;
  business_name?: string;
  is_owner?: string;
  warrant_included?: string;
  // PROPERTY_TAX fields
  assessment_number?: string;
  tax_year?: string;
  refund_amount?: string;
}

const AP13_TYPES = new Set(["STALE_WARRANT", "PAYROLL"]);

function isAp13(type: string): boolean {
  return AP13_TYPES.has(type);
}

interface RequiredDoc {
  id: string;
  label: string;
  required: boolean;
}

interface DocRequirementsResponse {
  refund_types: string[];
  docs: RequiredDoc[];
  either_of: string[][];
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
  const [docReqs, setDocReqs] = useState<RequiredDoc[]>([]);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  // Per-claim fields. AP-13 (STALE_WARRANT, PAYROLL) types each get one
  // entry per warrant; PROPERTY_TAX gets one entry. Fields like name /
  // address / email / phone live in formValues since they're shared.
  const [claims, setClaims] = useState<Claim[]>([]);
  const [sigDataUrl, setSigDataUrl] = useState<string | null>(null);
  // Per-required-doc file (single each).
  const [reqFiles, setReqFiles] = useState<Record<string, File | null>>({});
  // Optional scanned-form fallback (single file).
  const [scannedForm, setScannedForm] = useState<File | null>(null);
  // Optional "other" attachments (multi-file).
  const [otherFiles, setOtherFiles] = useState<File[]>([]);
  // Files already uploaded to S3 via save-draft. Keyed by safeName, value is
  // the original filename to display next to the doc box. The user can pick
  // a fresh file to replace one of these (saving uploads the new one and the
  // backend drops the old one with the same doc-id prefix).
  const [savedDocs, setSavedDocs] = useState<Record<string, string>>({});
  const [submissionId, setSubmissionId] = useState("");
  const [statusMsg, setStatusMsg] = useState("");
  // "info" = blue (in-progress), "success" = green (saved/ok),
  // "error" = red (validation / failures). Defaults to error for back-compat.
  const [statusKind, setStatusKind] = useState<"info" | "success" | "error">("error");
  const [errorMsg, setErrorMsg] = useState("");

  // Reserve-on-load state (bot handoff)
  const [reservedId, setReservedId] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const resumeId = params.get("submissionId") ?? "";
    const name = params.get("name") ?? "";
    const type = params.get("type") ?? "";
    const address = params.get("address") ?? "";

    setUrlName(name);
    setUrlType(type);
    setUrlAddress(address);

    if (resumeId) {
      // Resume path: claimant came from /claim's "Continue your claim" button.
      // Token must already be in sessionStorage from a prior verify; if not,
      // bounce them to /my-claim to verify first.
      (async () => {
        const token = getToken(resumeId);
        if (!token) {
          window.location.href = "/my-claim";
          return;
        }
        try {
          setPhase("loading");
          const status = await apiFetch<ClaimantSubmission>(
            `/claimant/status?id=${encodeURIComponent(resumeId)}`,
            { token },
          );
          setReservedId(resumeId);
          setUrlName(status.name);
          setUrlType(status.refundType);
          // Hydrate the form with whatever they typed before. We don't get
          // back the real address from the server (privacy) — but it's in
          // draftFormData if they typed it.
          const draft = status.draftFormData ?? {};
          const { claims: savedClaims, ...savedFields } = draft as Record<string, unknown>;
          setFormValues({
            name: status.name,
            ...(savedFields as Record<string, string>),
          });
          await loadSchemas(status.refundType.split(",").filter(Boolean));
          // Override the URL-derived claims with the saved per-claim entries
          // if the user got that far before saving.
          if (Array.isArray(savedClaims) && savedClaims.length > 0) {
            setClaims(savedClaims as Claim[]);
          }
          // Surface previously-uploaded files so the user sees them attached
          // and only has to pick replacements for what they want to change.
          const docs = status.documents ?? [];
          const origs = status.originalNames ?? {};
          const savedMap: Record<string, string> = {};
          for (const fn of docs) {
            if (fn === "unified-form.json") continue;
            savedMap[fn] = origs[fn] || fn;
          }
          setSavedDocs(savedMap);
          setPhase("form");
        } catch (e) {
          setErrorMsg(e instanceof Error ? e.message : String(e));
          setPhase("error");
        }
      })();
      return;
    }

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
          if (res.token) storeToken(res.submissionId, res.token);
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
    const [data, reqs] = await Promise.all([
      apiFetch<SchemasResponse>(
        `/form-schemas?types=${encodeURIComponent(types.join(","))}`,
      ),
      apiFetch<DocRequirementsResponse>(
        `/doc-requirements?types=${encodeURIComponent(types.join(","))}`,
      ).catch(() => ({ refund_types: types, docs: [] as RequiredDoc[], either_of: [] as string[][] })),
    ]);
    setSchemas(data);
    setDocReqs(reqs.docs || []);
    setReqFiles({});
    // Build a per-claim array from the bot-handoff URL — one entry per
    // refund. Multiple stale warrants for the same person turn into N
    // separate AP-13 entries; PROPERTY_TAX gets one entry that holds
    // assessment/tax-year/refund-amount.
    const params = new URLSearchParams(window.location.search);
    const amounts = (params.get("amount") ?? "").split(",");
    const ids = (params.get("id") ?? "").split(",");
    const assessment = params.get("assessment") ?? "";
    const taxyear = params.get("taxyear") ?? "";
    const built: Claim[] = types.map((rt, i) => {
      if (rt === "PROPERTY_TAX") {
        return {
          type: rt,
          assessment_number: assessment,
          tax_year: taxyear,
          refund_amount: amounts[i] || "",
        };
      }
      return {
        type: rt,
        warrant_number: ids[i] || "",
        warrant_amount: amounts[i] || "",
      };
    });
    setClaims(built);
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
      if (res.token) storeToken(res.submissionId, res.token);
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

  // Stash whatever the user has typed so they can resume later. Reuses the
  // claimant token from sessionStorage; the token is set by the bot-handoff
  // /reserve call (returned via setReservedId) or by a /my-claim verify.
  //
  // Also uploads any newly-picked files immediately so they survive a tab
  // close. Files already on S3 (savedDocs) are skipped unless the user picked
  // a replacement.
  async function saveDraft(opts: { silent?: boolean } = {}) {
    if (!reservedId) return; // No id yet -> can't save (e.g. mini-form path).
    const token = getToken(reservedId);
    if (!token) {
      if (!opts.silent) {
        setStatusKind("error");
        setStatusMsg("Your session expired. Please verify again at /my-claim before saving.");
      }
      return;
    }
    try {
      // Collect any newly-picked files that need uploading. Required-doc
      // slots, scanned-form, and "other" attachments use the same naming
      // scheme as handleSubmit so the backend `_doc_prefix()` semantics line up.
      const ext = (f: File) => (f.name.split(".").pop() ?? "bin").toLowerCase();
      const toUpload: { docId: string; file: File; safeName: string }[] = [];
      Object.entries(reqFiles).forEach(([docId, f]) => {
        if (f) toUpload.push({ docId, file: f, safeName: `${docId}.${ext(f)}` });
      });
      if (scannedForm) {
        toUpload.push({
          docId: "scanned-form",
          file: scannedForm,
          safeName: `scanned-form.${ext(scannedForm)}`,
        });
      }
      otherFiles.forEach((f, i) => {
        // Stamp other-N files with a per-save timestamp so multiple save
        // rounds don't collide on the same key.
        const ts = new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 14);
        const base = otherFiles.length > 1 ? `other-${ts}-${i + 1}` : `other-${ts}`;
        toUpload.push({ docId: base, file: f, safeName: `${base}.${ext(f)}` });
      });

      // Push files to S3 if there are any.
      const newOriginals: Record<string, string> = {};
      let uploadedNames: string[] = [];
      if (toUpload.length > 0) {
        const continueRes = await apiFetch<{ uploads: UploadSlot[] }>("/claimant/continue", {
          method: "POST",
          token,
          body: JSON.stringify({
            submissionId: reservedId,
            files: toUpload.map((d) => ({
              filename: d.safeName,
              contentType: d.file.type || "application/octet-stream",
              originalFilename: d.file.name,
            })),
          }),
        });
        for (const d of toUpload) {
          const slot = continueRes.uploads.find((u) => u.filename === d.safeName);
          if (!slot) continue;
          const r = await fetch(slot.uploadUrl, {
            method: "PUT",
            headers: { "Content-Type": d.file.type || "application/octet-stream" },
            body: d.file,
          });
          if (!r.ok) throw new Error(`S3 PUT failed (${r.status}) for ${d.file.name}`);
          newOriginals[d.safeName] = d.file.name;
        }
        uploadedNames = toUpload.map((d) => d.safeName);
      }

      // Persist form fields + merge filenames into documents server-side.
      const saveResp = await apiFetch<{ documents?: string[] }>("/claimant/save-draft", {
        method: "POST",
        token,
        body: JSON.stringify({
          submissionId: reservedId,
          formData: { ...formValues, claims },
          filenames: uploadedNames,
          originalNames: newOriginals,
        }),
      });

      // Roll uploaded files into savedDocs and clear the picked-file state so
      // the UI shows them as attached.
      if (uploadedNames.length > 0) {
        setSavedDocs((prev) => {
          const next = { ...prev };
          // Drop older docs with the same doc-id prefix so the user sees the
          // replacement, not both.
          const newPrefixes = new Set(uploadedNames.map((n) => n.split(".")[0].replace(/-\d+$/, "")));
          for (const k of Object.keys(next)) {
            const prefix = k.split(".")[0].replace(/-\d+$/, "");
            if (newPrefixes.has(prefix) && !uploadedNames.includes(k)) delete next[k];
          }
          for (const fn of uploadedNames) {
            next[fn] = newOriginals[fn] || fn;
          }
          return next;
        });
        // Clear the in-memory pickers — the files are saved now.
        setReqFiles({});
        setScannedForm(null);
        setOtherFiles([]);
      } else if (saveResp.documents) {
        // Server may have echoed back the canonical doc list; trust it.
        const known = new Set(saveResp.documents);
        setSavedDocs((prev) => {
          const next: Record<string, string> = {};
          for (const k of Object.keys(prev)) {
            if (known.has(k)) next[k] = prev[k];
          }
          return next;
        });
      }

      if (!opts.silent) {
        setStatusKind("success");
        const fileNote =
          uploadedNames.length > 0
            ? ` (${uploadedNames.length} file${uploadedNames.length === 1 ? "" : "s"} attached)`
            : "";
        setStatusMsg(
          `Saved${fileNote}. Resume any time at /my-claim with this Claim ID: ${reservedId}`,
        );
      }
    } catch (e) {
      if (!opts.silent) {
        setStatusKind("error");
        setStatusMsg(
          e instanceof ApiError
            ? `Could not save draft (${e.status}): ${e.message}`
            : `Could not save draft: ${e instanceof Error ? e.message : String(e)}`,
        );
      }
    }
  }

  // Best-effort save on tab close so the user doesn't lose progress.
  // Uses sendBeacon when available to survive the unload.
  useEffect(() => {
    if (!reservedId) return;
    const handler = () => {
      const token = getToken(reservedId);
      if (!token) return;
      const url =
        (window.__CLAIMANT_CONFIG__?.API_URL?.replace(/\/$/, "") ?? "") +
        "/claimant/save-draft";
      const body = JSON.stringify({
        submissionId: reservedId,
        formData: { ...formValues, claims },
      });
      // sendBeacon doesn't let us set custom headers, so fall back to a
      // best-effort fetch with keepalive when we need the auth header.
      try {
        fetch(url, {
          method: "POST",
          keepalive: true,
          headers: { "Content-Type": "application/json", "X-Claimant-Token": token },
          body,
        });
      } catch {
        // ignore — best-effort
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [reservedId, formValues, claims]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const usingScannedForm = !!scannedForm;
    if (!sigDataUrl && !usingScannedForm) {
      setStatusKind("error");
      setStatusMsg("Please sign the form (or upload a scanned hand-filled form) before submitting.");
      return;
    }
    if (!schemas) return;

    // Validate required fields. Common fields read from formValues; per-claim
    // fields read from the matching claims[i] entry — required checks are
    // applied per claim so 3 stale-warrant claims need 3 warrant numbers.
    const missing: string[] = [];
    for (const f of schemas.merged_fields) {
      const section = f.section || "common";
      const isCommon = section === "common";
      if (f.type === "checkbox") {
        if (!f.required) continue;
        if (isCommon) {
          if (!(formValues[f.id] === "true" || formValues[f.id] === "false")) missing.push(f.label);
        } else {
          claims.forEach((c, i) => {
            if (c.type !== section) return;
            const v = (c as unknown as Record<string, unknown>)[f.id];
            if (!(v === "true" || v === "false")) missing.push(`${f.label} (Claim ${i + 1})`);
          });
        }
        continue;
      }
      if (!f.required) continue;
      if (isCommon) {
        if (!(formValues[f.id] ?? "").trim()) missing.push(f.label);
      } else {
        claims.forEach((c, i) => {
          if (c.type !== section) return;
          const v = String((c as unknown as Record<string, unknown>)[f.id] ?? "").trim();
          if (!v) missing.push(`${f.label} (Claim ${i + 1})`);
        });
      }
    }
    if (missing.length > 0) {
      setStatusKind("error");
      setStatusMsg(`Please fill required fields: ${missing.join(", ")}`);
      return;
    }

    // Validate required documents
    // Required-doc check: a freshly-picked file or a previously-saved file
    // (from a draft-save round) both count.
    const savedSlots = new Set(
      Object.keys(savedDocs).map((k) => k.split(".")[0]),
    );
    const missingDocs = docReqs
      .filter((d) => d.required && !reqFiles[d.id] && !savedSlots.has(d.id))
      .map((d) => d.label);
    if (missingDocs.length > 0) {
      setStatusKind("error");
      setStatusMsg(`Please upload required documents: ${missingDocs.join(", ")}`);
      return;
    }

    setPhase("submitting");
    setStatusMsg("Submitting your claim…");

    try {
      const types = (urlType || miniType.join(",")).split(",").filter(Boolean);
      const name = formValues["name"] || urlName || miniName;
      const refundType = types.join(",");
      const address = formValues["address"] || urlAddress || miniAddress;

      // Build unified form JSON blob.
      //
      // `formData` keeps the shared fields (name/address/email/phone) so older
      // tooling that reads it still works.
      // `claims` is the new array — one entry per refund. The admin viewer
      // iterates this to render one filled AP-13 PDF per claim.
      const unifiedPayload = {
        formData: formValues,
        claims,
        refundTypes: types,
        signature: sigDataUrl,
        submittedAt: new Date().toISOString(),
      };
      const unifiedBlob = new Blob([JSON.stringify(unifiedPayload)], {
        type: "application/json",
      });

      // Build file list. The backend's _doc_prefix() maps "<docId>.<ext>" back
      // to its requirement; multi-file inputs need per-file suffixes so two
      // files don't collide on the same S3 key.
      const ext = (f: File) => (f.name.split(".").pop() ?? "bin").toLowerCase();
      const docFiles: { docId: string; file: File; safeName: string }[] = [];

      Object.entries(reqFiles).forEach(([docId, f]) => {
        if (f) docFiles.push({ docId, file: f, safeName: `${docId}.${ext(f)}` });
      });
      if (scannedForm) {
        docFiles.push({
          docId: "scanned-form",
          file: scannedForm,
          safeName: `scanned-form.${ext(scannedForm)}`,
        });
      }
      otherFiles.forEach((f, i) => {
        const base = otherFiles.length > 1 ? `other-${i + 1}` : "other";
        docFiles.push({ docId: base, file: f, safeName: `${base}.${ext(f)}` });
      });

      const fileList: {
        filename: string;
        contentType: string;
        originalFilename?: string;
      }[] = [
        { filename: "unified-form.json", contentType: "application/json" },
        ...docFiles.map((d) => ({
          filename: d.safeName,
          contentType: d.file.type || "application/octet-stream",
          // The backend stamps this on the manifest so the dashboard /
          // /claim status page can display "DMV License.pdf" instead of
          // the doc-id-derived safe filename.
          originalFilename: d.file.name,
        })),
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
      for (const d of docFiles) {
        const slot = uploadSlots.find((u) => u.filename === d.safeName);
        if (slot) {
          await fetch(slot.uploadUrl, {
            method: "PUT",
            headers: { "Content-Type": d.file.type || "application/octet-stream" },
            body: d.file,
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
      setStatusKind("error");
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
                const { merged_fields, schemas: typeSchemas } = schemas;
                const bySection: Record<string, FormField[]> = {};
                for (const f of merged_fields) {
                  const s = f.section || "common";
                  (bySection[s] = bySection[s] || []).push(f);
                }

                // Render shared (common) fields once, then iterate `claims`.
                // AP-13 refund types (STALE_WARRANT, PAYROLL) get one section
                // per claim instance. PROPERTY_TAX gets one (single-claim).
                const ap13Counts: Record<string, number> = {};
                for (const c of claims) if (isAp13(c.type)) ap13Counts[c.type] = (ap13Counts[c.type] ?? 0) + 1;
                const ap13SeenIdx: Record<string, number> = {};

                const renderSectionFields = (
                  sectionFields: FormField[],
                  // For AP-13 sections, the per-claim getter/setter; otherwise
                  // null which means use formValues directly.
                  perClaim: { idx: number; claim: Claim } | null,
                ) => (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                    {sectionFields.map((f) => {
                      const isWide =
                        f.type === "address" || f.type === "textarea" || f.type === "checkbox";

                      // Resolve current value + writer for this field.
                      const fieldKey = perClaim ? `${f.id}_${perClaim.idx}` : f.id;
                      const value = perClaim
                        ? String((perClaim.claim as unknown as Record<string, unknown>)[f.id] ?? "")
                        : (formValues[f.id] ?? "");
                      const onChange = (v: string) => {
                        if (perClaim) {
                          setClaims((prev) =>
                            prev.map((c, i) =>
                              i === perClaim.idx ? { ...c, [f.id]: v } : c,
                            ),
                          );
                        } else {
                          handleFieldChange(f.id, v);
                        }
                      };

                      if (f.type === "checkbox") {
                        return (
                          <div
                            key={fieldKey}
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
                                    name={fieldKey}
                                    value={val}
                                    checked={value === val}
                                    onChange={(e) => onChange(e.target.value)}
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
                            id={`f_${fieldKey}`}
                            required={f.required}
                            rows={2}
                            value={value}
                            onChange={(e) => onChange(e.target.value)}
                            className="w-full border-b bg-transparent outline-none py-2 text-sm resize-none"
                            style={{ borderColor: "var(--border)" }}
                          />
                        ) : (
                          <input
                            id={`f_${fieldKey}`}
                            type={inputType[f.type] ?? "text"}
                            required={f.required}
                            value={value}
                            onChange={(e) => onChange(e.target.value)}
                            className="w-full border-b bg-transparent outline-none py-2 text-sm"
                            style={{ borderColor: "var(--border)" }}
                          />
                        );

                      return (
                        <div
                          key={fieldKey}
                          className={isWide ? "col-span-full" : ""}
                        >
                          {input}
                          <label
                            htmlFor={`f_${fieldKey}`}
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
                );

                const sectionHeader = (title: string, key: string) => (
                  <div
                    key={`hdr-${key}`}
                    className="text-center text-xs font-bold uppercase tracking-widest py-2 mb-4 border-t border-b"
                    style={{
                      fontFamily: "Montserrat, sans-serif",
                      borderColor: "var(--border)",
                    }}
                  >
                    {title}
                  </div>
                );

                const blocks: React.ReactNode[] = [];

                // Common (shared) fields
                if (bySection.common && bySection.common.length) {
                  blocks.push(
                    <div key="common">
                      {sectionHeader("Claimant Information", "common")}
                      {renderSectionFields(bySection.common, null)}
                    </div>,
                  );
                }

                // Per-claim sections, in URL/handoff order.
                claims.forEach((claim, idx) => {
                  const fields = bySection[claim.type];
                  if (!fields || fields.length === 0) return;
                  const sectionTitle = typeSchemas[claim.type]?.title ?? claim.type;
                  let title = sectionTitle;
                  if (isAp13(claim.type) && (ap13Counts[claim.type] ?? 0) > 1) {
                    const seen = (ap13SeenIdx[claim.type] = (ap13SeenIdx[claim.type] ?? 0) + 1);
                    title = `${sectionTitle} — Claim ${seen} of ${ap13Counts[claim.type]}`;
                  }
                  blocks.push(
                    <div key={`claim-${idx}`}>
                      {sectionHeader(title, `claim-${idx}`)}
                      {renderSectionFields(fields, { idx, claim })}
                    </div>,
                  );
                });

                return blocks;
              })()}

              {/* Supporting Documents */}
              <div className="mb-6">
                <div
                  className="text-center text-xs font-bold uppercase tracking-widest py-2 mb-3 border-t border-b"
                  style={{ fontFamily: "Montserrat, sans-serif", borderColor: "var(--border)" }}
                >
                  Supporting Documents
                </div>

                {/* Required docs list — one labeled file box per requirement */}
                {docReqs.map((doc) => {
                  // A saved doc for this slot has a safeName starting with
                  // the doc-id (e.g. "government-id.pdf" → "government-id").
                  const savedKey = Object.keys(savedDocs).find(
                    (k) => k.split(".")[0] === doc.id,
                  );
                  const savedLabel = savedKey ? savedDocs[savedKey] : null;
                  const picked = reqFiles[doc.id];
                  return (
                    <div
                      key={doc.id}
                      className="border px-4 py-3 mb-3 flex flex-wrap items-center gap-3"
                      style={{ borderColor: "var(--border-light)" }}
                    >
                      <div className="flex-1 min-w-[16rem] text-sm font-semibold">
                        {doc.label}
                        {doc.required && (
                          <span
                            className="ml-2 text-xs font-bold"
                            style={{ color: "var(--red)" }}
                          >
                            Required
                          </span>
                        )}
                      </div>
                      <input
                        type="file"
                        accept=".pdf,.jpg,.jpeg,.png,.heic"
                        onChange={(e) =>
                          setReqFiles((prev) => ({
                            ...prev,
                            [doc.id]: e.target.files?.[0] ?? null,
                          }))
                        }
                        className="text-sm"
                      />
                      {picked ? (
                        <span className="text-xs" style={{ color: "var(--green, #2e7d32)" }}>
                          ✓ {picked.name} (will replace on save)
                        </span>
                      ) : savedLabel ? (
                        <span className="text-xs" style={{ color: "var(--green, #2e7d32)" }}>
                          ✓ {savedLabel} (saved — pick a new file to replace)
                        </span>
                      ) : null}
                    </div>
                  );
                })}

                {/* Optional: scanned hand-filled paper form */}
                {(() => {
                  const savedKey = Object.keys(savedDocs).find(
                    (k) => k.split(".")[0] === "scanned-form",
                  );
                  const savedLabel = savedKey ? savedDocs[savedKey] : null;
                  return (
                    <div
                      className="border-2 border-dashed px-4 py-3 mb-3"
                      style={{ borderColor: "var(--border-light)" }}
                    >
                      <div className="text-xs font-bold uppercase tracking-wider mb-1" style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}>
                        Optional: Hand-filled paper form
                      </div>
                      <p className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>
                        If you printed and filled this form by hand, upload a scan or photograph
                        here instead of signing digitally below.
                      </p>
                      <input
                        type="file"
                        accept=".pdf,.jpg,.jpeg,.png,.heic"
                        onChange={(e) => setScannedForm(e.target.files?.[0] ?? null)}
                        className="text-sm"
                      />
                      {scannedForm ? (
                        <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                          ✓ {scannedForm.name} (will replace on save)
                        </div>
                      ) : savedLabel ? (
                        <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                          ✓ {savedLabel} (saved — pick a new file to replace)
                        </div>
                      ) : null}
                    </div>
                  );
                })()}

                {/* Optional: other supporting documents */}
                <div
                  className="border-2 border-dashed px-4 py-3"
                  style={{ borderColor: "var(--border-light)" }}
                >
                  <div className="text-xs font-bold uppercase tracking-wider mb-1" style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}>
                    Optional: Other supporting documents
                  </div>
                  <p className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>
                    Attach any additional evidence you'd like staff to review (correspondence,
                    payment records, etc.). You can select multiple files at once.
                  </p>
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.jpg,.jpeg,.png,.heic"
                    onChange={(e) => setOtherFiles(Array.from(e.target.files ?? []))}
                    className="text-sm"
                  />
                  {/* Previously-saved 'other' attachments */}
                  {(() => {
                    const otherSaved = Object.entries(savedDocs).filter(
                      ([k]) => k.startsWith("other-") || k.startsWith("other."),
                    );
                    if (!otherSaved.length) return null;
                    return (
                      <ul className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                        {otherSaved.map(([key, label]) => (
                          <li key={key}>✓ {label} (saved)</li>
                        ))}
                      </ul>
                    );
                  })()}
                  {otherFiles.length > 0 && (
                    <ul className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                      {otherFiles.map((f, i) => (
                        <li key={i}>✓ {f.name}</li>
                      ))}
                    </ul>
                  )}
                </div>
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
              {statusMsg && (() => {
                // Submitting always wins as info-blue; otherwise the kind set
                // by whoever wrote the message (success-green / error-red).
                const kind = phase === "submitting" ? "info" : statusKind;
                const palette = {
                  info: { bg: "#e3f2fd", border: "#0c71ca", fg: "#1565c0" },
                  success: { bg: "#e8f5e9", border: "var(--green)", fg: "#1b5e20" },
                  error: { bg: "#fce4ec", border: "var(--red)", fg: "#c62828" },
                }[kind];
                return (
                  <div
                    className="px-4 py-3 text-sm border-l-4 mb-4"
                    style={{ background: palette.bg, borderColor: palette.border, color: palette.fg }}
                  >
                    {statusMsg}
                  </div>
                );
              })()}

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

              {reservedId && (
                <button
                  type="button"
                  onClick={() => saveDraft()}
                  disabled={phase === "submitting"}
                  className="w-full mt-2 py-2 text-xs font-bold uppercase tracking-widest disabled:opacity-50"
                  style={{
                    fontFamily: "Montserrat, sans-serif",
                    background: "transparent",
                    color: "var(--navy)",
                    border: "1px solid var(--navy)",
                    cursor: phase === "submitting" ? "not-allowed" : "pointer",
                  }}
                >
                  Save and continue later
                </button>
              )}
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
