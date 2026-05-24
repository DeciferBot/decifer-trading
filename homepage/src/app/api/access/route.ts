import { NextRequest, NextResponse } from "next/server";

const REQUIRED_FIELDS = ["name", "email", "investor_type", "interest"] as const;

export async function POST(req: NextRequest) {
  let body: Record<string, string>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid request body." }, { status: 400 });
  }

  // Honeypot — bots fill hidden fields; real users never do
  if (body.website) {
    return NextResponse.json({ ok: true });
  }

  for (const field of REQUIRED_FIELDS) {
    if (!body[field] || typeof body[field] !== "string" || !body[field].trim()) {
      return NextResponse.json({ error: `Missing required field: ${field}` }, { status: 400 });
    }
  }

  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(body.email)) {
    return NextResponse.json({ error: "Invalid email address." }, { status: 400 });
  }

  const submission = {
    name: body.name.trim(),
    email: body.email.trim().toLowerCase(),
    investor_type: body.investor_type.trim(),
    interest: body.interest.trim(),
    message: body.message?.trim() ?? "",
    submitted_at: new Date().toISOString(),
    source: "homepage-access-form",
  };

  const apiKey = process.env.RESEND_API_KEY;
  if (apiKey) {
    try {
      const res = await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          from: "DECIFER Trading <access@decifertrading.com>",
          to: ["chopraa@gmail.com"],
          reply_to: submission.email,
          subject: `DECIFER Trading — Early access request from ${submission.name}`,
          text: [
            `Name: ${submission.name}`,
            `Email: ${submission.email}`,
            `Investor type: ${submission.investor_type}`,
            `Interest: ${submission.interest}`,
            submission.message ? `Message: ${submission.message}` : "",
            `Submitted: ${submission.submitted_at}`,
          ].filter(Boolean).join("\n"),
        }),
      });

      if (!res.ok) {
        const err = await res.text();
        console.error("[access-form] Resend error:", res.status, err);
        return NextResponse.json({ error: "Failed to send. Please try again." }, { status: 500 });
      }
    } catch (err) {
      console.error("[access-form] Network error:", err);
      return NextResponse.json({ error: "Failed to send. Please try again." }, { status: 500 });
    }
  } else {
    // Graceful fallback — log submission; reviewable in Vercel function logs
    console.log("[access-form] RESEND_API_KEY not set. Submission received:", JSON.stringify(submission));
  }

  return NextResponse.json({ ok: true });
}
