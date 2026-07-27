"use client"

import { useEffect, useState } from "react"
import { renderFilledPdf, hasOverlayConfig, renderPropertyTaxHtml } from "@/lib/pdf-overlay"

type Props = {
  formDataUrl: string
  refundTypes: string[]
}

type RenderedItem = {
  // Stable id for the tab list. Includes the claim index so duplicate
  // refund types can each have their own tab.
  id: string
  url: string
  label: string
  signatureMissing?: boolean
}

const REFUND_TYPE_LABELS: Record<string, string> = {
  STALE_WARRANT: "Stale Dated Warrant (AP-13)",
  PAYROLL: "Payroll",
  PROPERTY_TAX: "Property Tax",
}

function formatRefundType(rt: string): string {
  return REFUND_TYPE_LABELS[rt] || rt.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}

interface ClaimEntry {
  type: string
  warrant_number?: string
  warrant_amount?: string
  warrant_date?: string
  business_unit?: string
  business_name?: string
  is_owner?: string
  warrant_included?: string
  assessment_number?: string
  tax_year?: string
  refund_amount?: string
}

export function FilledFormViewer({ formDataUrl, refundTypes }: Props) {
  const [pdfUrls, setPdfUrls] = useState<RenderedItem[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false

    async function generate() {
      setLoading(true)
      setError("")
      setPdfUrls([])
      try {
        const resp = await fetch(formDataUrl)
        if (!resp.ok) throw new Error(`Failed to fetch form data: ${resp.status}`)
        const json = await resp.json()
        const formData: Record<string, unknown> = json.formData || json
        const signature: string | undefined = json.signature
        const submittedAt: string | undefined = typeof json.submittedAt === "string" ? json.submittedAt : undefined

        // Multi-claim shape (current portal builds): one entry per refund.
        // Older single-claim submissions don't include `claims` — fall back
        // to one synthetic entry per refund type using formData directly.
        const rawClaims: ClaimEntry[] = Array.isArray(json.claims) && json.claims.length
          ? (json.claims as ClaimEntry[])
          : refundTypes.map((rt) => ({ type: rt }))

        const results: RenderedItem[] = []
        const claimNumByType: Record<string, number> = {}
        const totalByType: Record<string, number> = {}
        for (const c of rawClaims) totalByType[c.type] = (totalByType[c.type] ?? 0) + 1

        for (let i = 0; i < rawClaims.length; i++) {
          const claim = rawClaims[i]
          const rt = claim.type
          // Merge shared form data with this claim's per-claim fields so the
          // PDF overlay sees a flat record. Per-claim fields (warrant_*,
          // is_owner, warrant_included, etc.) take precedence over any
          // legacy single-claim values stuffed into formData.
          const merged: Record<string, unknown> = { ...formData }
          for (const [k, v] of Object.entries(claim)) {
            if (k === "type") continue
            if (v !== undefined && v !== null && v !== "") merged[k] = v
          }

          const claimNum = (claimNumByType[rt] = (claimNumByType[rt] ?? 0) + 1)
          const total = totalByType[rt]
          const baseLabel = formatRefundType(rt)
          const label = total > 1 ? `${baseLabel} ${claimNum}/${total}` : baseLabel

          if (hasOverlayConfig(rt)) {
            const result = await renderFilledPdf(rt, merged, signature, submittedAt)
            if (result && !cancelled) {
              const blob = new Blob([result.bytes as unknown as BlobPart], { type: "application/pdf" })
              results.push({
                id: `${rt}-${i}`,
                url: URL.createObjectURL(blob),
                label,
                signatureMissing: result.signatureMissing,
              })
            }
          } else if (rt === "PROPERTY_TAX") {
            const html = await renderPropertyTaxHtml(merged)
            if (!cancelled) {
              const blob = new Blob([html], { type: "text/html" })
              results.push({
                id: `${rt}-${i}`,
                url: URL.createObjectURL(blob),
                label,
              })
            }
          }
        }

        if (!cancelled) {
          setPdfUrls(results)
          if (results.length) setActive(results[0].id)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    generate()
    return () => {
      cancelled = true
      setPdfUrls((prev) => {
        prev.forEach((p) => URL.revokeObjectURL(p.url))
        return []
      })
    }
  }, [formDataUrl, refundTypes])

  if (loading) {
    return <div className="flex items-center justify-center h-full text-sm text-muted-foreground">Generating filled form…</div>
  }

  if (error) {
    return <div className="flex items-center justify-center h-full text-sm text-destructive p-4">{error}</div>
  }

  if (!pdfUrls.length) {
    return <div className="flex items-center justify-center h-full text-sm text-muted-foreground">No form overlay available for this refund type.</div>
  }

  const activeItem = pdfUrls.find((p) => p.id === active) || pdfUrls[0]

  return (
    <div className="flex flex-col h-full">
      {pdfUrls.length > 1 && (
        <div className="flex gap-1 p-2 border-b bg-background flex-wrap">
          {pdfUrls.map((p) => (
            <button
              key={p.id}
              onClick={() => setActive(p.id)}
              className={`px-2 py-1 text-xs rounded ${
                active === p.id
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:bg-muted/80"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}
      {activeItem.signatureMissing && (
        <div className="px-3 py-2 text-xs bg-amber-50 text-amber-900 border-b border-amber-200">
          Signature could not be rendered in this preview. The signed payload is preserved in the raw <code>unified-form.json</code>.
        </div>
      )}
      <iframe
        src={activeItem.url}
        className="flex-1 w-full min-h-[500px]"
        title={activeItem.label}
      />
    </div>
  )
}
