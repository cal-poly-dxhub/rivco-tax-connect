"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { signOut } from "@/lib/cognito"
import { api } from "@/lib/api"
import { ThemeToggle } from "@/components/theme-toggle"
import { ChatSessionsResponse, ChatSessionDetail } from "@/lib/types"
import { useAuthGate } from "@/hooks/use-auth-gate"
import { useApi } from "@/hooks/use-api"

export default function ChatHandoffsPage() {
  const router = useRouter()
  const { ready } = useAuthGate()
  const [filter, setFilter] = useState<"pending" | "all">("pending")
  const { data, error, loading } = useApi<ChatSessionsResponse>(
    `/admin/chat-sessions?status=${filter}`,
    { enabled: ready, deps: [filter] },
  )
  const sessions = data?.sessions ?? []

  // Reference-number lookup: lets any admin paste a quoted ref (e.g. REF-A1B2C)
  // and jump straight to that session's transcript.
  const [refQuery, setRefQuery] = useState("")
  const [refError, setRefError] = useState("")
  const [refBusy, setRefBusy] = useState(false)

  async function lookUpByRef(e: React.FormEvent) {
    e.preventDefault()
    const ref = refQuery.trim().toUpperCase()
    if (!ref) return
    setRefBusy(true)
    setRefError("")
    try {
      const res = await api(`/admin/chat-sessions/by-ref/${encodeURIComponent(ref)}`)
      if (res.status === 404) {
        setRefError(`No handoff found for "${ref}".`)
        return
      }
      if (!res.ok) {
        setRefError(`Lookup failed: ${res.status}`)
        return
      }
      const data = (await res.json()) as ChatSessionDetail
      const sessionId = data.meta?.sessionId
      if (!sessionId) {
        setRefError("Lookup returned no session id.")
        return
      }
      router.push(`/dashboard/chat/session?id=${encodeURIComponent(sessionId)}`)
    } catch (err) {
      setRefError(err instanceof Error ? err.message : String(err))
    } finally {
      setRefBusy(false)
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
          <h1 className="font-medium">Chat handoffs</h1>
          <div className="flex gap-2">
            <ThemeToggle />
            <Link href="/dashboard"><Button variant="outline">← Submissions</Button></Link>
            <Button variant="outline" onClick={onSignOut}>Sign out</Button>
          </div>
        </header>

        <form
          onSubmit={lookUpByRef}
          className="flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center"
        >
          <label className="text-sm font-medium">Look up by reference:</label>
          <Input
            value={refQuery}
            onChange={(e) => setRefQuery(e.target.value.toUpperCase())}
            placeholder="REF-A1B2C"
            className="max-w-[200px] font-mono uppercase"
          />
          <Button type="submit" size="sm" disabled={refBusy || !refQuery.trim()}>
            {refBusy ? "Looking up…" : "Open transcript"}
          </Button>
          {refError && (
            <span className="text-destructive text-sm">{refError}</span>
          )}
        </form>

        <div className="flex gap-2">
          <Button
            variant={filter === "pending" ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("pending")}
          >
            Pending
          </Button>
          <Button
            variant={filter === "all" ? "default" : "outline"}
            size="sm"
            onClick={() => setFilter("all")}
          >
            All
          </Button>
        </div>

        {error && <p className="text-destructive text-sm">{error}</p>}
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No {filter === "pending" ? "pending handoffs" : "handoffs"} yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Reference</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead>Requested</TableHead>
                <TableHead>Status</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((s) => (
                <TableRow key={s.sessionId}>
                  <TableCell className="font-mono">{s.refNumber}</TableCell>
                  <TableCell className="max-w-md truncate">{s.reason}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {s.requestedAt ? new Date(s.requestedAt).toLocaleString() : ""}
                  </TableCell>
                  <TableCell>
                    {s.resolved ? (
                      <Badge variant="secondary">Resolved</Badge>
                    ) : (
                      <Badge className="bg-orange-100 text-orange-800 border-orange-300">Pending</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Link href={`/dashboard/chat/session?id=${encodeURIComponent(s.sessionId)}`}>
                      <Button variant="ghost" size="sm">Open</Button>
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
