export type ClaimStatus =
  | "draft"
  | "partial"
  | "uploaded"
  | "under-review"
  | "approved"
  | "denied";

export interface ClaimantSubmission {
  submissionId: string;
  name: string;
  refundType: string;
  overallStatus: ClaimStatus;
  documents: string[];
  submittedAt: string;
  updatedAt: string;
}

export interface QuizResponse {
  street_options: string[];
}

export interface VerifyResponse {
  token: string;
  expiresAt: string;
}

export interface ReserveResponse {
  submissionId: string;
}

export interface UploadRequest {
  filename: string;
  contentType: string;
}

export interface UploadSlot {
  filename: string;
  uploadUrl: string;
}

export interface ContinueResponse {
  uploads: UploadSlot[];
}

// ── Token helpers ──────────────────────────────────────────

const TOKEN_KEY = "claimant_token";
const TOKEN_ID_KEY = "claimant_submission_id";

export function storeToken(submissionId: string, token: string): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(TOKEN_ID_KEY, submissionId);
}

export function getToken(submissionId: string): string | null {
  if (typeof window === "undefined") return null;
  const storedId = sessionStorage.getItem(TOKEN_ID_KEY);
  if (storedId !== submissionId) return null;
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_ID_KEY);
}

// ── Status display ─────────────────────────────────────────

export function statusLabel(status: ClaimStatus): string {
  const map: Record<ClaimStatus, string> = {
    draft: "Your form is being prepared",
    partial: "Waiting for your documents",
    uploaded: "Received — under review",
    "under-review": "Being reviewed by the county",
    approved: "Approved — refund in process",
    denied: "Claim was not approved",
  };
  return map[status] ?? status;
}

export function statusColor(status: ClaimStatus): string {
  if (status === "approved") return "text-green-700 bg-green-50 border-green-300";
  if (status === "denied") return "text-red-700 bg-red-50 border-red-300";
  if (status === "under-review") return "text-blue-700 bg-blue-50 border-blue-300";
  return "text-yellow-800 bg-yellow-50 border-yellow-300";
}
