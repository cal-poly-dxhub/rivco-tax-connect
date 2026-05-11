import { PDFDocument, rgb, StandardFonts } from "pdf-lib"

export type FieldPosition = {
  fieldId: string
  page: number
  x: number
  y: number
  maxWidth?: number
  fontSize?: number
}

export type FormOverlayConfig = {
  pdfPath: string
  fields: FieldPosition[]
  signaturePage?: number
  signatureX?: number
  signatureY?: number
  signatureWidth?: number
  signatureHeight?: number
}

// AP-13 page dimensions: 612 x 792 (US Letter)
// Coordinates are from bottom-left origin (PDF standard)
const AP13_CONFIG: FormOverlayConfig = {
  pdfPath: "/forms/ap13-affidavit.pdf",
  fields: [
    // Page 1 — Warrant Information section
    { fieldId: "warrant_date", page: 0, x: 72, y: 528, maxWidth: 120, fontSize: 10 },
    { fieldId: "warrant_amount", page: 0, x: 198, y: 528, maxWidth: 120, fontSize: 10 },
    { fieldId: "warrant_number", page: 0, x: 330, y: 528, maxWidth: 110, fontSize: 10 },
    { fieldId: "business_unit", page: 0, x: 460, y: 528, maxWidth: 100, fontSize: 10 },

    // Page 1 — Checkboxes (is_owner / warrant_included) — rendered as text
    { fieldId: "is_owner", page: 0, x: 467, y: 460, maxWidth: 50, fontSize: 10 },
    { fieldId: "warrant_included", page: 0, x: 467, y: 418, maxWidth: 50, fontSize: 10 },

    // Page 1 — Signature block
    { fieldId: "phone", page: 0, x: 380, y: 198, maxWidth: 180, fontSize: 10 },
    { fieldId: "name", page: 0, x: 72, y: 163, maxWidth: 270, fontSize: 11 },
    { fieldId: "business_name", page: 0, x: 380, y: 163, maxWidth: 180, fontSize: 10 },
    { fieldId: "address", page: 0, x: 72, y: 131, maxWidth: 470, fontSize: 10 },
    { fieldId: "email", page: 0, x: 72, y: 100, maxWidth: 300, fontSize: 10 },
    { fieldId: "date_signed", page: 0, x: 460, y: 80, maxWidth: 100, fontSize: 10 },

    // Page 2 — Declaration (for warrants >= $1000)
    { fieldId: "name", page: 1, x: 95, y: 565, maxWidth: 230, fontSize: 10 },
    { fieldId: "address", page: 1, x: 355, y: 565, maxWidth: 200, fontSize: 10 },
    { fieldId: "city", page: 1, x: 95, y: 538, maxWidth: 200, fontSize: 10 },
    { fieldId: "state_zip", page: 1, x: 355, y: 538, maxWidth: 200, fontSize: 10 },
    { fieldId: "name", page: 1, x: 72, y: 362, maxWidth: 250, fontSize: 10 },
    { fieldId: "warrant_number", page: 1, x: 360, y: 362, maxWidth: 100, fontSize: 10 },
    { fieldId: "warrant_amount", page: 1, x: 470, y: 362, maxWidth: 90, fontSize: 10 },
    { fieldId: "address", page: 1, x: 72, y: 335, maxWidth: 400, fontSize: 10 },
  ],
  signaturePage: 0,
  signatureX: 72,
  signatureY: 210,
  signatureWidth: 250,
  signatureHeight: 35,
}

const OVERLAY_CONFIGS: Record<string, FormOverlayConfig> = {
  STALE_WARRANT: AP13_CONFIG,
}

export function hasOverlayConfig(refundType: string): boolean {
  return refundType in OVERLAY_CONFIGS
}

export function getOverlayConfig(refundType: string): FormOverlayConfig | null {
  return OVERLAY_CONFIGS[refundType] || null
}

