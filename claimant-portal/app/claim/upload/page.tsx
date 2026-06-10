"use client";

// Upload-more-documents page — claimant adds files to an existing submission
// without rebuilding the whole form. URL pattern: /claim/upload/?id=<submissionId>
//
// Requires the claimant token already in sessionStorage (set when the user
// verified their identity on /my-claim). Without it we bounce back to verify.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiFetch, ApiError } from "@/lib/api";
import { getToken, clearToken } from "@/lib/types";
import type { ContinueResponse, UploadSlot } from "@/lib/types";

export default function ClaimUploadPage() {
  const router = useRouter();
  const [submissionId, setSubmissionId] = useState<string>("");
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<"ready" | "uploading" | "done" | "error">("ready");
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("id") ?? "";
    if (!id) {
      router.replace("/my-claim");
      return;
    }
    const token = getToken(id);
    if (!token) {
      router.replace("/my-claim");
      return;
    }
    setSubmissionId(id);
  }, [router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (files.length === 0) {
      setErrorMsg("Please pick at least one file.");
      return;
    }
    if (files.length > 10) {
      setErrorMsg("You can upload at most 10 files at a time.");
      return;
    }
    const token = getToken(submissionId);
    if (!token) {
      router.replace("/my-claim");
      return;
    }
    setErrorMsg("");
    setPhase("uploading");

    try {
      // Per-file safe filenames so multiple files in one batch can't collide
      // on the same S3 key. Existing files on the claim are preserved by the
      // backend regardless.
      const ext = (f: File) => (f.name.split(".").pop() ?? "bin").toLowerCase();
      const ts = new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 14);
      const named = files.map((f, i) => ({
        file: f,
        safeName: `other-${ts}-${i + 1}.${ext(f)}`,
      }));

      const fileList = named.map((d) => ({
        filename: d.safeName,
        contentType: d.file.type || "application/octet-stream",
      }));

      const cont = await apiFetch<ContinueResponse>("/claimant/continue", {
        method: "POST",
        token,
        body: JSON.stringify({ submissionId, files: fileList }),
      });

      // PUT to each presigned URL.
      for (const d of named) {
        const slot = cont.uploads.find((u: UploadSlot) => u.filename === d.safeName);
        if (!slot) continue;
        const res = await fetch(slot.uploadUrl, {
          method: "PUT",
          headers: { "Content-Type": d.file.type || "application/octet-stream" },
          body: d.file,
        });
        if (!res.ok) {
          throw new Error(`S3 PUT failed (${res.status}) for ${d.file.name}`);
        }
      }

      // Notify backend to merge filenames + recompute statuses.
      await apiFetch("/claimant/continue-complete", {
        method: "POST",
        token,
        body: JSON.stringify({
          submissionId,
          filenames: named.map((d) => d.safeName),
        }),
      });

      setPhase("done");
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
        clearToken();
        router.replace("/my-claim");
        return;
      }
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }

  if (!submissionId) {
    return (
      <PageShell>
        <div
          className="px-4 py-3 text-sm border-l-4 m-8"
          style={{ background: "#e3f2fd", borderColor: "#0c71ca", color: "#1565c0" }}
        >
          Loading…
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell>
      <div className="px-8 pb-8">
        <div className="mb-4">
          <Link
            href={`/claim/?id=${encodeURIComponent(submissionId)}`}
            className="text-xs"
            style={{ color: "var(--navy)" }}
          >
            ← Back to claim status
          </Link>
        </div>

        <div
          className="text-center text-xs font-bold uppercase tracking-widest py-2 mb-4 border-t border-b"
          style={{ fontFamily: "Montserrat, sans-serif", borderColor: "var(--border)" }}
        >
          Add Supporting Documents
        </div>

        <p className="text-sm mb-2" style={{ color: "var(--text-muted)" }}>
          Adding documents to claim:{" "}
          <span className="font-mono" style={{ color: "var(--text)" }}>
            {submissionId}
          </span>
        </p>
        <p className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
          New files will be appended to your claim — your existing documents won't be replaced.
          You can attach up to 10 files at a time.
        </p>

        {phase === "done" ? (
          <>
            <div
              className="px-4 py-3 text-sm border-l-4 mb-4"
              style={{ background: "#e8f5e9", borderColor: "var(--green)", color: "#1b5e20" }}
            >
              Your additional documents were uploaded and attached to your claim.
            </div>
            <div className="flex flex-col gap-2">
              <Link
                href={`/claim/?id=${encodeURIComponent(submissionId)}`}
                className="block w-full text-center py-3 font-bold uppercase tracking-wide text-sm"
                style={{
                  fontFamily: "Montserrat, sans-serif",
                  background: "var(--navy)",
                  color: "#fff",
                  border: "none",
                }}
              >
                View Claim Status
              </Link>
              <button
                type="button"
                onClick={() => {
                  setFiles([]);
                  setPhase("ready");
                }}
                className="w-full py-3 font-bold uppercase tracking-wide text-xs"
                style={{
                  fontFamily: "Montserrat, sans-serif",
                  background: "transparent",
                  color: "var(--navy)",
                  border: "1px solid var(--navy)",
                }}
              >
                Upload More
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <input
              type="file"
              multiple
              accept=".pdf,.jpg,.jpeg,.png,.heic"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              className="text-sm w-full mb-3"
              disabled={phase === "uploading"}
            />
            {files.length > 0 && (
              <ul className="mt-1 mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
                {files.map((f, i) => (
                  <li key={i}>• {f.name}</li>
                ))}
              </ul>
            )}

            {errorMsg && (
              <div
                className="px-4 py-3 text-sm border-l-4 mb-3"
                style={{ background: "#fce4ec", borderColor: "var(--red)", color: "#c62828" }}
              >
                {errorMsg}
              </div>
            )}

            <button
              type="submit"
              disabled={phase === "uploading" || files.length === 0}
              className="w-full py-3 font-bold uppercase tracking-wide text-sm"
              style={{
                fontFamily: "Montserrat, sans-serif",
                background: phase === "uploading" || files.length === 0 ? "#888" : "var(--navy)",
                color: "#fff",
                border: "none",
                cursor: phase === "uploading" || files.length === 0 ? "not-allowed" : "pointer",
              }}
            >
              {phase === "uploading" ? "Uploading…" : "Upload"}
            </button>
          </form>
        )}
      </div>
    </PageShell>
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
