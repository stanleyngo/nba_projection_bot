import styles from "./NotificationStack.module.css";

export interface DeepAnalysisNotification {
  id: string;
  // null for a job that failed before ever being successfully submitted
  // (e.g. Kafka unreachable) — there's no report to open in that case, since
  // the frontend never got a job id back at all.
  jobId: number | null;
  playerName: string;
  status: "done" | "failed";
}

export default function NotificationStack({
  notifications,
  onOpen,
  onDismiss,
}: {
  notifications: DeepAnalysisNotification[];
  onOpen: (n: DeepAnalysisNotification) => void;
  onDismiss: (id: string) => void;
}) {
  if (notifications.length === 0) return null;

  return (
    <div className={styles.stack} aria-live="polite">
      {notifications.map((n) => (
        <div key={n.id} className={`${styles.toast} ${n.status === "failed" ? styles.toastError : ""}`}>
          {n.jobId !== null ? (
            <button type="button" className={styles.body} onClick={() => onOpen(n)}>
              <div className={styles.title}>
                {n.status === "done" ? "Report ready" : "Report failed"}
              </div>
              <div className={styles.sub}>{n.playerName}</div>
            </button>
          ) : (
            <div className={styles.body}>
              <div className={styles.title}>Request failed</div>
              <div className={styles.sub}>{n.playerName}</div>
            </div>
          )}
          <button
            type="button"
            className={styles.dismiss}
            onClick={() => onDismiss(n.id)}
            aria-label="Dismiss notification"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
