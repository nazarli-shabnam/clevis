"use client"

// Only boundary that covers a root-layout crash. It replaces the root layout, so it renders its own
// <html>/<body> and gets no globals.css, fonts, or providers -- hence the inline styles.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "1rem",
          background: "#0a0a0b",
          color: "#e5e5e5",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        }}
      >
        <div style={{ maxWidth: "28rem", width: "100%", textAlign: "center" }}>
          <p style={{ fontSize: "0.875rem", fontWeight: 500, margin: "0 0 0.5rem" }}>
            Something went wrong
          </p>
          <p style={{ fontSize: "0.875rem", color: "#a1a1aa", margin: "0 0 1rem" }}>
            The app failed to load. Try again, or reload the page if it persists.
          </p>
          {error.digest && (
            <p
              style={{
                fontSize: "0.75rem",
                color: "#71717a",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                margin: "0 0 1rem",
              }}
            >
              Reference: {error.digest}
            </p>
          )}
          <button
            onClick={reset}
            style={{
              fontSize: "0.8125rem",
              padding: "0.375rem 0.875rem",
              borderRadius: "0.375rem",
              border: "1px solid #3f3f46",
              background: "transparent",
              color: "inherit",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  )
}
