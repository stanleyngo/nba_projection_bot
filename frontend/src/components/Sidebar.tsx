import type { ConversationSummary } from "../types";
import styles from "./Sidebar.module.css";

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
}: {
  conversations: ConversationSummary[];
  activeId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <nav className={styles.sidebar} aria-label="Past conversations">
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
                {c.title ?? "Untitled"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
