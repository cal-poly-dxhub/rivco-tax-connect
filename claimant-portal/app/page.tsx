import Link from "next/link";

export default function LandingPage() {
  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center px-4 py-16"
      style={{ background: "var(--bg)" }}
    >
      {/* Header */}
      <div className="w-full max-w-lg text-center mb-10">
        <div
          className="inline-flex items-center gap-4 mb-6 px-6 py-4"
          style={{ background: "var(--navy)" }}
        >
          <div className="text-left">
            <div
              className="text-lg font-bold uppercase tracking-wide text-white"
              style={{ fontFamily: "Montserrat, sans-serif" }}
            >
              County of Riverside
            </div>
            <div className="text-xs text-gray-300 uppercase tracking-widest">
              Office of the Auditor-Controller
            </div>
          </div>
        </div>

        <h1
          className="text-2xl font-bold uppercase tracking-wide mb-2"
          style={{ fontFamily: "Montserrat, sans-serif", color: "var(--navy)" }}
        >
          Unclaimed Refund Claim Portal
        </h1>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          Submit a new claim or check the status of an existing one.
        </p>
      </div>

      {/* Cards */}
      <div className="w-full max-w-lg grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Link
          href="/new"
          className="flex flex-col items-center justify-center gap-3 p-8 border-2 text-center"
          style={{
            background: "var(--surface)",
            borderColor: "var(--navy)",
            color: "var(--navy)",
          }}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="w-8 h-8"
            aria-hidden="true"
          >
            <path
              d="M12 5v14M5 12h14"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span
            className="font-bold uppercase tracking-wide text-sm"
            style={{ fontFamily: "Montserrat, sans-serif" }}
          >
            Start a new claim
          </span>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Begin the process for an unclaimed refund
          </span>
        </Link>

        <Link
          href="/my-claim"
          className="flex flex-col items-center justify-center gap-3 p-8 border-2 text-center"
          style={{
            background: "var(--yellow)",
            borderColor: "var(--navy)",
            color: "var(--navy-dark)",
          }}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="w-8 h-8"
            aria-hidden="true"
          >
            <circle cx={11} cy={11} r={8} />
            <path
              d="m21 21-4.35-4.35"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span
            className="font-bold uppercase tracking-wide text-sm"
            style={{ fontFamily: "Montserrat, sans-serif" }}
          >
            Check an existing claim
          </span>
          <span className="text-xs" style={{ color: "var(--navy)" }}>
            Look up your claim status by ID
          </span>
        </Link>
      </div>

      <footer
        className="mt-16 text-center text-xs leading-relaxed"
        style={{ color: "var(--text-muted)" }}
      >
        County of Riverside · Office of the Auditor-Controller
        <br />
        4080 Lemon Street, 6th Floor · P.O. Box 1326 · Riverside, CA 92502-1326
      </footer>
    </div>
  );
}
