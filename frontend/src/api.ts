import type {
  AskResponse,
  ConversationHistoryMessage,
  ConversationSummary,
  DeepAnalysisJobRef,
  DeepAnalysisJobResponse,
  DeepAnalysisJobSummary,
} from "./types";

/**
 * POST a question to the backend. Returns the parsed AskResponse, or throws an
 * Error whose message is already user-friendly (mirrors the error mapping from
 * the original static frontend: 401 / 429 / 400 / 502 / network).
 *
 * `idToken` is the Google ID token from sign-in (see App.tsx) — sent as a
 * Bearer token, which is how the backend's get_current_user_id verifies who's
 * actually calling. There's no "logged out" mode for asking questions: the
 * backend requires a valid token on every /ask call, so the caller must not
 * invoke this without one (App.tsx gates the composer on being signed in).
 */
export async function askBackend(
  question: string,
  conversationId: number | null,
  idToken: string,
): Promise<AskResponse> {
  let res: Response;
  try {
    res = await fetch("/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
      },
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

/** GET the signed-in user's conversation list, for the sidebar. */
export async function fetchConversations(idToken: string): Promise<ConversationSummary[]> {
  const res = await fetch("/conversations", {
    headers: { Authorization: `Bearer ${idToken}` },
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as ConversationSummary[];
}

/** GET one past conversation's message history, for switching to it. */
export async function fetchConversationHistory(
  conversationId: number,
  idToken: string,
): Promise<ConversationHistoryMessage[]> {
  const res = await fetch(`/conversations/${conversationId}`, {
    headers: { Authorization: `Bearer ${idToken}` },
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  const data = (await res.json()) as { messages: ConversationHistoryMessage[] };
  return data.messages;
}

/** POST a request for a deep analysis report for a player */
export async function requestDeepAnalysis(
  playerName: string,
  idempotencyKey: string,
  idToken: string,
): Promise<DeepAnalysisJobRef> {
  let res: Response;
  try {
    res = await fetch("/deep-analysis", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
        "idempotency-key": idempotencyKey,
      },
      body: JSON.stringify({ player_name: playerName }),
    });
  } catch {
    throw new Error(
      "Couldn't reach the server. Check that the API is running, then try again.",
    );
  }

  if (!res.ok) {
    throw new Error(await errorMessage(res));
  }
  return (await res.json()) as DeepAnalysisJobRef;
}

/** GET the signed-in user's past deep-analysis reports, for the sidebar. */
export async function fetchDeepAnalysisJobs(idToken: string): Promise<DeepAnalysisJobSummary[]> {
  const res = await fetch("/deep-analysis", {
    headers: { Authorization: `Bearer ${idToken}` },
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as DeepAnalysisJobSummary[];
}

/** GET the status for a deep analysis job and results if finished */
export async function fetchDeepAnalysisStatus(
  jobId: number,
  idToken: string,
): Promise<DeepAnalysisJobResponse> {
  const res = await fetch(`/deep-analysis/${jobId}`, {
    headers: { Authorization: `Bearer ${idToken}` },
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (await res.json()) as DeepAnalysisJobResponse;
}

async function errorMessage(res: Response): Promise<string> {
  if (res.status === 401) {
    return "Your sign-in has expired. Please sign in again.";
  }
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
