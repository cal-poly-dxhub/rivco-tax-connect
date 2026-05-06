"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { currentSession, signOut } from "@/lib/cognito"
import { api } from "@/lib/api"
import { ThemeToggle } from "@/components/theme-toggle"
import { AdminConfig, Department, AdminUser, REFUND_TYPES } from "@/lib/types"

export default function AdminConfigPage() {
  const router = useRouter()
  const [cfg, setCfg] = useState<AdminConfig | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      const session = await currentSession()
      if (!session || session.kind !== "success") return router.replace("/")
      if (!session.groups.includes("super-admin")) return router.replace("/dashboard")
      await reload()
    })()
  }, [router])

  async function reload() {
    setLoading(true)
    try {
      const res = await api("/admin/config")
      if (!res.ok) throw new Error(`/admin/config ${res.status}`)
      setCfg(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  function onSignOut() {
    signOut()
    router.push("/")
  }

  return (
    <div className="min-h-svh p-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="font-medium">Admin Config</h1>
          <div className="flex gap-2">
            <ThemeToggle />
            <Link href="/dashboard"><Button variant="outline">← Submissions</Button></Link>
            <Button variant="outline" onClick={onSignOut}>Sign out</Button>
          </div>
        </header>

        {error && <p className="text-destructive text-sm">{error}</p>}
        {loading || !cfg ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <Tabs defaultValue="departments">
            <TabsList>
              <TabsTrigger value="departments">Departments</TabsTrigger>
              <TabsTrigger value="users">Users</TabsTrigger>
              <TabsTrigger value="labels">Refund-type labels</TabsTrigger>
            </TabsList>
            <TabsContent value="departments">
              <DepartmentsTab cfg={cfg} reload={reload} />
            </TabsContent>
            <TabsContent value="users">
              <UsersTab cfg={cfg} reload={reload} />
            </TabsContent>
            <TabsContent value="labels">
              <LabelsTab cfg={cfg} reload={reload} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}

/* ─── Departments ─── */

function DepartmentsTab({ cfg, reload }: { cfg: AdminConfig; reload: () => void }) {
  const [editing, setEditing] = useState<Department | null>(null)
  const [creating, setCreating] = useState(false)

  async function remove(key: string) {
    if (!confirm(`Delete department "${key}"?`)) return
    await api(`/admin/departments/${encodeURIComponent(key)}`, { method: "DELETE" })
    reload()
  }

  return (
    <div className="flex flex-col gap-3 pt-4">
      <div className="flex justify-end">
        <Button onClick={() => setCreating(true)}>+ New department</Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Key</TableHead>
            <TableHead>Label</TableHead>
            <TableHead>Refund types</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cfg.departments.map((d) => (
            <TableRow key={d.key}>
              <TableCell className="font-mono text-xs">{d.key}</TableCell>
              <TableCell>{d.label}</TableCell>
              <TableCell>
                {d.refund_types.map((t) => <Badge key={t} variant="secondary" className="mr-1">{t}</Badge>)}
              </TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="sm" onClick={() => setEditing(d)}>Edit</Button>
                <Button variant="ghost" size="sm" onClick={() => remove(d.key)}>Delete</Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {creating && <DepartmentForm onClose={() => setCreating(false)} onSaved={reload} />}
      {editing && <DepartmentForm existing={editing} onClose={() => setEditing(null)} onSaved={reload} />}
    </div>
  )
}

function DepartmentForm({
  existing, onClose, onSaved,
}: { existing?: Department; onClose: () => void; onSaved: () => void }) {
  const [key, setKey] = useState(existing?.key || "")
  const [label, setLabel] = useState(existing?.label || "")
  const [types, setTypes] = useState<string[]>(existing?.refund_types || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  async function save() {
    setBusy(true); setErr("")
    const body = existing
      ? { label, refund_types: types }
      : { key, label, refund_types: types }
    const path = existing ? `/admin/departments/${encodeURIComponent(existing.key)}` : "/admin/departments"
    const res = await api(path, { method: existing ? "PATCH" : "POST", body: JSON.stringify(body) })
    setBusy(false)
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      setErr(data.error || `Save failed: ${res.status}`)
      return
    }
    onSaved(); onClose()
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{existing ? "Edit department" : "New department"}</DialogTitle></DialogHeader>
        <div className="flex flex-col gap-3">
          {!existing && (
            <div className="flex flex-col gap-2">
              <Label>Key</Label>
              <Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="payroll" />
            </div>
          )}
          <div className="flex flex-col gap-2">
            <Label>Label</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Payroll" />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Refund types</Label>
            {REFUND_TYPES.map((t) => (
              <label key={t} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={types.includes(t)}
                  onChange={(e) => setTypes(e.target.checked ? [...types, t] : types.filter((x) => x !== t))}
                />
                {t}
              </label>
            ))}
          </div>
          {err && <p className="text-destructive text-sm">{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ─── Users ─── */

function UsersTab({ cfg, reload }: { cfg: AdminConfig; reload: () => void }) {
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [creating, setCreating] = useState(false)

  async function remove(username: string) {
    if (!confirm(`Delete user "${username}"?`)) return
    const res = await api(`/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      alert(data.error || "Delete failed")
    }
    reload()
  }

  return (
    <div className="flex flex-col gap-3 pt-4">
      <div className="flex justify-end">
        <Button onClick={() => setCreating(true)}>+ New user</Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Username</TableHead>
            <TableHead>Email</TableHead>
            <TableHead>Groups</TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cfg.users.map((u) => (
            <TableRow key={u.username}>
              <TableCell className="font-mono text-xs">{u.username}</TableCell>
              <TableCell>{u.email}</TableCell>
              <TableCell>
                {u.groups.map((g) => <Badge key={g} variant="secondary" className="mr-1">{g}</Badge>)}
              </TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="sm" onClick={() => setEditing(u)}>Edit</Button>
                <Button variant="ghost" size="sm" onClick={() => remove(u.username)}>Delete</Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {creating && <UserForm cfg={cfg} onClose={() => setCreating(false)} onSaved={reload} />}
      {editing && <UserForm cfg={cfg} existing={editing} onClose={() => setEditing(null)} onSaved={reload} />}
    </div>
  )
}

function UserForm({
  cfg, existing, onClose, onSaved,
}: { cfg: AdminConfig; existing?: AdminUser; onClose: () => void; onSaved: () => void }) {
  const [email, setEmail] = useState(existing?.email || "")
  const [groups, setGroups] = useState<string[]>(existing?.groups || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  const allGroups = ["super-admin", ...cfg.departments.map((d) => `admin-${d.key}`)]

  async function save() {
    setBusy(true); setErr("")
    const body = existing ? { email, groups } : { email, groups }
    const path = existing ? `/admin/users/${encodeURIComponent(existing.username)}` : "/admin/users"
    const res = await api(path, { method: existing ? "PATCH" : "POST", body: JSON.stringify(body) })
    setBusy(false)
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      setErr(data.error || `Save failed: ${res.status}`)
      return
    }
    onSaved(); onClose()
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{existing ? "Edit user" : "New user"}</DialogTitle></DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-2">
            <Label>Email</Label>
            <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="jdoe@example.com" />
          </div>
          <div className="flex flex-col gap-2">
            <Label>Groups</Label>
            {allGroups.map((g) => (
              <label key={g} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={groups.includes(g)}
                  onChange={(e) => setGroups(e.target.checked ? [...groups, g] : groups.filter((x) => x !== g))}
                />
                {g}
              </label>
            ))}
          </div>
          {err && <p className="text-destructive text-sm">{err}</p>}
          {!existing && (
            <p className="text-xs text-muted-foreground">
              An invitation email with a temporary password will be sent.
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ─── Refund-type labels ─── */

function LabelsTab({ cfg, reload }: { cfg: AdminConfig; reload: () => void }) {
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string>("")

  async function save(type: string) {
    const label = (edits[type] ?? cfg.refundTypeLabels[type] ?? "").trim()
    if (!label) return
    setBusy(type)
    await api(`/admin/refund-types/${encodeURIComponent(type)}`, {
      method: "PUT", body: JSON.stringify({ label }),
    })
    setBusy(""); reload()
  }

  return (
    <div className="flex flex-col gap-3 pt-4 max-w-md">
      <p className="text-sm text-muted-foreground">
        Rename how refund types appear in the dashboard.
      </p>
      {REFUND_TYPES.map((t) => (
        <div key={t} className="flex items-center gap-2">
          <span className="font-mono text-xs w-36">{t}</span>
          <Input
            value={edits[t] ?? cfg.refundTypeLabels[t] ?? ""}
            onChange={(e) => setEdits({ ...edits, [t]: e.target.value })}
            placeholder={t}
          />
          <Button size="sm" onClick={() => save(t)} disabled={busy === t}>Save</Button>
        </div>
      ))}
    </div>
  )
}
