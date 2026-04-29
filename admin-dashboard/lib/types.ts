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

export type Submission = {
  submissionId: string
  name: string
  refundType: string
  statuses: Record<string, StatusValue>
  documents: string[]
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
export type AdminUser = { username: string; email: string; groups: string[]; createdAt: string }

export type AdminConfig = {
  departments: Department[]
  users: AdminUser[]
  refundTypeLabels: Record<string, string>
}

export const STATUSES: StatusValue[] = [
  "partial", "uploaded", "under-review", "approved", "denied",
]

export const REFUND_TYPES = ["STALE_WARRANT", "PAYROLL", "PROPERTY_TAX"] as const

export function labelFor(type: string, labels: Record<string, string>) {
  return labels[type] || type.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}
