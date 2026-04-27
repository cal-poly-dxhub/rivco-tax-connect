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
  Submission, StatusResponse, Permissions, STATUSES, labelFor, Package, PackageFile,
} from "@/lib/types"

const STATUS_STYLES: Record<Submission["status"], string> = {
  partial: "bg-orange-100 text-orange-800 border-orange-300",
  complete: "bg-green-100 text-green-800 border-green-300",
  "under-review": "bg-blue-100 text-blue-800 border-blue-300",
  approved: "bg-emerald-100 text-emerald-900 border-emerald-300",
  denied: "bg-red-100 text-red-800 border-red-300",
}

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
                      <SelectTrigger className={`w-[140px] h-8 ${STATUS_STYLES[s.status]}`}>
                        <SelectValue />
                      </SelectTrigger>
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
          <DialogContent className="!max-w-none !w-[80vw] !h-[80vh] flex flex-col">
            {selected && <SubmissionDetail submission={selected} />}
          </DialogContent>
        </Dialog>
      </div>
    </div>
  )
}

function SubmissionDetail({ submission }: { submission: Submission }) {
  const [pkg, setPkg] = useState<Package | null>(null)
  const [active, setActive] = useState<PackageFile | null>(null)
  const [err, setErr] = useState("")

  useEffect(() => {
    (async () => {
      try {
        const res = await api(`/package?id=${encodeURIComponent(submission.submissionId)}`)
        if (!res.ok) throw new Error(`/package ${res.status}`)
        const data: Package = await res.json()
        setPkg(data)
        if (data.files.length) setActive(data.files[0])
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [submission.submissionId])

  return (
    <>
      <DialogHeader>
        <DialogTitle>{submission.name}</DialogTitle>
      </DialogHeader>
      <div className="grid grid-cols-[260px_1fr] gap-4 text-sm flex-1 min-h-0">
        <aside className="flex flex-col gap-4 overflow-y-auto">
          <div>
            <p className="text-muted-foreground text-xs">ID: {submission.submissionId}</p>
            <p className="text-muted-foreground text-xs">{submission.refundType}</p>
          </div>
          <div>
            <p className="font-medium">Tasks</p>
            <ul className="mt-1 space-y-1">
              {submission.tasks.map((t, i) => (
                <li key={i} className={t.done ? "text-green-700" : "text-muted-foreground"}>
                  {t.done ? "✓" : "○"} {t.label}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="font-medium">Files {pkg && `(${pkg.files.length})`}</p>
            {err && <p className="text-destructive text-xs mt-1">{err}</p>}
            {!pkg && !err && <p className="text-muted-foreground text-xs mt-1">Loading…</p>}
            <ul className="mt-1 space-y-1">
              {pkg?.files.map((f) => (
                <li key={f.filename}>
                  <button
                    onClick={() => setActive(f)}
                    className={`text-left w-full truncate ${active?.filename === f.filename ? "font-semibold text-foreground" : "text-blue-600 hover:underline"}`}
                  >
                    📄 {f.filename}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </aside>
        <section className="border rounded-md bg-muted/20 overflow-hidden flex flex-col">
          {active ? <FileViewer file={active} /> : <p className="p-4 text-muted-foreground">Select a file to preview.</p>}
        </section>
      </div>
    </>
  )
}

function FileViewer({ file }: { file: PackageFile }) {
  const [jsonText, setJsonText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const ext = file.filename.split(".").pop()?.toLowerCase() || ""
  const isPdf = ext === "pdf"
  const isImage = ["jpg", "jpeg", "png", "heic", "gif", "webp"].includes(ext)
  const isJson = ext === "json"

  useEffect(() => {
    if (!isJson) return
    setLoading(true); setJsonText(null)
    fetch(file.downloadUrl)
      .then((r) => r.text())
      .then((t) => {
        try { setJsonText(JSON.stringify(JSON.parse(t), null, 2)) }
        catch { setJsonText(t) }
      })
      .catch((e) => setJsonText(`Failed to load: ${e.message}`))
      .finally(() => setLoading(false))
  }, [file.downloadUrl, isJson])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between border-b p-2 bg-background">
        <span className="text-xs truncate">{file.filename}</span>
        <a
          href={file.downloadUrl}
          download={file.filename}
          className="text-xs text-blue-600 hover:underline ml-2"
        >
          Download
        </a>
      </div>
      <div className="flex-1 overflow-auto">
        {isPdf && <iframe src={file.downloadUrl} className="w-full h-full min-h-[500px]" title={file.filename} />}
        {isImage && <img src={file.downloadUrl} alt={file.filename} className="max-w-full h-auto p-4 mx-auto" />}
        {isJson && (
          loading
            ? <p className="p-4 text-muted-foreground text-xs">Loading…</p>
            : <pre className="p-4 text-xs whitespace-pre-wrap break-all">{jsonText}</pre>
        )}
        {!isPdf && !isImage && !isJson && (
          <div className="p-4 text-sm text-muted-foreground">
            Preview not supported for .{ext}. Use Download.
          </div>
        )}
      </div>
    </div>
  )
}
