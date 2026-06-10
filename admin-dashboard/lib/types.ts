export type DocReq = {
  id: string
  label: string
  required: boolean
  internal: boolean
}

export type RefundTypeDocReqs = {
  docs: DocReq[]
  either_of: string[][]
}

export type DocReqsResponse = Record<string, RefundTypeDocReqs>

export type AuditEntry = {
  submissionId: string
  timestamp: string
  actor: string
  action: string
  details: Record<string, unknown>
}

export type AuditResponse = {
  submissionId: string
  entries: AuditEntry[]
}

export type PackageFile = { filename: string; downloadUrl: string; size: number }

export type Package = {
  submissionId: string
  name: string
  refundType: string
  files: PackageFile[]
}

export type Task = { label: string; done: boolean }

export type StatusValue = "partial" | "uploaded" | "under-review" | "approved" | "denied"

export type Confidence = "high" | "low"

export type Submission = {
  submissionId: string
  name: string
  refundType: string
  statuses: Record<string, StatusValue>
  documents: string[]
  confidence: Confidence
  submittedAt: string
  departments: string[]
  tasksByDepartment: Record<string, Task[]>
}

export type Permissions = {
  isSuperAdmin: boolean
  canDelete: boolean
  departments: string[] | null
}

export type StatusResponse = {
  submissions: Submission[]
  permissions: Permissions
}

export type Department = { key: string; label: string; refund_types: string[] }
export type AdminUser = { username: string; email: string; groups: string[]; notifyEmail: boolean; createdAt: string }
export type RefundType = { key: string; label: string; isDefault: boolean }

export type AdminConfig = {
  departments: Department[]
  users: AdminUser[]
  refundTypeLabels: Record<string, string>
  refundTypes: RefundType[]
}

export const STATUSES: StatusValue[] = [
  "partial", "uploaded", "under-review", "approved", "denied",
]

// Legacy seed list — used as a fallback when the API's refundTypes is missing
// (older backend) and as the authoritative set for any refund-type-specific
// hard-coded UI logic (e.g. the AP-13 PDF overlay).
export const LEGACY_REFUND_TYPES = ["STALE_WARRANT", "PAYROLL", "PROPERTY_TAX"] as const

export type FormField = {
  id: string
  label: string
  type: string
  required: boolean
  section: string
}

export type FormSchema = {
  title: string
  fields: FormField[]
}

export type FormSchemasResponse = Record<string, FormSchema>

export type ChatHandoffSummary = {
  sessionId: string
  refNumber: string
  reason: string
  requestedAt: string
  resolved: boolean
}

export type ChatSessionsResponse = {
  sessions: ChatHandoffSummary[]
}

export type ChatMessage = {
  timestamp: string
  role: "user" | "assistant"
  content: string
}

export type ChatSessionDetail = {
  meta: {
    sessionId: string
    startedAt: string
    disconnectedAt: string
    status: string
  }
  handoff: {
    refNumber?: string
    reason?: string
    requestedAt?: string
    resolved?: boolean
  }
  messages: ChatMessage[]
}

export function labelFor(type: string, labels: Record<string, string>) {
  return labels[type] || type.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}
