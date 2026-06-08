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
import { signOut } from "@/lib/cognito"
import { api } from "@/lib/api"
import { ThemeToggle } from "@/components/theme-toggle"
import { AdminConfig, Department, AdminUser, RefundType, LEGACY_REFUND_TYPES, DocReq, DocReqsResponse, FormField, FormSchema, FormSchemasResponse } from "@/lib/types"
import { useAuthGate } from "@/hooks/use-auth-gate"
import { useApi } from "@/hooks/use-api"

export default function AdminConfigPage() {
  const router = useRouter()
  const { ready } = useAuthGate({ requireSuperAdmin: true })
  const { data: cfg, error, loading, reload } = useApi<AdminConfig>("/admin/config", { enabled: ready })

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
              <TabsTrigger value="refund-types">Refund types</TabsTrigger>
              <TabsTrigger value="docs">Document requirements</TabsTrigger>
              <TabsTrigger value="schemas">Form schemas</TabsTrigger>
            </TabsList>
            <TabsContent value="departments">
              <DepartmentsTab cfg={cfg} reload={reload} />
            </TabsContent>
            <TabsContent value="users">
              <UsersTab cfg={cfg} reload={reload} />
            </TabsContent>
            <TabsContent value="refund-types">
              <RefundTypesTab cfg={cfg} reload={reload} />
            </TabsContent>
            <TabsContent value="docs">
              <DocsTab refundTypes={refundTypeKeys(cfg)} />
            </TabsContent>
            <TabsContent value="schemas">
              <SchemasTab refundTypes={refundTypeKeys(cfg)} />
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  )
}

/* ─── Departments ─── */

function refundTypeKeys(cfg: AdminConfig): string[] {
  // Backend may be older and not yet return refundTypes — fall back to the
  // legacy seed list so the dashboard still renders something usable.
  if (cfg.refundTypes && cfg.refundTypes.length > 0) {
    return cfg.refundTypes.map((rt) => rt.key)
  }
  return [...LEGACY_REFUND_TYPES]
}

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

      {creating && <DepartmentForm cfg={cfg} onClose={() => setCreating(false)} onSaved={reload} />}
      {editing && <DepartmentForm cfg={cfg} existing={editing} onClose={() => setEditing(null)} onSaved={reload} />}
    </div>
  )
}

