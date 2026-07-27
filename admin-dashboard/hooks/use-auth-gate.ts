"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { currentSession } from "@/lib/cognito"

type Options = { requireSuperAdmin?: boolean }

// Centralizes the "verify Cognito session, redirect on failure, signal ready"
// dance that every authenticated dashboard page repeats.
export function useAuthGate({ requireSuperAdmin = false }: Options = {}) {
  const router = useRouter()
  const [ready, setReady] = useState(false)
  const [isSuperAdmin, setIsSuperAdmin] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const session = await currentSession()
      if (cancelled) return
      if (!session || session.kind !== "success") {
        router.replace("/")
        return
      }
      const superAdmin = session.groups.includes("super-admin")
      if (requireSuperAdmin && !superAdmin) {
        router.replace("/dashboard")
        return
      }
      setIsSuperAdmin(superAdmin)
      setReady(true)
    })()
    return () => {
      cancelled = true
    }
  }, [router, requireSuperAdmin])

  return { ready, isSuperAdmin }
}
