import { useEffect, useRef, useState } from "react";
import type { CredentialResponse } from "@react-oauth/google";
import type {
  AskResponse,
  ConversationSummary,
  DeepAnalysisJobResponse,
  DeepAnalysisJobSummary,
} from "./types";
import {
  askBackend,
  fetchConversationHistory,
  fetchConversations,
  fetchDeepAnalysisJobs,
  fetchDeepAnalysisStatus,
  requestDeepAnalysis,
} from "./api";
import Topbar from "./components/Topbar";
import Hero from "./components/Hero";
import Sidebar from "./components/Sidebar";
import MessageStream, { type Message } from "./components/MessageStream";
import ThinkingIndicator from "./components/ThinkingIndicator";
import Composer from "./components/Composer";
import RequestDeepAnalysisModal from "./components/RequestDeepAnalysisModal";
import DeepAnalysisReportModal from "./components/DeepAnalysisReportModal";
import NotificationStack, { type DeepAnalysisNotification } from "./components/NotificationStack";
import styles from "./App.module.css";

const POLL_INTERVAL_MS = 3000;
const NOTIFICATION_TTL_MS = 8000;

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // The Google ID token from sign-in — null means signed out. Deliberately
  // kept in memory only (not localStorage): it's short-lived anyway (~1hr),
  // and this avoids exposing it to any XSS that might read localStorage.
  // Tradeoff: a page refresh signs you out. Revisit if that's annoying in
  // practice — but don't add persistence pre-emptively for a problem you
  // haven't confirmed matters yet.
  const [idToken, setIdToken] = useState<string | null>(null);

  // The signed-in user's past conversations, for the sidebar.
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);

  // The signed-in user's past deep-analysis reports, for the sidebar.
  const [deepAnalysisJobs, setDeepAnalysisJobs] = useState<DeepAnalysisJobSummary[]>([]);
  const [requestModalOpen, setRequestModalOpen] = useState(false);
  const [requestPending, setRequestPending] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  // The report currently open in DeepAnalysisReportModal, or null if closed.
  const [openReport, setOpenReport] = useState<{ jobId: number; playerName: string } | null>(
    null,
  );

  // Every deep-analysis job still being tracked (not yet done/failed) — kept
  // here rather than inside DeepAnalysisReportModal, since a freshly
  // submitted job needs to keep being polled for notification purposes even
  // after its modal closes. `notify` distinguishes that case from merely
  // opening an existing, still-pending report from history to check on it —
  // the latter should stay live only while its modal is actually open, not
  // commit to indefinite background polling for something the user was just
  // glancing at (see closeReport/selectReport below).
  const [pollingJobs, setPollingJobs] = useState<
    { jobId: number; playerName: string; notify: boolean }[]
  >([]);
  // Latest known status/error per job id, fed to DeepAnalysisReportModal as
  // props — the modal itself no longer fetches anything.
  const [jobStates, setJobStates] = useState<
    Record<number, { status: DeepAnalysisJobResponse | null; error: string | null }>
  >({});
  const [notifications, setNotifications] = useState<DeepAnalysisNotification[]>([]);

  // Keep the newest message in view as the conversation grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  // Load the sidebar lists once, right after sign-in.
  useEffect(() => {
    if (idToken) {
      void refreshConversations(idToken);
      void refreshDeepAnalysisJobs(idToken);
    }
  }, [idToken]);

  async function refreshConversations(token: string) {
    try {
      setConversations(await fetchConversations(token));
    } catch (err) {
      // A stale sidebar isn't worth surfacing as a user-facing error — the
      // chat itself still works fine either way — but DO log it, so a real
      // bug here doesn't just disappear silently.
      console.error("Failed to refresh conversation list:", err);
    }
  }

  async function refreshDeepAnalysisJobs(token: string) {
    try {
      setDeepAnalysisJobs(await fetchDeepAnalysisJobs(token));
    } catch (err) {
      console.error("Failed to refresh deep-analysis report list:", err);
    }
  }

  // Single fetch for one job's current status. Used both for the initial
  // check (right after submitting, or right after opening one from history)
  // and for each tick of the recurring poll effect below. Registers the job
  // for continued polling if it isn't done yet; fires a toast the moment it
  // reaches a terminal status, but only if `notify` is true — a job merely
  // opened from history (see selectReport) still gets its status refreshed
  // here, it just doesn't get a completion toast if the user isn't around
  // to see it finish.
  async function pollJob(jobId: number, playerName: string, token: string, notify: boolean) {
    try {
      const result = await fetchDeepAnalysisStatus(jobId, token);
      setJobStates((prev) => ({ ...prev, [jobId]: { status: result, error: null } }));

      if (result.status === "done" || result.status === "failed") {
        setPollingJobs((prev) => prev.filter((j) => j.jobId !== jobId));
        if (notify) {
          const notificationId = crypto.randomUUID();
          setNotifications((prev) => [
            ...prev,
            { id: notificationId, jobId, playerName, status: result.status as "done" | "failed" },
          ]);
          setTimeout(() => dismissNotification(notificationId), NOTIFICATION_TTL_MS);
        }
        void refreshDeepAnalysisJobs(token);
      } else {
        setPollingJobs((prev) =>
          prev.some((j) => j.jobId === jobId) ? prev : [...prev, { jobId, playerName, notify }],
        );
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Couldn't load this report.";
      setJobStates((prev) => ({
        ...prev,
        [jobId]: { status: prev[jobId]?.status ?? null, error: message },
      }));
    }
  }

  // The one recurring poll loop for every currently-tracked job — re-runs
  // whenever a job gets added to or removed from pollingJobs.
  useEffect(() => {
    if (!idToken || pollingJobs.length === 0) return;
    const token = idToken;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    // One list call per tick, regardless of how many jobs are tracked — the
    // point is to keep total request volume constant instead of scaling
    // with the number of concurrent in-flight jobs (N separate per-job
    // polls would multiply the rate-limit budget by N). The list only
    // carries `status`, not `result`/`error`, so a job that just reached a
    // terminal status still gets one individual fetch via pollJob — but
    // only once, right when it finishes, not on every tick.
    async function tick() {
      try {
        const jobs = await fetchDeepAnalysisJobs(token);
        const byId = new Map(jobs.map((j) => [j.id, j]));
        for (const { jobId, playerName, notify } of pollingJobs) {
          if (cancelled) return;
          const summary = byId.get(jobId);
          if (!summary) continue;
          if (summary.status === "done" || summary.status === "failed") {
            await pollJob(jobId, playerName, token, notify);
          } else {
            setJobStates((prev) => ({
              ...prev,
              [jobId]: {
                status: {
                  status: summary.status,
                  result: null,
                  error: null,
                  produced: summary.produced,
                  created_at: summary.created_at,
                },
                error: null,
              },
            }));
          }
        }
      } catch (err) {
        console.error("Failed to poll deep-analysis job list:", err);
      }
      if (!cancelled) timer = setTimeout(tick, POLL_INTERVAL_MS);
    }

    // The first check for a newly-added job already happened at the point
    // it was submitted/selected (see handleRequestDeepAnalysis/selectReport)
    // — so this only needs to schedule the next one, not fetch immediately.
    timer = setTimeout(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [pollingJobs, idToken]);

  function dismissNotification(id: string) {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }

  function openNotification(n: DeepAnalysisNotification) {
    // A submit-time failure never got a job id at all — nothing to open.
    if (n.jobId !== null) setOpenReport({ jobId: n.jobId, playerName: n.playerName });
    dismissNotification(n.id);
  }

  function handleSignIn(credentialResponse: CredentialResponse) {
    if (credentialResponse.credential) setIdToken(credentialResponse.credential);
  }

  function handleSignOut() {
    setIdToken(null);
    // A signed-out session shouldn't keep showing another identity's chat.
    setConversationId(null);
    setMessages([]);
    setConversations([]);
    setDeepAnalysisJobs([]);
    setRequestModalOpen(false);
    setOpenReport(null);
    setPollingJobs([]);
    setJobStates({});
    setNotifications([]);
  }

  function openDeepAnalysisRequest() {
    setRequestError(null);
    setRequestModalOpen(true);
  }

  function closeDeepAnalysisRequest() {
    // Letting the user close out even while the request is still in flight —
    // the underlying submission keeps running in the background regardless
    // (see handleRequestDeepAnalysis's finally block), it just won't have
    // anywhere to show an error if it later fails after being dismissed.
    setRequestModalOpen(false);
    setRequestError(null);
  }

  async function handleRequestDeepAnalysis(playerName: string) {
    if (!idToken) return;
    setRequestPending(true);
    setRequestError(null);
    try {
      // A fresh key per submission — this is a one-shot trigger from a
      // button click, not a retried network request, so there's no earlier
      // key to reuse (contrast with a client that retries the same logical
      // request after a failure, which would need to hold onto one key
      // across attempts).
      const idempotencyKey = crypto.randomUUID();
      const { job_id } = await requestDeepAnalysis(playerName, idempotencyKey, idToken);
      setRequestModalOpen(false);
      setOpenReport({ jobId: job_id, playerName });
      // notify: true — this job was just submitted by the user, so it stays
      // tracked (and gets a completion toast) even after this modal closes.
      void pollJob(job_id, playerName, idToken, true);
      void refreshDeepAnalysisJobs(idToken);
    } catch (err) {
      setRequestError(err instanceof Error ? err.message : "Something went wrong.");
      // A submit-time failure may still have written/updated a row (e.g. the
      // produce-failure path marks it `failed`) — refresh so the sidebar
      // reflects that immediately instead of only after a manual reload.
      void refreshDeepAnalysisJobs(idToken);
      // No job id was ever obtained here, so there's nothing to poll — this
      // is the one notification not driven by pollJob, since otherwise a
      // user who closes this modal before a slow failure resolves (see
      // closeDeepAnalysisRequest) would get no feedback at all that it
      // failed.
      const notificationId = crypto.randomUUID();
      setNotifications((prev) => [
        ...prev,
        { id: notificationId, jobId: null, playerName, status: "failed" },
      ]);
      setTimeout(() => dismissNotification(notificationId), NOTIFICATION_TTL_MS);
    } finally {
      setRequestPending(false);
    }
  }

  function selectReport(jobId: number, playerName: string) {
    setOpenReport({ jobId, playerName });
    // notify: false — opening an existing report from history is just
    // checking on it, not an action that should commit to background
    // tracking/notifications after the modal closes (see closeReport). It
    // still gets refreshed here even if we already have a stale copy from
    // an earlier poll (e.g. reopening a report that's since finished), and
    // it'll keep getting refreshed on every tick for as long as this modal
    // stays open.
    if (idToken) void pollJob(jobId, playerName, idToken, false);
  }

  function closeReport() {
    // A job that was only being tracked because its modal was open (not
    // because the user just submitted it — see selectReport) has no reason
    // to keep polling once that modal closes.
    if (openReport) {
      const closedJobId = openReport.jobId;
      setPollingJobs((prev) => prev.filter((j) => !(j.jobId === closedJobId && !j.notify)));
    }
    setOpenReport(null);
  }

  async function submit(textArg?: string) {
    const text = (textArg ?? input).trim();
    if (!text || pending || !idToken) return;

    const isNewConversation = conversationId === null;
    setMessages((m) => [...m, { kind: "user", text }]);
    setInput("");
    setPending(true);

    try {
      const data: AskResponse = await askBackend(text, conversationId, idToken);
      setConversationId(data.conversation_id);
      setMessages((m) => [...m, { kind: "assistant", data }]);
      // A new conversation gets its title generated server-side as part of
      // this same call (see agent.py) — refetch so the sidebar picks it up.
      if (isNewConversation) void refreshConversations(idToken);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Something went wrong.";
      setMessages((m) => [...m, { kind: "error", text: msg }]);
      // Restore the question so nothing is lost on a failed turn.
      setInput((cur) => (cur.trim() === "" ? text : cur));
    } finally {
      setPending(false);
    }
  }

  async function selectConversation(id: number) {
    if (!idToken || id === conversationId || pending) return;
    setPending(true);
    try {
      const history = await fetchConversationHistory(id, idToken);
      const loaded: Message[] = history.map((m) =>
        m.role === "user"
          ? { kind: "user", text: m.content }
          : {
              kind: "assistant",
              data: {
                answer: m.content,
                conversation_id: id,
                projections: m.projections,
                news: m.news,
              },
            },
      );
      setConversationId(id);
      setMessages(loaded);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Couldn't load that conversation.";
      setMessages([{ kind: "error", text: msg }]);
    } finally {
      setPending(false);
    }
  }

  function newChat() {
    setConversationId(null);
    setMessages([]);
    setInput("");
  }

  return (
    <div className={styles.appRow}>
      {idToken !== null && (
        <Sidebar
          conversations={conversations}
          activeId={conversationId}
          onSelect={selectConversation}
          reports={deepAnalysisJobs}
          onSelectReport={selectReport}
        />
      )}

      <div className={styles.app}>
        <Topbar
          onNewChat={newChat}
          onDeepAnalysis={openDeepAnalysisRequest}
          signedIn={idToken !== null}
          onSignIn={handleSignIn}
          onSignOut={handleSignOut}
        />

        <div className={styles.scroll} ref={scrollRef}>
          <div className={styles.col}>
            {idToken === null ? (
              <Hero onPick={() => {}} onDeepAnalysis={() => {}} signedOut onSignIn={handleSignIn} />
            ) : messages.length === 0 ? (
              <Hero onPick={(t) => submit(t)} onDeepAnalysis={openDeepAnalysisRequest} />
            ) : (
              <MessageStream messages={messages} />
            )}
            {pending && <ThinkingIndicator />}
          </div>
        </div>

        <Composer
          value={input}
          onChange={setInput}
          onSubmit={submit}
          disabled={pending || idToken === null}
        />
      </div>

      {requestModalOpen && (
        <RequestDeepAnalysisModal
          onClose={closeDeepAnalysisRequest}
          onSubmit={handleRequestDeepAnalysis}
          pending={requestPending}
          error={requestError}
        />
      )}

      {openReport && (
        <DeepAnalysisReportModal
          playerName={openReport.playerName}
          status={jobStates[openReport.jobId]?.status ?? null}
          error={jobStates[openReport.jobId]?.error ?? null}
          onClose={closeReport}
        />
      )}

      <NotificationStack
        notifications={notifications}
        onOpen={openNotification}
        onDismiss={dismissNotification}
      />
    </div>
  );
}
