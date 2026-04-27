"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { sendResetCode, confirmReset } from "@/lib/cognito"

export default function ResetPasswordPage() {
  const router = useRouter()
  const [username, setUsername] = useState("")
  const [code, setCode] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [sending, setSending] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [sent, setSent] = useState(false)

  useEffect(() => {
    const u = sessionStorage.getItem("__cog_user")
    if (!u) {
      router.replace("/")
      return
    }
    setUsername(u)
    sendResetCode(u).then((r) => {
      setSending(false)
      if (r.ok) setSent(true)
      else setError(r.message || "Failed to send reset code")
    })
  }, [router])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    if (newPassword !== confirm) {
      setError("Passwords don't match.")
      return
    }
    setSaving(true)
    const r = await confirmReset(username, code.trim(), newPassword)
    setSaving(false)
    if (r.ok) {
      sessionStorage.removeItem("__cog_user")
      router.push("/")
    } else {
      setError(r.message || "Failed to reset password")
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Reset your password</CardTitle>
        </CardHeader>
        <CardContent>
          {sending && <p className="text-sm text-muted-foreground">Sending verification code to your email…</p>}
          {!sending && !sent && <p className="text-destructive text-sm">{error || "Could not send code."}</p>}
          {sent && (
            <form onSubmit={onSubmit} className="flex flex-col gap-4">
              <p className="text-sm text-muted-foreground">Check your email for a verification code.</p>
              <div className="flex flex-col gap-2">
                <Label htmlFor="code">Verification code</Label>
                <Input id="code" value={code} onChange={(e) => setCode(e.target.value)} required autoComplete="one-time-code" />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="new">New password</Label>
                <Input id="new" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required minLength={8} autoComplete="new-password" />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="confirm">Confirm password</Label>
                <Input id="confirm" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required autoComplete="new-password" />
              </div>
              {error && <p className="text-destructive text-sm">{error}</p>}
              <Button type="submit" disabled={saving}>
                {saving ? "Saving…" : "Save password"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
