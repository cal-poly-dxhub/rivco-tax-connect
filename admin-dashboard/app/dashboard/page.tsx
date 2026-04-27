"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { currentSession, signOut } from "@/lib/cognito"
import { api } from "@/lib/api"

type Permissions = { isSuperAdmin: boolean; canDelete: boolean; departments: string[] | null }

export default function DashboardPage() {
  const router = useRouter()
  const [groups, setGroups] = useState<string[]>([])
  const [perms, setPerms] = useState<Permissions | null>(null)
  const [count, setCount] = useState<number | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    (async () => {
      const session = await currentSession()
      if (!session || session.kind !== "success") {
        router.replace("/")
        return
      }
      setGroups(session.groups)
      try {
        const res = await api("/status")
        if (!res.ok) throw new Error(`/status ${res.status}`)
        const data = await res.json()
        setPerms(data.permissions)
        setCount(data.submissions.length)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [router])

  function onSignOut() {
    signOut()
    router.push("/")
  }

  return (
    <div className="min-h-svh p-6">
      <div className="mx-auto flex max-w-4xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="font-medium">Riverside County — Admin Dashboard</h1>
          <Button variant="outline" onClick={onSignOut}>Sign out</Button>
        </header>

        <Card>
          <CardHeader><CardTitle>Session</CardTitle></CardHeader>
          <CardContent className="text-sm">
            <p>Groups: {groups.length ? groups.join(", ") : "none"}</p>
            {perms && (
              <p className="text-muted-foreground mt-1">
                {perms.isSuperAdmin
                  ? "Super-admin — full access."
                  : `Departments: ${perms.departments?.join(", ") || "none"}`}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Submissions</CardTitle></CardHeader>
          <CardContent className="text-sm">
            {error && <p className="text-destructive">{error}</p>}
            {count !== null && <p>{count} visible submissions (detailed view in Phase 4)</p>}
          </CardContent>
        </Card>

        {perms?.isSuperAdmin && (
          <Card>
            <CardHeader><CardTitle>Admin config (super-admin only)</CardTitle></CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              Departments + users management coming in Phase 4.
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
