import type { AskResponse } from "../types";
import ProjectionCard from "./ProjectionCard";
import NewsCard from "./NewsCard";
import Markdown from "./Markdown";
import styles from "./Message.module.css";

export default function AssistantTurn({ data }: { data: AskResponse }) {
  return (
    <div className={styles.msgAssistant}>
      <div className={styles.answer}>
        {data.projections?.map((p, i) => (
          <ProjectionCard key={`p${i}`} projection={p} />
        ))}
        {data.news?.map((n, i) => (
          <NewsCard key={`n${i}`} context={n} />
        ))}
        <Markdown>{data.answer || ""}</Markdown>
      </div>
    </div>
  );
}