export async function renderFilledPdf(
  refundType: string,
  formData: Record<string, unknown>,
  signatureDataUrl?: string,
): Promise<Uint8Array | null> {
  const config = OVERLAY_CONFIGS[refundType]
  if (!config) return null

  const pdfBytes = await fetch(config.pdfPath).then((r) => r.arrayBuffer())
  const pdfDoc = await PDFDocument.load(pdfBytes)
  const font = await pdfDoc.embedFont(StandardFonts.Helvetica)
  const pages = pdfDoc.getPages()

  for (const field of config.fields) {
    const value = formatFieldValue(field.fieldId, formData[field.fieldId])
    if (!value) continue

    const page = pages[field.page]
    if (!page) continue

    const size = field.fontSize || 10
    let text = value
    if (field.maxWidth) {
      while (font.widthOfTextAtSize(text, size) > field.maxWidth && text.length > 1) {
        text = text.slice(0, -1)
      }
    }

    page.drawText(text, {
      x: field.x,
      y: field.y,
      size,
      font,
      color: rgb(0.05, 0.05, 0.2),
    })
  }

  if (signatureDataUrl && config.signaturePage != null) {
    try {
      const sigBytes = dataUrlToBytes(signatureDataUrl)
      const sigImage = await pdfDoc.embedPng(sigBytes)
      const page = pages[config.signaturePage]
      if (page) {
        const scaled = sigImage.scaleToFit(
          config.signatureWidth || 200,
          config.signatureHeight || 40,
        )
        page.drawImage(sigImage, {
          x: config.signatureX || 72,
          y: config.signatureY || 200,
          width: scaled.width,
          height: scaled.height,
        })
      }
    } catch {
      // signature embed failed — continue without it
    }
  }

  return pdfDoc.save()
}

function formatFieldValue(fieldId: string, value: unknown): string {
  if (value === null || value === undefined) return ""
  if (typeof value === "boolean") return value ? "Yes" : "No"
  if (typeof value === "number") {
    if (fieldId.includes("amount")) return `$${value.toLocaleString()}`
    return String(value)
  }
  return String(value)
}

function dataUrlToBytes(dataUrl: string): Uint8Array {
  const base64 = dataUrl.split(",")[1]
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

export async function renderPropertyTaxHtml(
  formData: Record<string, unknown>,
): Promise<string> {
  const f = (id: string) => String(formData[id] || "")
  return `<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: Arial, sans-serif; font-size: 11px; margin: 40px; color: #1a1a2e; }
  h1 { font-size: 16px; text-align: center; margin-bottom: 4px; }
  h2 { font-size: 12px; text-align: center; font-weight: normal; margin-bottom: 20px; color: #555; }
  .header { text-align: center; border-bottom: 2px solid #002f87; padding-bottom: 12px; margin-bottom: 20px; }
  .section { margin-bottom: 16px; }
  .section-title { font-weight: bold; font-size: 12px; text-transform: uppercase; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-bottom: 8px; color: #002f87; }
  .row { display: flex; margin-bottom: 6px; }
  .label { font-weight: bold; width: 180px; flex-shrink: 0; }
  .value { border-bottom: 1px solid #999; flex: 1; padding-bottom: 2px; min-height: 14px; }
  .footer { margin-top: 30px; font-size: 10px; color: #666; text-align: center; }
</style>
</head>
<body>
  <div class="header">
    <h1>County of Riverside — Property Tax Refund Claim</h1>
    <h2>Office of the Auditor-Controller</h2>
  </div>
  <div class="section">
    <div class="section-title">Claimant Information</div>
    <div class="row"><span class="label">Name:</span><span class="value">${f("name")}</span></div>
    <div class="row"><span class="label">Mailing Address:</span><span class="value">${f("address")}</span></div>
    <div class="row"><span class="label">Email:</span><span class="value">${f("email")}</span></div>
    <div class="row"><span class="label">Phone:</span><span class="value">${f("phone")}</span></div>
  </div>
  <div class="section">
    <div class="section-title">Property Tax Details</div>
    <div class="row"><span class="label">Assessment Number:</span><span class="value">${f("assessment_number")}</span></div>
    <div class="row"><span class="label">Tax Year:</span><span class="value">${f("tax_year")}</span></div>
    <div class="row"><span class="label">Refund Amount:</span><span class="value">${f("refund_amount")}</span></div>
    <div class="row"><span class="label">Reason for Refund:</span><span class="value">${f("refund_reason")}</span></div>
  </div>
  <div class="section">
    <div class="section-title">Declaration</div>
    <p>I declare, under penalty of perjury under the laws of the State of California, that the foregoing is true and correct.</p>
    <div class="row" style="margin-top: 12px;"><span class="label">Date:</span><span class="value">${f("date_signed") || new Date().toLocaleDateString()}</span></div>
    <div class="row"><span class="label">Signature:</span><span class="value" style="min-height: 40px;"></span></div>
  </div>
  <div class="footer">
    County of Riverside · Office of the Auditor-Controller · 4080 Lemon Street, 6th Floor · Riverside, CA 92502-1326
  </div>
</body>
</html>`
}
