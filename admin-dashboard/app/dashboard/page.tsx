"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { currentSession, signOut } from "@/lib/cognito"
import { api } from "@/lib/api"
import {
  Submission, StatusResponse, Permissions, STATUSES, labelFor,
} from "@/lib/types"

export default function DashboardPage() {
  const router = useRouter()
  const [subs, setSubs] = useState<Submission[]>([])
  const [perms, setPerms] = useState<Permissions | null>(null)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [search, setSearch] = useState("")
  const [deptFilter, setDeptFilter] = useState<string>("all")
  const [statusFilter, setStatusFilter] = useState<string>("all")
  const [error, setError] = useState("")
  const [selected, setSelected] = useState<Submission | null>(null)

  useEffect(() => {
    (async () => {
      const session = await currentSession()
      if (!session || session.kind !== "success") return router.replace("/")
      try {
        const res = await api("/status")
        if (!res.ok) throw new Error(`/status ${res.status}`)
        const data: StatusResponse = await res.json()
        setSubs(data.submissions)
        setPerms(data.permissions)
        if (data.permissions.isSuperAdmin) {
          const cfgRes = await api("/admin/config")
          if (cfgRes.ok) setLabels((await cfgRes.json()).refundTypeLabels || {})
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [router])

  const availableDepts = useMemo(() => {
    const set = new Set<string>()
    subs.forEach((s) => s.departments.forEach((d) => set.add(d)))
    return Array.from(set).sort()
  }, [subs])

  const filtered = useMemo(() => {
    return subs.filter((s) => {
      if (deptFilter !== "all" && !s.departments.includes(deptFilter)) return false
      if (statusFilter !== "all" && s.status !== statusFilter) return false
      if (search && !s.name.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [subs, search, deptFilter, statusFilter])

  async function changeStatus(id: string, status: string) {
    setSubs((prev) => prev.map((s) => (s.submissionId === id ? { ...s, status: status as Submission["status"] } : s)))
    const res = await api("/update-status", {
      method: "POST",
      body: JSON.stringify({ submissionId: id, status }),
    })
    if (!res.ok) setError(`Update failed: ${res.status}`)
  }

  async function deleteSubmission(id: string) {
    if (!confirm("Delete submission? This removes all files.")) return
    const res = await api("/delete-submission", {
      method: "POST",
      body: JSON.stringify({ submissionId: id }),
    })
    if (res.ok) setSubs((prev) => prev.filter((s) => s.submissionId !== id))
    else setError(`Delete failed: ${res.status}`)
  }

  function onSignOut() {
    signOut()
    router.push("/")
  }

  return (
    <div className="min-h-svh p-6">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="font-medium">Riverside County — Admin Dashboard</h1>
          <div className="flex gap-2">
            {perms?.isSuperAdmin && (
              <Link href="/dashboard/config"><Button variant="outline">Admin config</Button></Link>
            )}
            <Button variant="outline" onClick={onSignOut}>Sign out</Button>
          </div>
        </header>

        {error && <p className="text-destructive text-sm">{error}</p>}

        <div className="flex flex-wrap gap-2 items-center">
          <Input
            placeholder="Search name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="max-w-xs"
          />
          <Select value={deptFilter} onValueChange={(v) => setDeptFilter(v ?? "all")}>
            <SelectTrigger className="w-[180px]"><SelectValue placeholder="Department" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All departments</SelectItem>
              {availableDepts.map((d) => (
                <SelectItem key={d} value={d}>{d}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v ?? "all")}>
            <SelectTrigger className="w-[160px]"><SelectValue placeholder="Status" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {STATUSES.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-sm text-muted-foreground ml-auto">
            {filtered.length} of {subs.length}
          </span>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Departments</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Tasks</TableHead>
              <TableHead>Submitted</TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((s) => {
              const taskDone = s.tasks.filter((t) => t.done).length
              return (
                <TableRow key={s.submissionId} className="cursor-pointer" onClick={() => setSelected(s)}>
                  <TableCell className="font-medium">{s.name}</TableCell>
                  <TableCell>
                    {s.refundType.split(",").map((t) => (
                      <Badge key={t} variant="secondary" className="mr-1">{labelFor(t, labels)}</Badge>
                    ))}
                  </TableCell>
                  <TableCell>
                    {s.departments.length ? s.departments.map((d) => (
                      <Badge key={d} variant="outline" className="mr-1">{d}</Badge>
                    )) : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Select value={s.status} onValueChange={(v) => v && changeStatus(s.submissionId, v)}>
                      <SelectTrigger className="w-[140px] h-8"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {STATUSES.map((st) => (
                          <SelectItem key={st} value={st}>{st}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell className="text-sm">{taskDone}/{s.tasks.length}</TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {s.submittedAt ? new Date(s.submittedAt).toLocaleDateString() : "—"}
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    {perms?.canDelete && (
                      <Button variant="ghost" size="sm" onClick={() => deleteSubmission(s.submissionId)}>
                        Delete
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>

        <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
          <DialogContent className="max-w-lg">
            {selected && (
              <>
                <DialogHeader>
                  <DialogTitle>{selected.name}</DialogTitle>
                </DialogHeader>
                <div className="text-sm">
                  <p className="text-muted-foreground">ID: {selected.submissionId}</p>
                  <p className="mt-2 font-medium">Tasks</p>
                  <ul className="mt-1 space-y-1">
                    {selected.tasks.map((t, i) => (
                      <li key={i} className={t.done ? "text-green-700" : "text-muted-foreground"}>
                        {t.done ? "✓" : "○"} {t.label}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-4 font-medium">Documents ({selected.documents.length})</p>
                  <ul className="mt-1 space-y-1">
                    {selected.documents.map((d) => <li key={d}>📄 {d}</li>)}
                  </ul>
                </div>
              </>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </div>
  )
}