function DepartmentForm({
  cfg, existing, onClose, onSaved,
}: { cfg: AdminConfig; existing?: Department; onClose: () => void; onSaved: () => void }) {
  const [key, setKey] = useState(existing?.key || "")
  const [label, setLabel] = useState(existing?.label || "")
  const [types, setTypes] = useState<string[]>(existing?.refund_types || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")
  const refundTypes = refundTypeKeys(cfg)

  async function save() {
    setBusy(true); setErr("")
    const body = existing
      ? { label, refund_types: types }
      : { key, label, refund_types: types }
    const path = existing ? `/admin/departments/${encodeURIComponent(existing.key)}` : "/admin/departments"
    try {
      const res = await api(path, { method: existing ? "PATCH" : "POST", body: JSON.stringify(body) })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setErr(data.error || `Save failed: ${res.status}`)
        return
      }
      onSaved(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
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
            {refundTypes.length === 0 && (
              <p className="text-xs text-muted-foreground">No refund types defined yet — add some in the <strong>Refund types</strong> tab first.</p>
            )}
            {refundTypes.map((t) => (
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
            <TableHead>Notifications</TableHead>
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
              <TableCell>
                {u.notifyEmail
                  ? <Badge variant="secondary">on</Badge>
                  : <Badge variant="outline" className="text-muted-foreground">off</Badge>}
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
  const [notifyEmail, setNotifyEmail] = useState(existing?.notifyEmail ?? true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  const allGroups = ["super-admin", ...cfg.departments.map((d) => `admin-${d.key}`)]

  async function save() {
    setBusy(true); setErr("")
    const body = { email, groups, notifyEmail }
    const path = existing ? `/admin/users/${encodeURIComponent(existing.username)}` : "/admin/users"
    try {
      const res = await api(path, { method: existing ? "PATCH" : "POST", body: JSON.stringify(body) })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setErr(data.error || `Save failed: ${res.status}`)
        return
      }
      onSaved(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
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
          <div className="flex flex-col gap-2">
            <Label>Notifications</Label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={notifyEmail}
                onChange={(e) => setNotifyEmail(e.target.checked)}
              />
              Receive email notifications for new submissions and review-ready alerts
            </label>
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

/* ─── Refund types ─── */

function RefundTypesTab({ cfg, reload }: { cfg: AdminConfig; reload: () => void }) {
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<RefundType | null>(null)
  const types = cfg.refundTypes || []

  async function remove(t: RefundType) {
    if (!confirm(`Delete refund type "${t.key}"? This will also remove its document requirements and form schema.`)) return
    const res = await api(`/admin/refund-types/${encodeURIComponent(t.key)}`, { method: "DELETE" })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      alert(data.error || "Delete failed")
    }
    reload()
  }

  return (
    <div className="flex flex-col gap-3 pt-4">
      <p className="text-sm text-muted-foreground">
        Refund types drive every per-type form schema, document checklist, and department mapping.
        Adding a new type lets you define its own form fields and required docs without a code change.
      </p>
      <div className="flex justify-end">
        <Button onClick={() => setCreating(true)}>+ New refund type</Button>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Key</TableHead>
            <TableHead>Display label</TableHead>
            <TableHead></TableHead>
            <TableHead></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {types.map((t) => (
            <TableRow key={t.key}>
              <TableCell className="font-mono text-xs">{t.key}</TableCell>
              <TableCell>{t.label}</TableCell>
              <TableCell>
                {t.isDefault && <Badge variant="outline" className="text-muted-foreground">Default</Badge>}
              </TableCell>
              <TableCell className="text-right">
                <Button variant="ghost" size="sm" onClick={() => setEditing(t)}>Edit label</Button>
                <Button variant="ghost" size="sm" onClick={() => remove(t)}>Delete</Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {creating && <RefundTypeForm onClose={() => setCreating(false)} onSaved={reload} />}
      {editing && <RefundTypeForm existing={editing} onClose={() => setEditing(null)} onSaved={reload} />}
    </div>
  )
}

function RefundTypeForm({
  existing, onClose, onSaved,
}: { existing?: RefundType; onClose: () => void; onSaved: () => void }) {
  const [key, setKey] = useState(existing?.key || "")
  const [label, setLabel] = useState(existing?.label || "")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  async function save() {
    setBusy(true); setErr("")
    try {
      const path = existing
        ? `/admin/refund-types/${encodeURIComponent(existing.key)}`
        : `/admin/refund-types`
      const body = existing ? { label } : { key: key.toUpperCase(), label }
      const res = await api(path, { method: existing ? "PATCH" : "POST", body: JSON.stringify(body) })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setErr(data.error || `Save failed: ${res.status}`)
        return
      }
      onSaved(); onClose()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{existing ? "Edit refund type" : "New refund type"}</DialogTitle></DialogHeader>
        <div className="flex flex-col gap-3">
          {!existing && (
            <div className="flex flex-col gap-2">
              <Label>Key</Label>
              <Input value={key} onChange={(e) => setKey(e.target.value.toUpperCase())} placeholder="BUSINESS_LICENSE" />
              <p className="text-xs text-muted-foreground">Uppercase letters, digits, and underscores. Cannot be changed later.</p>
            </div>
          )}
          <div className="flex flex-col gap-2">
            <Label>Display label</Label>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Business License Refund" />
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


/* ─── Document requirements ─── */

function DocsTab({ refundTypes }: { refundTypes: string[] }) {
  const { data, error: err, reload } = useApi<DocReqsResponse>("/admin/doc-requirements")

  if (err) return <p className="text-destructive text-sm pt-4">{err}</p>
  if (!data) return <p className="text-muted-foreground text-sm pt-4">Loading…</p>

  if (refundTypes.length === 0) {
    return (
      <p className="text-sm text-muted-foreground pt-4">
        No refund types defined yet — add some in the <strong>Refund types</strong> tab first.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-6 pt-4">
      <p className="text-sm text-muted-foreground">
        Each refund type has a list of required documents. Mark a document <strong>internal</strong> to hide it from department admins (visible only to super-admin).
      </p>
      {refundTypes.map((rt) => (
        <DocTypeEditor
          key={rt}
          refundType={rt}
          initial={data[rt] || { docs: [], either_of: [] }}
          onSaved={reload}
        />
      ))}
    </div>
  )
}

function DocTypeEditor({
  refundType, initial, onSaved,
}: { refundType: string; initial: { docs: DocReq[]; either_of: string[][] }; onSaved: () => void }) {
  const [docs, setDocs] = useState<DocReq[]>(initial.docs)
  const [eitherOf, setEitherOf] = useState<string[][]>(initial.either_of || [])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  function update(i: number, patch: Partial<DocReq>) {
    setDocs((d) => d.map((doc, idx) => idx === i ? { ...doc, ...patch } : doc))
  }
  function remove(i: number) {
    setDocs((d) => d.filter((_, idx) => idx !== i))
  }
  function add() {
    setDocs((d) => [...d, { id: "", label: "", required: true, internal: false }])
  }
  function updateGroup(gi: number, value: string) {
    const items = value.split(",").map((s) => s.trim()).filter(Boolean)
    setEitherOf((g) => g.map((group, idx) => idx === gi ? items : group))
  }
  function removeGroup(gi: number) {
    setEitherOf((g) => g.filter((_, idx) => idx !== gi))
  }
  function addGroup() {
    setEitherOf((g) => [...g, []])
  }

  async function save() {
    setBusy(true); setErr("")
    const cleanDocs = docs.filter((d) => d.id.trim())
    try {
      const res = await api(`/admin/doc-requirements/${encodeURIComponent(refundType)}`, {
        method: "PUT",
        body: JSON.stringify({ docs: cleanDocs, either_of: eitherOf.filter((g) => g.length) }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setErr(data.error || `Save failed: ${res.status}`)
        return
      }
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border rounded-md p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="font-medium">{refundType}</h3>
        <Button size="sm" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</Button>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="py-1 pr-2">ID</th>
            <th className="py-1 pr-2">Label</th>
            <th className="py-1 pr-2 w-20">Required</th>
            <th className="py-1 pr-2 w-20">Internal</th>
            <th className="py-1 w-8"></th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d, i) => (
            <tr key={d.id || `new-${i}`} className="border-t">
              <td className="py-1 pr-2">
                <Input value={d.id} onChange={(e) => update(i, { id: e.target.value })} placeholder="photo-id" />
              </td>
              <td className="py-1 pr-2">
                <Input value={d.label} onChange={(e) => update(i, { label: e.target.value })} placeholder="Government photo ID" />
              </td>
              <td className="py-1 pr-2 text-center">
                <input type="checkbox" checked={d.required} onChange={(e) => update(i, { required: e.target.checked })} />
              </td>
              <td className="py-1 pr-2 text-center">
                <input type="checkbox" checked={d.internal} onChange={(e) => update(i, { internal: e.target.checked })} />
              </td>
              <td className="py-1">
                <Button variant="ghost" size="sm" onClick={() => remove(i)}>✕</Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Button variant="outline" size="sm" onClick={add} className="self-start">+ Add document</Button>

      <div className="flex flex-col gap-2 mt-2">
        <p className="text-xs text-muted-foreground">
          Either-of groups: claimant must supply at least one id from each list. Comma-separated ids.
        </p>
        {eitherOf.map((group, gi) => (
          <div key={gi} className="flex items-center gap-2">
            <Input
              value={group.join(", ")}
              onChange={(e) => updateGroup(gi, e.target.value)}
              placeholder="proof-of-payment, proof-of-ownership"
            />
            <Button variant="ghost" size="sm" onClick={() => removeGroup(gi)}>✕</Button>
          </div>
        ))}
        <Button variant="outline" size="sm" onClick={addGroup} className="self-start">+ Add group</Button>
      </div>

      {err && <p className="text-destructive text-sm">{err}</p>}
    </div>
  )
}

/* ─── Form schemas ─── */

const FIELD_TYPES = ["text", "email", "tel", "date", "number", "address", "textarea", "checkbox"] as const

function SchemasTab({ refundTypes }: { refundTypes: string[] }) {
  const { data: schemas, error: loadError, reload } = useApi<FormSchemasResponse>("/admin/form-schemas")
  const [selected, setSelected] = useState<string>(refundTypes[0] || "")
  const [editing, setEditing] = useState<FormSchema | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const error = saveError || loadError

  useEffect(() => {
    // If the selected type was deleted from another tab, fall back to the first.
    if (selected && !refundTypes.includes(selected) && refundTypes.length > 0) {
      setSelected(refundTypes[0])
    }
  }, [refundTypes, selected])

  useEffect(() => {
    if (schemas && selected && schemas[selected]) {
      setEditing(deepClone(schemas[selected]))
    }
  }, [selected, schemas])

  async function save() {
    if (!editing) return
    setSaving(true); setSaveError("")
    try {
      const res = await api(`/admin/form-schemas/${selected}`, {
        method: "PUT",
        body: JSON.stringify({ title: editing.title, fields: editing.fields }),
      })
      if (!res.ok) throw new Error((await res.json()).error || `${res.status}`)
      await reload()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  function updateField(i: number, patch: Partial<FormField>) {
    if (!editing) return
    const next = { ...editing, fields: editing.fields.map((f, idx) => idx === i ? { ...f, ...patch } : f) }
    setEditing(next)
  }
  function removeField(i: number) {
    if (!editing) return
    setEditing({ ...editing, fields: editing.fields.filter((_, idx) => idx !== i) })
  }
  function addField() {
    if (!editing) return
    setEditing({
      ...editing,
      fields: [...editing.fields, { id: "", label: "", type: "text", required: false, section: "common" }],
    })
  }
  function moveField(i: number, dir: -1 | 1) {
    if (!editing) return
    const j = i + dir
    if (j < 0 || j >= editing.fields.length) return
    const next = [...editing.fields]
    ;[next[i], next[j]] = [next[j], next[i]]
    setEditing({ ...editing, fields: next })
  }

  if (refundTypes.length === 0) {
    return (
      <p className="text-sm text-muted-foreground pt-4">
        No refund types defined yet — add some in the <strong>Refund types</strong> tab first.
      </p>
    )
  }

  if (!schemas || !editing) {
    return <p className="text-sm text-muted-foreground pt-4">{error || "Loading…"}</p>
  }

  const sectionOptions = ["common", ...refundTypes]

  return (
    <div className="flex flex-col gap-4 pt-4">
      <div className="flex items-center gap-3">
        <Label>Refund type</Label>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="border rounded px-2 py-1 text-sm bg-background"
        >
          {refundTypes.map((rt) => <option key={rt} value={rt}>{rt}</option>)}
        </select>
        <Button onClick={save} disabled={saving}>{saving ? "Saving…" : "Save schema"}</Button>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="schema-title">Form title</Label>
        <Input
          id="schema-title"
          value={editing.title}
          onChange={(e) => setEditing({ ...editing, title: e.target.value })}
        />
      </div>

      <div className="border rounded-md p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium">Fields ({editing.fields.length})</h3>
          <Button size="sm" variant="outline" onClick={addField}>+ Add field</Button>
        </div>
        <p className="text-xs text-muted-foreground mb-3">
          Fields with the same <code>id</code> across refund types are deduplicated on the unified claimant form.
          Use <code>section = common</code> for fields every claim needs (name, address, contact).
        </p>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10"></TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Label</TableHead>
              <TableHead className="w-28">Type</TableHead>
              <TableHead className="w-28">Section</TableHead>
              <TableHead className="w-20">Required</TableHead>
              <TableHead className="w-12"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {editing.fields.map((f, i) => (
              <TableRow key={f.id || `new-${i}`}>
                <TableCell>
                  <div className="flex flex-col">
                    <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => moveField(i, -1)} disabled={i === 0}>↑</button>
                    <button className="text-xs text-muted-foreground hover:text-foreground" onClick={() => moveField(i, 1)} disabled={i === editing.fields.length - 1}>↓</button>
                  </div>
                </TableCell>
                <TableCell>
                  <Input value={f.id} onChange={(e) => updateField(i, { id: e.target.value })} placeholder="warrant_number" />
                </TableCell>
                <TableCell>
                  <Input value={f.label} onChange={(e) => updateField(i, { label: e.target.value })} placeholder="Warrant Number" />
                </TableCell>
                <TableCell>
                  <select
                    value={f.type}
                    onChange={(e) => updateField(i, { type: e.target.value })}
                    className="w-full border rounded px-1 py-1 text-sm bg-background"
                  >
                    {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </TableCell>
                <TableCell>
                  <select
                    value={f.section}
                    onChange={(e) => updateField(i, { section: e.target.value })}
                    className="w-full border rounded px-1 py-1 text-sm bg-background"
                  >
                    {sectionOptions.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </TableCell>
                <TableCell className="text-center">
                  <input
                    type="checkbox"
                    checked={f.required}
                    onChange={(e) => updateField(i, { required: e.target.checked })}
                  />
                </TableCell>
                <TableCell>
                  <Button size="sm" variant="ghost" onClick={() => removeField(i)}>✕</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}
    </div>
  )
}

function deepClone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x))
}
