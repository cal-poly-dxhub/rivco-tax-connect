"use client";

// Claim status page — reads the submission ID from the URL query string
// (?id=<submissionId>) rather than a dynamic route segment, so this page
// can be statically exported without generateStaticParams.
//
// URL pattern:  /claim/?id=abc123def456
// The `my-claim` page redirects here after successful address quiz verification.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { getToken, clearToken, statusLabel, statusColor } from "@/lib/types";
import type { ClaimantSubmission, ClaimStatus } from "@/lib/types";

export default function ClaimStatusPage() {
  const router = useRouter();
  const [submissionId, setSubmissionId] = useState<string | null>(null);
  const [submission, setSubmission] = useState<ClaimantSubmission | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id") ?? "";
    setSubmissionId(id);

    if (!id) {
      router.replace("/my-claim");
      return;
    }
    const token = getToken(id);
    if (!token) {
      router.replace("/my-claim");
      return;
    }
    (async () => {
      try {
        const data = await apiFetch<ClaimantSubmission>(
          `/claimant/status?id=${encodeURIComponent(id)}`,
          { token },
        );
        setSubmission(data);
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          clearToken();
          router.replace("/my-claim");
        } else {
          setError(e instanceof Error ? e.message : "Failed to load claim.");
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  function handleUploadMore() {
    if (submissionId) {
      router.push(`/claim/upload?id=${encodeURIComponent(submissionId)}`);
    }
  }

  if (loading) {
    return (
      <PageShell>
        <div
          className="px-4 py-3 text-sm border-l-4 m-8"
          style={{ background: "#e3f2fd", borderColor: "#0c71ca", color: "#1565c0" }}
        >
          Loading claim status…
        </div>
      </PageShell>
    );
  }

  if (error) {
    return (
      <PageShell>
        <div className="px-8 py-8">
          <div
            className="px-4 py-3 text-sm border-l-4 mb-4"
            style={{ background: "#fce4ec", borderColor: "var(--red)", color: "#c62828" }}
          >
            {error}
          </div>
          <a href="/my-claim" className="text-sm" style={{ color: "var(--navy)" }}>
            ← Try again
          </a>
        </div>
      </PageShell>
    );
  }

  if (!submission) return null;

  const overallStatus = submission.overallStatus as ClaimStatus;
  const canUploadMore =
    overallStatus === "partial" || overallStatus === "uploaded";
  const visibleDocs = (submission.documents ?? []).filter(
    (d) => !d.startsWith("_"),
  );
  const refundTypes = submission.refundType
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

  const typeLabels: Record<string, string> = {
    STALE_WARRANT: "Stale-Dated Warrant",
    PAYROLL: "Payroll Warrant",
    PROPERTY_TAX: "Property Tax",
  };

  return (
    <PageShell>
      <div className="px-8 pb-8">
        {/* Claim ID + status header */}
        <div className="mb-6">
          <div className="flex items-start justify-between flex-wrap gap-2 mb-3">
            <div>
              <span
                className="block text-xs uppercase tracking-widest"
                style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
              >
                Claim ID
              </span>
              <span
                className="text-xl font-bold tracking-wider"
                style={{ fontFamily: "Montserrat, sans-serif", color: "var(--navy)" }}
              >
                {submission.submissionId}
              </span>
            </div>
            <div
              className={`px-3 py-2 text-xs font-bold uppercase tracking-widest border ${statusColor(overallStatus)}`}
              style={{ fontFamily: "Montserrat, sans-serif" }}
            >
              {statusLabel(overallStatus)}
            </div>
          </div>
        </div>

        {/* Details */}
        <div
          className="border mb-4 divide-y text-sm"
          style={{ borderColor: "var(--border-light)" }}
        >
          <Row label="Name" value={submission.name} />
          <Row
            label="Claim Types"
            value={refundTypes.map((t) => typeLabels[t] ?? t).join(", ")}
          />
          <Row
            label="Submitted"
            value={
              submission.submittedAt
                ? new Date(submission.submittedAt).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })
                : "—"
            }
          />
          <Row
            label="Last Updated"
            value={
              submission.updatedAt
                ? new Date(submission.updatedAt).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })
                : "—"
            }
          />
        </div>

        {/* Documents */}
        {visibleDocs.length > 0 && (
          <div className="mb-4">
            <div
              className="text-xs uppercase tracking-widest font-bold mb-2"
              style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
            >
              Documents on File
            </div>
            <ul className="text-sm space-y-1">
              {visibleDocs.map((doc) => (
                <li key={doc} className="flex items-center gap-2">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    className="w-4 h-4 flex-shrink-0"
                    style={{ color: "var(--navy)" }}
                    aria-hidden="true"
                  >
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  <span style={{ color: "var(--text)" }}>
                    {friendlyDocLabel(doc, submission.originalNames)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Upload more button */}
        {canUploadMore && (
          <div className="mb-4">
            <button
              type="button"
              onClick={handleUploadMore}
              className="w-full py-3 font-bold uppercase tracking-wide text-sm"
              style={{
                fontFamily: "Montserrat, sans-serif",
                background: "var(--navy)",
                color: "#fff",
                border: "none",
                cursor: "pointer",
              }}
            >
              Upload More Documents
            </button>
          </div>
        )}

        {/* Processing note */}
        {overallStatus !== "approved" && overallStatus !== "denied" && (
          <div
            className="px-4 py-3 text-xs border-l-4"
            style={{
              background: "#fff8e1",
              borderColor: "#f9a825",
              color: "#7a5800",
            }}
          >
            Please allow up to 90 days for processing. You can return to this
            page at any time using your Claim ID and mailing address.
          </div>
        )}

        <div
          className="mt-6 pt-4 border-t"
          style={{ borderColor: "var(--border-light)" }}
        >
          <button
            type="button"
            onClick={() => {
              clearToken();
              router.push("/my-claim");
            }}
            className="text-xs"
            style={{
              color: "var(--navy)",
              background: "none",
              border: "none",
              cursor: "pointer",
            }}
          >
            ← Look up a different claim
          </button>
        </div>
      </div>
    </PageShell>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex px-4 py-3 gap-4">
      <span
        className="w-28 flex-shrink-0 text-xs uppercase tracking-widest"
        style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
      >
        {label}
      </span>
      <span style={{ color: "var(--text)" }}>{value}</span>
    </div>
  );
}

function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="min-h-screen flex flex-col items-center justify-start px-4 py-16"
      style={{ background: "var(--bg)" }}
    >
      <div
        className="w-full max-w-lg"
        style={{
          background: "var(--surface)",
          border: "1px solid #999",
          boxShadow: "0 2px 12px rgba(0,0,0,0.12)",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-4 px-6 py-4 mb-6"
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

        {children}

        <footer
          className="border-t pt-4 pb-6 text-center text-xs"
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

// Map our doc-id-derived safe filenames to user-friendly labels. Used as a
// fallback when the upload manifest has no original filename for a given doc
// (e.g. older claims).
const DOC_LABELS: Record<string, string> = {
  "unified-form": "Submitted claim form",
  "government-id": "Government photo ID",
  "proof-of-entitlement": "Proof of entitlement",
  "proof-of-ownership": "Proof of property ownership",
  "scanned-form": "Scanned paper form",
  "ap13-affidavit": "Signed AP-13 affidavit",
  "property-tax-claim": "Signed property tax claim",
};

function friendlyDocLabel(
  filename: string,
  originalNames?: Record<string, string>,
): string {
  if (filename === "unified-form.json") return "Submitted claim form";
  const original = originalNames?.[filename];
  if (original) return original;
  // Fall back to the doc-id prefix (`government-id.pdf` -> `government-id`)
  // and look it up in our label map.
  const stem = filename.split(".")[0].replace(/-\d+$/, "");
  return DOC_LABELS[stem] || filename;
}
