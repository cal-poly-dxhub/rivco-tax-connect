"use client"

import { useCallback, useEffect, useState } from "react"
import { api } from "@/lib/api"

type Options = {
  // Skip the request until truthy. Useful for waiting on an auth gate.
  enabled?: boolean
  // Extra values that should retrigger a reload when they change.
  deps?: ReadonlyArray<unknown>
}

// Standard "fetch JSON via the authenticated api() helper, track loading/error,
// expose reload" effect — replaces hand-rolled try/catch/setError blocks.
export function useApi<T>(path: string, { enabled = true, deps = [] }: Options = {}) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(enabled)

  const reload = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const res = await api(path)
      if (!res.ok) throw new Error(`${path} ${res.status}`)
      setData((await res.json()) as T)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError("")
      try {
        const res = await api(path)
        if (cancelled) return
        if (!res.ok) throw new Error(`${path} ${res.status}`)
        const json = (await res.json()) as T
        if (!cancelled) setData(json)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled, ...deps])

  return { data, error, loading, reload, setData }
}
