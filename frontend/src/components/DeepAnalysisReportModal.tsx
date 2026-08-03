import type { DeepAnalysisJobResponse } from "../types";
import Modal from "./Modal";
import Markdown from "./Markdown";
import styles from "./DeepAnalysisReportModal.module.css";

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  fetching: "Fetching season stats",
  simulating: "Running projections",
  summarizing: "Writing report",
};

/**
 * Purely presentational — shows whatever status App.tsx currently has for
 * this job. Polling lives in App.tsx (not here), since a job needs to keep
 * being tracked for notification purposes even after this modal closes;
 * keeping a second, independent poll loop in here would double the request
 * rate against GET /deep-analysis/{job_id} whenever this modal is open.
 */
export default function DeepAnalysisReportModal({
  playerName,
  status,
  error,
  onClose,
}: {
  playerName: string;
  status: DeepAnalysisJobResponse | null;
  error: string | null;
  onClose: () => void;
}) {
  const waitingOnService = status?.status === "queued" && !status.produced;

  return (
    <Modal onClose={onClose} ariaLabel={`Deep analysis report for ${playerName}`} size="lg">
      <h2 className={styles.title}>{playerName}</h2>
      <div className={styles.eyebrow}>Deep analysis report</div>

      {error ? (
        <div className={styles.error}>{error}</div>
      ) : status?.status === "failed" ? (
        <div className={styles.error}>
          {status.error ?? "This report failed to generate. Please try again."}
        </div>
      ) : status?.status === "done" ? (
        <div className={styles.report}>
          <Markdown>{status.result ?? ""}</Markdown>
        </div>
      ) : (
        <div className={styles.pending}>
          <div className={styles.pendingLine}>
            <span className={styles.ball} />
            {status === null
              ? "Loading…"
              : waitingOnService
                ? "Waiting for the analysis service to come back online…"
                : `${STATUS_LABELS[status.status] ?? status.status}…`}
          </div>
          <p className={styles.reassurance}>
            {waitingOnService
              ? "The analysis service is temporarily unavailable (it sleeps after inactivity " +
                "on the free tier). Your request is saved and will start automatically once " +
                "it's back — feel free to close this modal in the meantime."
              : "Feel free to close this modal — this report will keep generating in the " +
                "background, and we'll let you know when it's ready."}
          </p>
        </div>
      )}
    </Modal>
  );
}
