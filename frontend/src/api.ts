import type { AskResponse } from "./types";

/**
 * POST a question to the backend. Returns the parsed AskResponse, or throws an
 * Error whose message is already user-friendly (mirrors the error mapping from
 * the original static frontend: 429 / 400 / 502 / network).
 */
export async function askBackend(
  question: string,
  conversationId: number | null,
): Promise<AskResponse> {
  let res: Response;
  try {
    res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: conversationId }),
    });
  } catch {
    throw new Error(
      "Couldn't reach the server. Check that the API is running, then try again.",
    );
  }

  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  return (await res.json()) as AskResponse;
}

async function errorMessage(res: Response): Promise<string> {
  if (res.status === 429) {
    return "You're asking a little fast — give it a minute and try again.";
  }
  let detail = "";
  try {
    detail = ((await res.json()) as { detail?: string }).detail || "";
  } catch {
    /* body wasn't JSON */
  }
  if (res.status === 400 && detail) return detail;
  if (res.status === 502) {
    return "The projection service is briefly unavailable. Try again in a moment.";
  }
  return detail || "Something went wrong on the server. Please try again.";
}
