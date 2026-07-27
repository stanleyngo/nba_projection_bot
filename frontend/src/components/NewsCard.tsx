import type { NewsContext, NewsItem } from "../types";
import styles from "./NewsCard.module.css";

// Renders the RAG news/analysis context. News (reported facts) and Analysis
// (opinion) are kept visually separate, and every analysis item is explicitly
// labeled as opinion — matching the strict framing rules in agent.py's prompt.
export default function NewsCard({ context }: { context: NewsContext }) {
  const news = context.news || [];
  const analysis = context.analysis || [];
  if (news.length === 0 && analysis.length === 0) return null;

  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.title}>News &amp; Analysis</span>
        <span className={styles.player}>{context.player_name}</span>
      </div>

      {news.length > 0 && (
        <section className={styles.section}>
          <div className={styles.label}>News</div>
          {news.map((it, i) => (
            <Item key={i} item={it} />
          ))}
        </section>
      )}

      {analysis.length > 0 && (
        <section className={styles.section}>
          <div className={styles.label}>
            Analysis <span className={styles.opinionNote}>· opinion, not fact</span>
          </div>
          {analysis.map((it, i) => (
            <Item key={i} item={it} opinion />
          ))}
        </section>
      )}
    </div>
  );
}

function Item({ item, opinion }: { item: NewsItem; opinion?: boolean }) {
  return (
    <div className={styles.item}>
      <div className={styles.itemHead}>
        {item.url ? (
          <a
            className={styles.itemTitle}
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {item.title || item.url}
          </a>
        ) : (
          <span className={styles.itemTitle}>{item.title}</span>
        )}
        {opinion && <span className={styles.opinionTag}>Opinion</span>}
      </div>
      {item.text && <p className={styles.snippet}>{item.text}</p>}
    </div>
  );
}
