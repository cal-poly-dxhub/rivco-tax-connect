"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { signIn, completeNewPassword } from "@/lib/cognito"

export default function NewPasswordPage() {
  const router = useRouter()
  const [newPassword, setNewPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [hasContext, setHasContext] = useState(false)

  useEffect(() => {
    if (!sessionStorage.getItem("__cog_user") || !sessionStorage.getItem("__cog_tmp_pw")) {
      router.replace("/")
      return
    }
    setHasContext(true)
  }, [router])

  if (!hasContext) return null

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    if (newPassword !== confirm) {
      setError("Passwords don't match.")
      return
    }
    setLoading(true)
    const username = sessionStorage.getItem("__cog_user") || ""
    const tmpPw = sessionStorage.getItem("__cog_tmp_pw") || ""
    const first = await signIn(username, tmpPw)
    if (first.kind !== "new-password") {
      setLoading(false)
      setError("Session expired. Please sign in again.")
      router.replace("/")
      return
    }
    const result = await completeNewPassword(first.user, newPassword)
    setLoading(false)
    sessionStorage.removeItem("__cog_user")
    sessionStorage.removeItem("__cog_tmp_pw")
    if (result.kind === "success") router.push("/dashboard")
    else setError(result.kind === "error" ? result.message : "Unexpected response")
  }

  return (
    <div className="flex min-h-svh items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Set a new password</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="new">New password</Label>
              <Input id="new" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} autoComplete="new-password" required minLength={8} />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="confirm">Confirm password</Label>
              <Input id="confirm" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
            </div>
            {error && <p className="text-destructive text-sm">{error}</p>}
            <Button type="submit" disabled={loading}>
              {loading ? "Saving…" : "Save password"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
