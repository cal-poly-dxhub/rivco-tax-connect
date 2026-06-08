"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { signOut } from "@/lib/cognito"
import { ThemeToggle } from "@/components/theme-toggle"
import { ChatSessionsResponse } from "@/lib/types"
import { useAuthGate } from "@/hooks/use-auth-gate"
import { useApi } from "@/hooks/use-api"

export default function ChatHandoffsPage() {
  const router = useRouter()
  const { ready } = useAuthGate({ requireSuperAdmin: true })
  const [filter, setFilter] = useState<"pending" | "all">("pending")
  const { data, error, loading } = useApi<ChatSessionsResponse>(
    `/admin/chat-sessions?status=${filter}`,
    { enabled: ready, deps: [filter] },
  )
  const sessions = data?.sessions ?? []

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
