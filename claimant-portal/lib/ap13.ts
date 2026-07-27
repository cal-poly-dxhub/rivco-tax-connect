// Renders a filled AP-13 affidavit PDF for one warrant. Shared shape with the
// admin-dashboard overlay (admin-dashboard/lib/pdf-overlay.ts) — the claimant
// portal only needs the AP-13 path since notarization is the only reason a
// claimant has to print a form.

import { PDFDocument } from "pdf-lib";

export const AP13_TYPES = new Set(["STALE_WARRANT", "PAYROLL"]);

export function isAp13Type(refundType: string): boolean {
  return AP13_TYPES.has(refundType);
}

export type ClaimLike = Record<string, unknown> & { type: string };

export async function renderAp13Pdf(
  formData: Record<string, unknown>,
  claim: ClaimLike,
  signatureDataUrl?: string | null,
  submittedAt?: string,
): Promise<Uint8Array | null> {
  if (!isAp13Type(claim.type)) return null;

  // Per-claim fields override shared form data so the right warrant lands on
  // the right affidavit.
  const merged: Record<string, unknown> = { ...formData };
  for (const [k, v] of Object.entries(claim)) {
    if (k === "type") continue;
    if (v !== undefined && v !== null && v !== "") merged[k] = v;
  }

  const pdfBytes = await fetch("/forms/ap13-affidavit.pdf").then((r) => r.arrayBuffer());
  const pdfDoc = await PDFDocument.load(pdfBytes);
  const form = pdfDoc.getForm();

  const val = (id: string) => formatFieldValue(id, merged[id]);
  const parsedAddress = parseAddress(typeof merged.address === "string" ? merged.address : "");
  const submittedDateStr = submittedAt ? formatDate(submittedAt) : "";
  const warrantDate = val("warrant_date") || submittedDateStr;

  // Page 1
  setField(form, "Date26_af_date", warrantDate);
  setField(form, "Text2", val("warrant_amount"));
  setField(form, "Text3", val("warrant_number"));
  setField(form, "Text4", val("business_unit"));
  setField(form, "Text9", val("phone"));
  setField(form, "Text10", val("name"));
  setField(form, "Text11", val("business_name"));
  setField(form, "Text12", val("address"));
  setField(form, "Text13", val("email"));

  // Page 2 — Declaration
  setField(form, "Text14", val("name"));
  setField(form, "Text15", parsedAddress.street || val("address"));
  setField(form, "Text16", parsedAddress.city || val("city"));
  setField(form, "Text17", parsedAddress.stateZip || val("state_zip"));

  // Page 2 — Executed at
  setField(form, "Text18", val("exec_city") || parsedAddress.city);
  setField(form, "Text19", val("exec_state") || parsedAddress.state);
  setField(form, "Text20", val("exec_date") || submittedDateStr);

  // Page 2 — Name / Warrant / Amount + mailing address
  setField(form, "Text21", val("name"));
  setField(form, "Text22", val("warrant_number"));
  setField(form, "Text23", val("warrant_amount"));
  setField(form, "Text24", val("address"));

  const checkboxes: Record<string, { yes: string; no: string }> = {
    is_owner: { yes: "Check Box5", no: "Check Box6" },
    warrant_included: { yes: "Check Box7", no: "Check Box8" },
  };
  for (const [fieldId, names] of Object.entries(checkboxes)) {
    const v = merged[fieldId];
    if (v === undefined || v === null || v === "") continue;
    const isYes = v === true || v === "true" || v === "Yes" || v === "yes";
    const isNo = v === false || v === "false" || v === "No" || v === "no";
    if (!isYes && !isNo) continue;
    try {
      form.getCheckBox(isYes ? names.yes : names.no).check();
    } catch {
      // missing field
    }
  }

  if (signatureDataUrl) {
    try {
      const sigBytes = dataUrlToBytes(signatureDataUrl);
      const sigImage = await pdfDoc.embedPng(sigBytes);
      const pages = pdfDoc.getPages();
      const scaled = sigImage.scaleToFit(250, 28);
      // Page 1 — claimant signature on the affidavit body.
      pages[0].drawImage(sigImage, {
        x: 65,
        y: 227,
        width: scaled.width,
        height: scaled.height,
      });
      // Page 2 — second claimant signature (declaration block). Notary box
      // stays blank for the notary to sign in person.
      if (pages[1]) {
        const scaled2 = sigImage.scaleToFit(280, 18);
        pages[1].drawImage(sigImage, {
          x: 80,
          y: 425,
          width: scaled2.width,
          height: scaled2.height,
        });
      }
    } catch {
      // fall through — signature may be missing on the printed copy and the
      // claimant signs on paper before it goes to the notary.
    }
  }

  // Same dedupe list as admin-dashboard/lib/pdf-overlay.ts so empty boxes
  // don't render on top of the filled fields.
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
    "SIGNATURE PayeeBusiness Claimant", "SIGNATURE PayeeBusiness Claimant_2",
  ];
  for (const name of duplicateFields) {
    try { form.removeField(form.getField(name)); } catch {}
  }
  form.flatten();
  return pdfDoc.save();
}

function setField(form: ReturnType<PDFDocument["getForm"]>, name: string, value: string) {
  if (!value) return;
  try {
    form.getTextField(name).setText(value);
  } catch {
    // missing field
  }
}

function formatFieldValue(fieldId: string, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (fieldId.includes("amount")) return `$${value.toLocaleString()}`;
    return String(value);
  }
  return String(value);
}

function parseAddress(address: string): {
  street: string; city: string; state: string; zip: string; stateZip: string;
} {
  const blank = { street: "", city: "", state: "", zip: "", stateZip: "" };
  if (!address) return blank;
  const parts = address.split(",").map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return blank;
  const street = parts[0] || "";
  let city = "";
  let stateZipRaw = "";
  if (parts.length >= 3) {
    city = parts[1] || "";
    stateZipRaw = parts.slice(2).join(", ");
  } else if (parts.length === 2) {
    const tail = parts[1];
    const m = tail.match(/^(.+?)\s+([A-Za-z]{2})\s*(\d{5}(?:-\d{4})?)?$/);
    if (m) {
      city = m[1].trim();
      stateZipRaw = `${m[2]} ${m[3] ?? ""}`.trim();
    } else {
      city = tail;
    }
  } else {
    return { ...blank, street };
  }
  const m = stateZipRaw.match(/^([A-Za-z]{2})\s*(\d{5}(?:-\d{4})?)?$/);
  const state = m ? m[1].toUpperCase() : "";
  const zip = m ? (m[2] ?? "") : "";
  return { street, city, state, zip, stateZip: stateZipRaw };
}

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${mm}/${dd}/${d.getFullYear()}`;
}

function dataUrlToBytes(dataUrl: string): Uint8Array {
  const base64 = dataUrl.split(",")[1];
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
