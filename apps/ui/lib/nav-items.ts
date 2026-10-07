/** The sidebar's routes, shared with the command palette so the two cannot drift apart. */
export const NAV_GROUPS = [
  [
    { title: "Overview",         href: "/" },
    { title: "Activity",         href: "/activity", showUnreadBadge: true },
    { title: "Pull Requests",    href: "/pulls" },
    { title: "Releases",         href: "/releases" },
  ],
  [
    { title: "Repositories",     href: "/repos" },
    { title: "Health & Security",href: "/security", showHealthDot: true },
  ],
  [
    // "/collaborators" is a sentinel, not a real route: the render loop swaps in membersNavHref.
    { title: "Collaborators",    href: "/collaborators" },
    { title: "Automation",       href: "/automation" },
    { title: "Audit Log",        href: "/audit" },
  ],
  [
    { title: "My Work",    href: "/my" },
  ],
]
