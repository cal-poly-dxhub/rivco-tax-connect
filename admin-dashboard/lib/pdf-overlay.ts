import { PDFDocument } from "pdf-lib"

// Maps our form field IDs to the PDF's AcroForm field names
const AP13_FIELD_MAP: Record<string, string | string[]> = {
  // Page 1 — Warrant Information
  warrant_date: "Date26_af_date",
  warrant_amount: "Text2",
  warrant_number: "Text3",
  business_unit: "Text4",

  // Page 1 — Signature block
  phone: "Text9",
  name: ["Text10", "Print Name", "Text14", "NAME PayeeBusiness Name", "Text21"],
  business_name: "Text11",
  address: ["Text12", "Street Address", "Text15", "MAILING ADDRESS", "Text24"],
  email: "Text13",

  // Page 2 — Declaration
  city: ["City", "Text16"],
  state_zip: ["State and ZIP code", "Text17"],
  warrant_number_p2: ["Text22"],
  warrant_amount_p2: ["Text23"],
}

// Checkbox field IDs → PDF checkbox field names
const AP13_CHECKBOX_MAP: Record<string, { yes: string; no: string }> = {
  is_owner: { yes: "Check Box5", no: "Check Box6" },
  warrant_included: { yes: "Check Box7", no: "Check Box8" },
}

export function hasOverlayConfig(refundType: string): boolean {
  return refundType === "STALE_WARRANT"
}

export async function renderFilledPdf(
  refundType: string,
  formData: Record<string, unknown>,
  signatureDataUrl?: string,
): Promise<Uint8Array | null> {
  if (refundType !== "STALE_WARRANT") return null

  const pdfBytes = await fetch("/forms/ap13-affidavit.pdf").then((r) => r.arrayBuffer())
  const pdfDoc = await PDFDocument.load(pdfBytes)
  const form = pdfDoc.getForm()

  // Fill text fields
  for (const [formFieldId, pdfFieldNames] of Object.entries(AP13_FIELD_MAP)) {
    const value = formatFieldValue(formFieldId, formData[formFieldId])
    if (!value) continue

    const names = Array.isArray(pdfFieldNames) ? pdfFieldNames : [pdfFieldNames]
    for (const name of names) {
      try {
        const field = form.getTextField(name)
        field.setText(value)
      } catch {
        // field not found — skip
      }
    }
  }

  // Also fill warrant_number and warrant_amount into page 2 fields
  const wn = formatFieldValue("warrant_number", formData["warrant_number"])
  if (wn) {
    for (const name of AP13_FIELD_MAP.warrant_number_p2 as string[]) {
      try { form.getTextField(name).setText(wn) } catch {}
    }
  }
  const wa = formatFieldValue("warrant_amount", formData["warrant_amount"])
  if (wa) {
    for (const name of AP13_FIELD_MAP.warrant_amount_p2 as string[]) {
      try { form.getTextField(name).setText(wa) } catch {}
    }
  }

  // Fill checkboxes
  for (const [formFieldId, pdfNames] of Object.entries(AP13_CHECKBOX_MAP)) {
    const val = formData[formFieldId]
    const isYes = val === true || val === "true" || val === "Yes" || val === "yes"
    try {
      const checkbox = form.getCheckBox(isYes ? pdfNames.yes : pdfNames.no)
      checkbox.check()
    } catch {
      // checkbox not found — skip
    }
  }

  // Embed signature image into the signature field area
  if (signatureDataUrl) {
    try {
      const sigBytes = dataUrlToBytes(signatureDataUrl)
      const sigImage = await pdfDoc.embedPng(sigBytes)
      const pages = pdfDoc.getPages()
      const page = pages[0]
      // Signature field rect: [61.8, 225.6, 339.9, 254.4]
      const scaled = sigImage.scaleToFit(250, 28)
      page.drawImage(sigImage, {
        x: 65,
        y: 227,
        width: scaled.width,
        height: scaled.height,
      })
    } catch {
      // signature embed failed
    }
  }

  // Flatten so the form renders as static content
  form.flatten()

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
