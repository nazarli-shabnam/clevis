// Liveness for the compose healthcheck -- deliberately doesn't call the API or DB, so a
// downstream outage can't mark the UI container unhealthy.
export function GET() {
  return new Response("ok", { status: 200 })
}
