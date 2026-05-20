"use client"

import { useEffect, useState } from "react"
import { renderFilledPdf, hasOverlayConfig, renderPropertyTaxHtml } from "@/lib/pdf-overlay"

type Props = {
  formDataUrl: string
  refundTypes: string[]
}

type RenderedItem = { type: string; url: string; label: string; signatureMissing?: boolean }

const REFUND_TYPE_LABELS: Record<string, string> = {
  STALE_WARRANT: "Stale Dated Warrant (AP-13)",
  PAYROLL: "Payroll",
  PROPERTY_TAX: "Property Tax",
}

function formatRefundType(rt: string): string {
  return REFUND_TYPE_LABELS[rt] || rt.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
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

        const results: RenderedItem[] = []

        for (const rt of refundTypes) {
          if (hasOverlayConfig(rt)) {
            const result = await renderFilledPdf(rt, formData, signature)
            if (result && !cancelled) {
              const blob = new Blob([result.bytes as unknown as BlobPart], { type: "application/pdf" })
              results.push({
                type: rt,
                url: URL.createObjectURL(blob),
                label: formatRefundType(rt),
                signatureMissing: result.signatureMissing,
              })
            }
          } else if (rt === "PROPERTY_TAX") {
            const html = await renderPropertyTaxHtml(formData)
            if (!cancelled) {
              const blob = new Blob([html], { type: "text/html" })
              results.push({
                type: rt,
                url: URL.createObjectURL(blob),
                label: formatRefundType(rt),
              })
            }
          }
        }

        if (!cancelled) {
          setPdfUrls(results)
          if (results.length) setActive(results[0].type)
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

  const activeItem = pdfUrls.find((p) => p.type === active) || pdfUrls[0]

  return (
    <div className="flex flex-col h-full">
      {pdfUrls.length > 1 && (
        <div className="flex gap-1 p-2 border-b bg-background">
          {pdfUrls.map((p) => (
            <button
              key={p.type}
              onClick={() => setActive(p.type)}
              className={`px-2 py-1 text-xs rounded ${
                active === p.type
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
