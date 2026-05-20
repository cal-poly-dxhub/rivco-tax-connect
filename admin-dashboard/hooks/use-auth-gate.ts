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

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const session = await currentSession()
      if (cancelled) return
      if (!session || session.kind !== "success") {
        router.replace("/")
        return
      }
      if (requireSuperAdmin && !session.groups.includes("super-admin")) {
        router.replace("/dashboard")
        return
      }
      setReady(true)
    })()
    return () => {
      cancelled = true
    }
  }, [router, requireSuperAdmin])

  return { ready }
}
