"use client"

import { config, currentSession, signOut } from "./cognito"

export async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const session = await currentSession()
  if (!session || session.kind !== "success") {
    signOut()
    if (typeof window !== "undefined") window.location.href = "/"
    throw new Error("Not signed in")
  }
  const headers = new Headers(init.headers || {})
  headers.set("Authorization", session.idToken)
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json")
  }
  const res = await fetch(config().API_URL + path, { ...init, headers })
  if (res.status === 401) {
    signOut()
    if (typeof window !== "undefined") window.location.href = "/"
    throw new Error("Session expired")
  }
  return res
}
