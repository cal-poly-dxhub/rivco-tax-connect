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

// STALE_WARRANT and PAYROLL both use the AP-13 affidavit.
const AP13_REFUND_TYPES = new Set(["STALE_WARRANT", "PAYROLL"])

export function hasOverlayConfig(refundType: string): boolean {
  return AP13_REFUND_TYPES.has(refundType)
}

export type FilledPdfResult = {
  bytes: Uint8Array
  // True when a signature was provided but pdf-lib couldn't embed it. The
  // signed payload is still in unified-form.json — this only affects the
  // admin's visual preview.
  signatureMissing: boolean
}

export async function renderFilledPdf(
  refundType: string,
  formData: Record<string, unknown>,
  signatureDataUrl?: string,
  submittedAt?: string,
): Promise<FilledPdfResult | null> {
  if (!AP13_REFUND_TYPES.has(refundType)) return null
  let signatureMissing = false

  const pdfBytes = await fetch("/forms/ap13-affidavit.pdf").then((r) => r.arrayBuffer())
  const pdfDoc = await PDFDocument.load(pdfBytes)
  const form = pdfDoc.getForm()

  const val = (id: string) => formatFieldValue(id, formData[id])

  // Address comes in as a single mailing-address string. The PDF wants three
  // separate fields on page 2 (street / city / state+zip) plus the "executed
  // at" line (city / state). Parse once and reuse.
  const parsedAddress = parseAddress(typeof formData.address === "string" ? formData.address : "")

  // The PDF date field at the top of page 1 is a "warrant date" slot, but
  // there's a separate "Date" field on page 2 (next to "executed at City").
  // Fall back to the submittedAt timestamp when no explicit warrant_date
  // was provided so the form isn't blank.
  const submittedDateStr = submittedAt ? formatDate(submittedAt) : ""
  const warrantDate = val("warrant_date") || submittedDateStr

  // Page 1 fields
  setField(form, "Date26_af_date", warrantDate)
  setField(form, "Text2", val("warrant_amount"))
  setField(form, "Text3", val("warrant_number"))
  setField(form, "Text4", val("business_unit"))
  setField(form, "Text9", val("phone"))
  setField(form, "Text10", val("name"))
  setField(form, "Text11", val("business_name"))
  setField(form, "Text12", val("address"))
  setField(form, "Text13", val("email"))

  // Page 2 — Declaration header (street / city / state+zip parsed out)
  setField(form, "Text14", val("name"))
  setField(form, "Text15", parsedAddress.street || val("address"))
  setField(form, "Text16", parsedAddress.city || val("city"))
  setField(form, "Text17", parsedAddress.stateZip || val("state_zip"))

  // Page 2 — Executed at row (date defaults to submittedAt)
  setField(form, "Text18", val("exec_city") || parsedAddress.city)
  setField(form, "Text19", val("exec_state") || parsedAddress.state)
  setField(form, "Text20", val("exec_date") || submittedDateStr)

  // Page 2 — NAME / Warrant / Amount row
  setField(form, "Text21", val("name"))
  setField(form, "Text22", val("warrant_number"))
  setField(form, "Text23", val("warrant_amount"))

  // Page 2 — Mailing address
  setField(form, "Text24", val("address"))

  // Checkboxes — only mark a box when the claimant gave an explicit answer.
  // Treating undefined as "No" would misrepresent unanswered questions.
  for (const [formFieldId, pdfNames] of Object.entries(AP13_CHECKBOX_MAP)) {
    const v = formData[formFieldId]
    if (v === undefined || v === null || v === "") continue
    const isYes = v === true || v === "true" || v === "Yes" || v === "yes"
    const isNo = v === false || v === "false" || v === "No" || v === "no"
    if (!isYes && !isNo) continue
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
      signatureMissing = true
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
  return { bytes: await pdfDoc.save(), signatureMissing }
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

/**
 * Split a mailing-address string into PDF-ready parts. The form takes
 * "street", "city", and "state ZIP" as three separate fields on page 2.
 *
 * Handles common shapes:
 *   "789 Mission Blvd, San Diego, CA 92154"
 *   "789 Mission Blvd, San Diego CA 92154"
 *   "789 Mission Blvd"           (street only — city/state/zip empty)
 */
function parseAddress(address: string): {
  street: string
  city: string
  state: string
  zip: string
  stateZip: string
} {
  const blank = { street: "", city: "", state: "", zip: "", stateZip: "" }
  if (!address) return blank
  // Comma-separated is the common shape; fall back to whitespace splitting if
  // the user typed it that way.
  const parts = address.split(",").map((p) => p.trim()).filter(Boolean)
  if (parts.length === 0) return blank

  const street = parts[0] || ""
  let city = ""
  let stateZipRaw = ""

  if (parts.length >= 3) {
    // street, city, "STATE ZIP"
    city = parts[1] || ""
    stateZipRaw = parts.slice(2).join(", ")
  } else if (parts.length === 2) {
    // street, "city STATE ZIP" — split off the trailing state+zip
    const tail = parts[1]
    const m = tail.match(/^(.+?)\s+([A-Za-z]{2})\s*(\d{5}(?:-\d{4})?)?$/)
    if (m) {
      city = m[1].trim()
      stateZipRaw = `${m[2]} ${m[3] ?? ""}`.trim()
    } else {
      city = tail
    }
  } else {
    // street only
    return { ...blank, street }
  }

  // Pull state + zip out of "CA 92154" / "CA 92154-0001"
  const m = stateZipRaw.match(/^([A-Za-z]{2})\s*(\d{5}(?:-\d{4})?)?$/)
  const state = m ? m[1].toUpperCase() : ""
  const zip = m ? (m[2] ?? "") : ""

  return { street, city, state, zip, stateZip: stateZipRaw }
}

/** Format an ISO timestamp as "MM/DD/YYYY" for the PDF date fields. */
function formatDate(iso: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  const mm = String(d.getMonth() + 1).padStart(2, "0")
  const dd = String(d.getDate()).padStart(2, "0")
  return `${mm}/${dd}/${d.getFullYear()}`
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
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
  const f = (id: string) => escapeHtml(String(formData[id] ?? ""))
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
