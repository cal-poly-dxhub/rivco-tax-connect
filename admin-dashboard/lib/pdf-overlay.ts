import { PDFDocument } from "pdf-lib"

// Maps our form field IDs to the PDF's AcroForm field names.
// The PDF has duplicate overlapping fields (named + TextN); use only one per slot.
const AP13_FIELD_MAP: Record<string, string | string[]> = {
  // Page 1 — Warrant Information
  warrant_date: "Date26_af_date",
  warrant_amount: "Text2",
  warrant_number: "Text3",
  business_unit: "Text4",

  // Page 1 — Signature block
  phone: "Text9",
  name: "Text10",
  business_name: "Text11",
  address: "Text12",
  email: "Text13",

  // Page 2 — Declaration (Print Name / Street Address row)
  name_p2: "Text14",
  address_p2: "Text15",
  city: "Text16",
  state_zip: "Text17",

  // Page 2 — Executed at row
  exec_city: "Text18",
  exec_state: "Text19",
  exec_date: "Text20",

  // Page 2 — NAME / Warrant Number / Warrant Amount row
  name_p2_lower: "Text21",
  warrant_number_p2: "Text22",
  warrant_amount_p2: "Text23",

  // Page 2 — Mailing address
  address_p2_lower: "Text24",
}

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

  const val = (id: string) => formatFieldValue(id, formData[id])

  // Page 1 fields
  setField(form, "Date26_af_date", val("warrant_date"))
  setField(form, "Text2", val("warrant_amount"))
  setField(form, "Text3", val("warrant_number"))
  setField(form, "Text4", val("business_unit"))
  setField(form, "Text9", val("phone"))
  setField(form, "Text10", val("name"))
  setField(form, "Text11", val("business_name"))
  setField(form, "Text12", val("address"))
  setField(form, "Text13", val("email"))

  // Page 2 — Declaration header
  setField(form, "Text14", val("name"))
  setField(form, "Text15", val("address"))
  setField(form, "Text16", val("city"))
  setField(form, "Text17", val("state_zip"))

  // Page 2 — NAME / Warrant / Amount row
  setField(form, "Text21", val("name"))
  setField(form, "Text22", val("warrant_number"))
  setField(form, "Text23", val("warrant_amount"))

  // Page 2 — Mailing address
  setField(form, "Text24", val("address"))

  // Checkboxes
  for (const [formFieldId, pdfNames] of Object.entries(AP13_CHECKBOX_MAP)) {
    const v = formData[formFieldId]
    const isYes = v === true || v === "true" || v === "Yes" || v === "yes"
    try {
      const checkbox = form.getCheckBox(isYes ? pdfNames.yes : pdfNames.no)
      checkbox.check()
    } catch {
      // field not found
    }
  }

  // Embed signature
  if (signatureDataUrl) {
    try {
      const sigBytes = dataUrlToBytes(signatureDataUrl)
      const sigImage = await pdfDoc.embedPng(sigBytes)
      const pages = pdfDoc.getPages()
      const page = pages[0]
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

  // Remove the duplicate named fields so they don't render empty boxes
  const duplicateFields = [
    "Print Name", "Street Address", "City", "State and ZIP code",
    "City_2", "State", "Date", "NAME PayeeBusiness Name",
    "MAILING ADDRESS", "Date25_af_date",
    "WARRANT INFORMATIONRow1_2", "WARRANT INFORMATIONRow1_3",
    "WARRANT INFORMATIONRow1_4", "WARRANT INFORMATIONRow1_5",
    "WARRANT INFORMATIONRow1_6", "WARRANT INFORMATIONRow1_7",
    "Yes", "No",
    "Provide all information An incomplete form will be returned",
    "PRINTED NAME Payee Business Name", "undefined",
    "AFFIDAVIT FOR THE REPLACEMENT OF STALE DATED WARRANT OFFICE OF THE AUDITORCONTROLLER",
    "AP  13 Policy  214 Page 2 of 4",
  ]
  for (const name of duplicateFields) {
    try { form.removeField(form.getField(name)) } catch {}
  }

  form.flatten()
  return pdfDoc.save()
}

function setField(form: ReturnType<PDFDocument["getForm"]>, name: string, value: string) {
  if (!value) return
  try {
    form.getTextField(name).setText(value)
  } catch {
    // field not found
  }
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
