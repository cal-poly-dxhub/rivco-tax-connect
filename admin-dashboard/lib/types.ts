export type Task = { label: string; done: boolean }

export type Submission = {
  submissionId: string
  name: string
  refundType: string
  status: "partial" | "complete" | "under-review" | "approved" | "denied"
  documents: string[]
  submittedAt: string
  departments: string[]
  tasks: Task[]
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

export const STATUSES: Submission["status"][] = [
  "partial", "complete", "under-review", "approved", "denied",
]

export const REFUND_TYPES = ["STALE_WARRANT", "PAYROLL", "PROPERTY_TAX"] as const

export function labelFor(type: string, labels: Record<string, string>) {
  return labels[type] || type.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}
