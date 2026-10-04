// Routes reachable without a session. AuthGuard skips its login redirect for these and
// ShellRouter skips the app shell (sidebar fires authenticated requests), so both must agree.
const PUBLIC_ROUTES = ["/login", "/setup", "/register", "/verify-email"]
const PUBLIC_ROUTE_PREFIXES = ["/invite/"]

export function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.includes(pathname) || PUBLIC_ROUTE_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}
