import type { ConversationSummary, DeepAnalysisJobSummary } from "../types";
import styles from "./Sidebar.module.css";

function formatMonthYear(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  reports,
  onSelectReport,
}: {
  conversations: ConversationSummary[];
  activeId: number | null;
  onSelect: (id: number) => void;
  reports: DeepAnalysisJobSummary[];
  onSelectReport: (id: number, playerName: string) => void;
}) {
  return (
    <nav className={styles.sidebar} aria-label="Past conversations and deep-analysis reports">
      <div className={styles.section}>
        <div className={styles.heading}>Conversations</div>
        {conversations.length === 0 ? (
          <div className={styles.empty}>No conversations yet</div>
        ) : (
          <ul className={styles.list}>
            {conversations.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`${styles.item} ${c.id === activeId ? styles.active : ""}`}
                  onClick={() => onSelect(c.id)}
                >
                  <span className={styles.itemTitle}>{c.title ?? "Untitled"}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className={styles.section}>
        <div className={styles.heading}>Reports</div>
        {reports.length === 0 ? (
          <div className={styles.empty}>No reports yet</div>
        ) : (
          <ul className={styles.list}>
            {reports.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  className={styles.item}
                  onClick={() => onSelectReport(r.id, r.player_name)}
                >
                  <span className={styles.itemTitle}>{r.player_name}</span>
                  <span className={styles.itemMeta}>{formatMonthYear(r.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </nav>
  );
}
