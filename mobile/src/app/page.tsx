import { redirect } from "next/navigation";

// Root route redirects to customer Market Map.
// next.config.ts also issues a 307 at the routing layer — this is belt-and-suspenders.
export default function Page() {
  redirect("/customer");
}
