// Liveness endpoint for the docker-compose healthcheck -- deliberately does not call the
// API or DB, so it can't report "unhealthy" due to a downstream outage the UI container
// itself isn't responsible for. It only confirms the Next.js server is up and responding.
export function GET() {
  return new Response("ok", { status: 200 })
}
