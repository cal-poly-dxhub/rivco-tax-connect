"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { storeToken } from "@/lib/types";
import type { QuizResponse, VerifyResponse } from "@/lib/types";

const MAX_ATTEMPTS = 5;

export default function MyClaimPage() {
  const router = useRouter();

  // Lookup form
  const [claimId, setClaimId] = useState("");
  const [lookupError, setLookupError] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);

  // Quiz modal
  const [showModal, setShowModal] = useState(false);
  const [streetOptions, setStreetOptions] = useState<string[]>([]);
  const [selectedStreet, setSelectedStreet] = useState<string | null>(null);
  const [houseNumber, setHouseNumber] = useState("");
  const [verifyError, setVerifyError] = useState("");
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [attemptsRemaining, setAttemptsRemaining] = useState<number | null>(null);
  const [isLockedOut, setIsLockedOut] = useState(false);

  async function handleLookup(e: React.FormEvent) {
    e.preventDefault();
    const id = claimId.trim();
    if (!id) {
      setLookupError("Please enter your Claim ID.");
      return;
    }
    setLookupError("");
    setLookupLoading(true);
    try {
      const data = await apiFetch<QuizResponse>(
        `/claimant/quiz?id=${encodeURIComponent(id)}`,
      );
      setStreetOptions(data.street_options);
      setSelectedStreet(null);
      setHouseNumber("");
      setVerifyError("");
      setAttemptsRemaining(null);
      setIsLockedOut(false);
      setShowModal(true);
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 404) {
          setLookupError("Claim ID not found. Please check and try again.");
        } else if (e.status === 429) {
          setLookupError("Too many attempts. Please try again later.");
        } else {
          setLookupError(e.message);
        }
      } else {
        setLookupError("An unexpected error occurred.");
      }
    } finally {
      setLookupLoading(false);
    }
  }

  async function handleVerify() {
    if (!selectedStreet) {
      setVerifyError("Please select your street.");
      return;
    }
    if (!houseNumber.trim()) {
      setVerifyError("Please enter your house number.");
      return;
    }
    setVerifyError("");
    setVerifyLoading(true);
    try {
      const data = await apiFetch<VerifyResponse>("/claimant/verify", {
        method: "POST",
        body: JSON.stringify({
          submissionId: claimId.trim(),
          street: selectedStreet,
          number: houseNumber.trim(),
        }),
      });
      storeToken(claimId.trim(), data.token);
      setShowModal(false);
      router.push(`/claim?id=${encodeURIComponent(claimId.trim())}`);
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 403) {
          try {
            const body = JSON.parse(e.message) as {
              error?: string;
              attempts_remaining?: number;
            };
            const remaining = body.attempts_remaining ?? 0;
            setAttemptsRemaining(remaining);
            if (remaining <= 0) {
              setIsLockedOut(true);
              setVerifyError(
                "This claim has been locked due to too many failed attempts. Please try again in 1 hour.",
              );
            } else {
              setVerifyError(
                `Verification failed. ${remaining} attempt${remaining === 1 ? "" : "s"} remaining.`,
              );
            }
          } catch {
            setVerifyError("Verification failed. Please check your address.");
          }
        } else {
          setVerifyError(e.message);
        }
      } else {
        setVerifyError("An unexpected error occurred during verification.");
      }
    } finally {
      setVerifyLoading(false);
    }
  }

  function handleCloseModal() {
    setShowModal(false);
    setSelectedStreet(null);
    setHouseNumber("");
    setVerifyError("");
  }

  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center px-4 py-16"
      style={{ background: "var(--bg)" }}
    >
      <div
        className="w-full max-w-md"
        style={{
          background: "var(--surface)",
          border: "1px solid #999",
          boxShadow: "0 2px 12px rgba(0,0,0,0.12)",
        }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-4 px-6 py-4"
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

        <div className="px-8 py-8">
          <h1
            className="text-base font-bold uppercase tracking-wide mb-1"
            style={{ fontFamily: "Montserrat, sans-serif", color: "var(--navy)" }}
          >
            Check Claim Status
          </h1>
          <p className="text-sm mb-6" style={{ color: "var(--text-muted)" }}>
            Enter your Claim ID to look up your submission.
          </p>

          <form onSubmit={handleLookup}>
            <label
              htmlFor="claim-id"
              className="block text-xs uppercase tracking-widest mb-1"
              style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
            >
              Claim ID
            </label>
            <input
              id="claim-id"
              type="text"
              value={claimId}
              onChange={(e) => setClaimId(e.target.value)}
              placeholder="e.g. a1b2c3d4e5f6"
              className="w-full border-b bg-transparent outline-none py-2 text-sm mb-4"
              style={{ borderColor: "var(--border)" }}
              autoComplete="off"
            />

            {lookupError && (
              <div
                className="px-4 py-3 text-sm border-l-4 mb-4"
                style={{
                  background: "#fce4ec",
                  borderColor: "var(--red)",
                  color: "#c62828",
                }}
              >
                {lookupError}
              </div>
            )}

            <button
              type="submit"
              disabled={lookupLoading}
              className="w-full py-3 font-bold uppercase tracking-wide text-sm disabled:opacity-50"
              style={{
                fontFamily: "Montserrat, sans-serif",
                background: "var(--yellow)",
                color: "var(--navy-dark)",
                border: "none",
                cursor: lookupLoading ? "not-allowed" : "pointer",
              }}
            >
              {lookupLoading ? "Looking up…" : "Look Up"}
            </button>
          </form>

          <div className="mt-6 pt-4 border-t" style={{ borderColor: "var(--border-light)" }}>
            <a
              href="/"
              className="text-xs"
              style={{ color: "var(--navy)" }}
            >
              ← Back to home
            </a>
          </div>
        </div>

        <footer
          className="border-t pt-4 pb-6 text-center text-xs"
          style={{ borderColor: "var(--border-light)", color: "var(--text-muted)" }}
        >
          County of Riverside · Office of the Auditor-Controller
          <br />
          4080 Lemon Street, 6th Floor · P.O. Box 1326 · Riverside, CA 92502-1326
        </footer>
      </div>

      {/* ── Address quiz modal ── */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-4"
          style={{ background: "rgba(0,0,0,0.5)" }}
        >
          <div
            className="w-full max-w-md relative"
            style={{
              background: "var(--surface)",
              border: "2px solid var(--navy)",
              boxShadow: "0 8px 32px rgba(0,0,0,0.25)",
            }}
          >
            {/* Modal header */}
            <div
              className="px-6 py-4 flex items-center justify-between"
              style={{ background: "var(--navy)" }}
            >
              <span
                className="text-white font-bold uppercase tracking-wide text-sm"
                style={{ fontFamily: "Montserrat, sans-serif" }}
              >
                Verify Your Identity
              </span>
              <button
                type="button"
                onClick={handleCloseModal}
                className="text-gray-300 hover:text-white text-lg leading-none"
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <div className="px-6 py-6">
              <p className="text-sm mb-4" style={{ color: "var(--text-muted)" }}>
                To protect your claim, please select your street address and enter
                your house number.
              </p>

              {/* Street selection */}
              <div className="mb-4">
                <span
                  className="block text-xs uppercase tracking-widest mb-2"
                  style={{ fontFamily: "Montserrat, sans-serif", color: "var(--text-muted)" }}
                >
                  Select your street
                </span>
                <div className="grid grid-cols-1 gap-2">
                  {streetOptions.map((street) => (
                    <button
                      key={street}
                      type="button"
                      onClick={() => {
                        setSelectedStreet(street);
                        setVerifyError("");
                      }}
                      className="text-left px-4 py-3 border-2 text-sm transition-colors"
                      style={{
                        borderColor:
                          selectedStreet === street ? "var(--navy)" : "var(--border-light)",
                        background:
                          selectedStreet === street ? "#f0f4ff" : "var(--surface)",
                        color: "var(--text)",
                        fontWeight: selectedStreet === street ? 700 : 400,
                      }}
                    >
                      {street}
                    </button>
                  ))}
                </div>
              </div>

              {/* House number input — slides in after street selected */}
              <div
                style={{
                  overflow: "hidden",
                  maxHeight: selectedStreet ? "200px" : "0",
                  transition: "max-height 0.3s ease",
                }}
              >
                <div className="mb-4">
                  <label
                    htmlFor="house-number"
                    className="block text-xs uppercase tracking-widest mb-1"
                    style={{
                      fontFamily: "Montserrat, sans-serif",
                      color: "var(--text-muted)",
                    }}
                  >
                    House / Unit Number
                  </label>
                  <input
                    id="house-number"
                    type="text"
                    value={houseNumber}
                    onChange={(e) => setHouseNumber(e.target.value)}
                    placeholder="e.g. 123"
                    className="w-full border-b bg-transparent outline-none py-2 text-sm"
                    style={{ borderColor: "var(--border)" }}
                    autoComplete="off"
                  />
                </div>
              </div>

              {/* Error / lockout */}
              {verifyError && (
                <div
                  className="px-4 py-3 text-sm border-l-4 mb-4"
                  style={{
                    background: isLockedOut ? "#fff3cd" : "#fce4ec",
                    borderColor: isLockedOut ? "#f9a825" : "var(--red)",
                    color: isLockedOut ? "#7a5800" : "#c62828",
                  }}
                >
                  {verifyError}
                  {attemptsRemaining !== null &&
                    attemptsRemaining > 0 &&
                    attemptsRemaining <= 2 && (
                      <span className="block mt-1 font-bold">
                        Warning: {attemptsRemaining} attempt
                        {attemptsRemaining === 1 ? "" : "s"} remaining before
                        lockout.
                      </span>
                    )}
                </div>
              )}

              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={handleVerify}
                  disabled={verifyLoading || isLockedOut || !selectedStreet}
                  className="flex-1 py-3 font-bold uppercase tracking-wide text-sm disabled:opacity-50"
                  style={{
                    fontFamily: "Montserrat, sans-serif",
                    background: "var(--yellow)",
                    color: "var(--navy-dark)",
                    border: "none",
                    cursor:
                      verifyLoading || isLockedOut || !selectedStreet
                        ? "not-allowed"
                        : "pointer",
                  }}
                >
                  {verifyLoading ? "Verifying…" : "Verify"}
                </button>
                <button
                  type="button"
                  onClick={handleCloseModal}
                  className="px-5 py-3 text-sm border"
                  style={{
                    borderColor: "var(--border-light)",
                    color: "var(--text-muted)",
                    background: "transparent",
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
