"use client"

import { useState, use } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { signOut } from "@/lib/cognito"
import { api } from "@/lib/api"
import { ThemeToggle } from "@/components/theme-toggle"
import { ChatSessionDetail } from "@/lib/types"
import { useAuthGate } from "@/hooks/use-auth-gate"
import { useApi } from "@/hooks/use-api"

export default function ChatSessionClient({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  const { ready } = useAuthGate({ requireSuperAdmin: true })
  const { data, error: loadError, loading, reload, setData } = useApi<ChatSessionDetail>(
    `/admin/chat-sessions/${id}`,
    { enabled: ready, deps: [id] },
  )
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const error = actionError || loadError

  async function resolve() {
    if (!confirm("Mark this handoff as resolved?")) return
    setBusy(true); setActionError("")
    try {
      const res = await api(`/admin/chat-sessions/${id}/resolve`, { method: "POST" })
      if (!res.ok) throw new Error(`Resolve failed: ${res.status}`)
      await reload()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!confirm(`Delete session ${id}? This wipes the entire transcript.`)) return
    setBusy(true); setActionError("")
    try {
      const res = await api(`/admin/chat-sessions/${id}`, { method: "DELETE" })
      if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
      setData(null)
      router.push("/dashboard/chat")
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  function onSignOut() {
    signOut()
    router.push("/")
  }

  function renderContent(content: string): string {
    try {
      const parsed = JSON.parse(content)
      if (Array.isArray(parsed)) {
        return parsed
          .map((b: { type?: string; text?: string; name?: string }) => {
            if (b.type === "text") return b.text || ""
            if (b.type === "tool_use") return `[tool: ${b.name}]`
            if (b.type === "tool_result") return `[tool result]`
            return ""
          })
          .filter(Boolean)
          .join("\n")
      }
    } catch {
      // plain string
    }
    return content
  }

  return (
    <div className="min-h-svh p-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="font-medium font-mono">{data?.handoff?.refNumber || id}</h1>
          <div className="flex gap-2">
            <ThemeToggle />
            <Link href="/dashboard/chat"><Button variant="outline">← Handoffs</Button></Link>
            <Button variant="outline" onClick={onSignOut}>Sign out</Button>
          </div>
        </header>

        {error && <p className="text-destructive text-sm">{error}</p>}
        {loading || !data ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <>
            <div className="rounded-md border p-4 flex flex-col gap-2 text-sm">
              <div className="flex justify-between">
                <span><strong>Started:</strong> {data.meta.startedAt ? new Date(data.meta.startedAt).toLocaleString() : "—"}</span>
                <span>{data.handoff.resolved ? <Badge variant="secondary">Resolved</Badge> : <Badge className="bg-orange-100 text-orange-800 border-orange-300">Pending</Badge>}</span>
              </div>
              {data.handoff.reason && (
                <div><strong>Reason:</strong> {data.handoff.reason}</div>
              )}
              <div className="flex gap-2 pt-2">
                {data.handoff.refNumber && !data.handoff.resolved && (
                  <Button size="sm" onClick={resolve} disabled={busy}>Mark resolved</Button>
                )}
                <Button size="sm" variant="ghost" onClick={remove} disabled={busy}>Delete session</Button>
              </div>
            </div>

            <div className="rounded-md border p-4 flex flex-col gap-3">
              <h2 className="text-sm font-semibold text-muted-foreground">Transcript</h2>
              {data.messages.length === 0 ? (
                <p className="text-sm text-muted-foreground">No messages.</p>
              ) : (
                data.messages.map((m, i) => {
                  const text = renderContent(m.content)
                  if (!text) return null
                  return (
                    <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[80%] rounded-md px-3 py-2 text-sm whitespace-pre-wrap ${
                        m.role === "user" ? "bg-blue-100 text-blue-900" : "bg-muted"
                      }`}>
                        {text}
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
