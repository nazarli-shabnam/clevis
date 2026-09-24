import { redirect } from "next/navigation"

// Redirect to the tabbed /my page so existing links and bookmarks still land on the right tab.
export default function MyReviewsRedirect() {
  redirect("/my?tab=reviews")
}
