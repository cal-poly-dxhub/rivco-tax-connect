"use client"

// Internal admin chatbot tester — bare-bones interface to exercise the same
// WebSocket the production chat widget uses. Same protocol: delta, tool_use,
// street_options, number_input, handoff, done, error.
//
// Reads window.__CHAT_TESTER_CONFIG__.WS_ENDPOINT (injected by CodeBuild into
// /public/config.js).

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { RefreshCcw, Send } from "lucide-react"

declare global {
  interface Window {
    __CHAT_TESTER_CONFIG__?: { WS_ENDPOINT: string }
  }
}

type Bubble = { role: "user" | "assistant" | "system"; text: string }

function generateSessionId(): string {
  const bytes = new Uint8Array(6)
  crypto.getRandomValues(bytes)
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("")
}

export default function ChatTesterPage() {
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [input, setInput] = useState("")
  const [streetOptions, setStreetOptions] = useState<string[] | null>(null)
  const [showNumberInput, setShowNumberInput] = useState(false)
  const [houseNumber, setHouseNumber] = useState("")
  const [handoffRef, setHandoffRef] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [endpoint, setEndpoint] = useState<string>("")
  const [sessionId, setSessionId] = useState<string>("")

  const wsRef = useRef<WebSocket | null>(null)
  const activeAssistantIdxRef = useRef<number | null>(null)
  const suppressDeltasRef = useRef(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const pendingNumberRef = useRef<string | null>(null)

  // Initial setup: read config + session id.
  useEffect(() => {
    const ep = window.__CHAT_TESTER_CONFIG__?.WS_ENDPOINT ?? ""
    setEndpoint(ep)
    let sid = sessionStorage.getItem("rcac_tester_session")
    if (!sid || !/^[a-z0-9]{12}$/.test(sid)) {
      sid = generateSessionId()
      sessionStorage.setItem("rcac_tester_session", sid)
    }
    setSessionId(sid)
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [bubbles, streetOptions, showNumberInput, handoffRef])

  const addBubble = useCallback((role: Bubble["role"], text: string): number => {
    let nextIdx = -1
    setBubbles((prev) => {
      nextIdx = prev.length
      return [...prev, { role, text }]
    })
    return nextIdx
  }, [])

  const ensureSocket = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
      return wsRef.current
    }
    if (!endpoint || !sessionId) return null

    const url = `${endpoint}?session=${encodeURIComponent(sessionId)}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.addEventListener("open", () => setConnected(true))
    ws.addEventListener("close", () => {
      setConnected(false)
      wsRef.current = null
    })
    ws.addEventListener("error", (e) => {
      console.warn("chat-tester: socket error", e)
    })
    ws.addEventListener("message", (event) => {
      let frame: { type: string; [k: string]: unknown }
      try {
        frame = JSON.parse(event.data)
      } catch {
        return
      }
      switch (frame.type) {
        case "delta": {
          if (suppressDeltasRef.current) return
          const text = String(frame.text ?? "")
          setBubbles((prev) => {
            const next = [...prev]
            if (activeAssistantIdxRef.current === null) {
              activeAssistantIdxRef.current = next.length
              next.push({ role: "assistant", text })
            } else {
              const idx = activeAssistantIdxRef.current
              next[idx] = { ...next[idx], text: next[idx].text + text }
            }
            return next
          })
          break
        }
        case "street_options": {
          const opts = frame.options
          if (Array.isArray(opts) && opts.length > 0) {
            suppressDeltasRef.current = true
            activeAssistantIdxRef.current = null
            setStreetOptions(opts.map(String))
          }
          break
        }
        case "number_input": {
          suppressDeltasRef.current = true
          activeAssistantIdxRef.current = null
          // If the user typed a number-like value before the verify step
          // arrived, auto-send it now (matches the production widget).
          if (pendingNumberRef.current) {
            const num = pendingNumberRef.current
            pendingNumberRef.current = null
            addBubble("user", num)
            sendRaw(num)
          } else {
            setShowNumberInput(true)
          }
          break
        }
        case "handoff": {
          if (typeof frame.reference === "string") {
            setHandoffRef(frame.reference)
          }
          break
        }
        case "done": {
          activeAssistantIdxRef.current = null
          suppressDeltasRef.current = false
          setBusy(false)
          break
        }
        case "error": {
          activeAssistantIdxRef.current = null
          suppressDeltasRef.current = false
          setBubbles((prev) => [...prev, { role: "system", text: `⚠ ${String(frame.message ?? "Something went wrong.")}` }])
          setBusy(false)
          break
        }
        case "tool_use":
          // optional: indicate the bot is calling a tool; we just rely on the
          // pause that follows.
          break
      }
    })
    return ws
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint, sessionId, addBubble])

  const sendRaw = useCallback(
    (text: string) => {
      const ws = ensureSocket()
      if (!ws) return
      const payload = JSON.stringify({ action: "sendMessage", session: sessionId, text })
      const fire = () => ws.send(payload)
      if (ws.readyState === WebSocket.OPEN) fire()
      else ws.addEventListener("open", fire, { once: true })
      setBusy(true)
    },
    [ensureSocket, sessionId]
  )

  function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setInput("")
    addBubble("user", text)
    // Heuristic from the production widget: if the user types a bare number
    // and the bot hasn't yet asked for a house number, hold it for the next
    // number_input frame.
    if (/^\d{1,8}$/.test(text) && !showNumberInput) {
      pendingNumberRef.current = text
    }
    sendRaw(text)
  }

  function onPickStreet(street: string) {
    setStreetOptions(null)
    suppressDeltasRef.current = false
    addBubble("user", street)
    sendRaw(street)
  }

  function onSubmitNumber(e: React.FormEvent) {
    e.preventDefault()
    const num = houseNumber.trim()
    if (!num) return
    setShowNumberInput(false)
    setHouseNumber("")
    suppressDeltasRef.current = false
    addBubble("user", num)
    sendRaw(num)
  }

  function onRestart() {
    if (wsRef.current) {
      try { wsRef.current.close() } catch { /* ignore */ }
      wsRef.current = null
    }
    sessionStorage.removeItem("rcac_tester_session")
    const fresh = generateSessionId()
    sessionStorage.setItem("rcac_tester_session", fresh)
    setSessionId(fresh)
    setBubbles([])
    setStreetOptions(null)
    setShowNumberInput(false)
    setHouseNumber("")
    setHandoffRef(null)
    setBusy(false)
    activeAssistantIdxRef.current = null
    suppressDeltasRef.current = false
    pendingNumberRef.current = null
  }

  const statusBadge = useMemo(() => {
    if (!endpoint) return <Badge variant="destructive">No endpoint</Badge>
    if (connected) return <Badge variant="secondary">Connected</Badge>
    return <Badge variant="outline">Idle</Badge>
  }, [endpoint, connected])

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Chatbot Tester</h1>
          <p className="text-xs text-muted-foreground">
            Internal admin tool. Talks to the same agent the public chat widget uses.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {statusBadge}
          <Button variant="outline" size="sm" onClick={onRestart} title="Start a new session">
            <RefreshCcw className="mr-1 h-3.5 w-3.5" />
            Restart
          </Button>
        </div>
      </header>

      {!endpoint && (
        <Card className="border-destructive p-4 text-sm">
          <p className="font-medium text-destructive">Configuration missing</p>
          <p className="mt-1 text-muted-foreground">
            <code>window.__CHAT_TESTER_CONFIG__.WS_ENDPOINT</code> wasn&apos;t set. CodeBuild
            should write this to <code>public/config.js</code> on each deploy.
          </p>
        </Card>
      )}

      <Card className="flex min-h-[60vh] flex-1 flex-col">
        <div className="flex-1 space-y-3 overflow-y-auto p-4" role="log" aria-live="polite">
          {bubbles.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Type a message to start. Try <em>&ldquo;My name is Carey Ministries&rdquo;</em>
              {" "}or <em>&ldquo;What services do you offer?&rdquo;</em>
            </p>
          )}
          {bubbles.map((b, i) => (
            <div
              key={i}
              className={
                b.role === "user"
                  ? "flex justify-end"
                  : b.role === "system"
                  ? "flex justify-center"
                  : "flex justify-start"
              }
            >
              <div
                className={
                  b.role === "user"
                    ? "max-w-[80%] whitespace-pre-wrap rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                    : b.role === "system"
                    ? "max-w-[90%] rounded-md bg-muted px-3 py-1.5 text-xs text-muted-foreground"
                    : "max-w-[80%] whitespace-pre-wrap rounded-lg bg-muted px-3 py-2 text-sm"
                }
              >
                {b.text}
              </div>
            </div>
          ))}

          {streetOptions && (
            <div className="space-y-2 rounded-md border bg-secondary/40 p-3">
              <p className="text-xs font-medium text-muted-foreground">Pick the street you&apos;ve lived on:</p>
              <div className="flex flex-wrap gap-2">
                {streetOptions.map((s) => (
                  <Button key={s} size="sm" variant="outline" onClick={() => onPickStreet(s)}>
                    {s}
                  </Button>
                ))}
              </div>
            </div>
          )}

          {showNumberInput && (
            <form onSubmit={onSubmitNumber} className="flex items-center gap-2 rounded-md border bg-secondary/40 p-3">
              <span className="text-xs text-muted-foreground">House number:</span>
              <Input
                type="text"
                inputMode="numeric"
                value={houseNumber}
                onChange={(e) => setHouseNumber(e.target.value)}
                className="h-8 w-32"
                autoFocus
              />
              <Button type="submit" size="sm">Send</Button>
            </form>
          )}

          {handoffRef && (
            <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
              <p className="font-semibold">Reference number</p>
              <p className="mt-1 font-mono">{handoffRef}</p>
              <p className="mt-1 text-xs">
                Call (951) 955-3800 and quote this reference; the agent will pick up where the bot left off.
              </p>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <form onSubmit={onSubmit} className="flex gap-2 border-t p-3">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message…"
            disabled={busy || !endpoint}
            maxLength={2000}
          />
          <Button type="submit" disabled={busy || !endpoint || !input.trim()}>
            <Send className="mr-1 h-3.5 w-3.5" />
            Send
          </Button>
        </form>
      </Card>

      <footer className="text-center text-xs text-muted-foreground">
        Session: <span className="font-mono">{sessionId || "—"}</span>
      </footer>
    </main>
  )
}
