import type { AskResponse } from "../types";
import UserBubble from "./UserBubble";
import AssistantTurn from "./AssistantTurn";
import styles from "./Message.module.css";

export type Message =
  | { kind: "user"; text: string }
  | { kind: "assistant"; data: AskResponse }
  | { kind: "error"; text: string };

export default function MessageStream({ messages }: { messages: Message[] }) {
  return (
    <div aria-live="polite">
      {messages.map((m, i) => {
        if (m.kind === "user") return <UserBubble key={i} text={m.text} />;
        if (m.kind === "assistant") return <AssistantTurn key={i} data={m.data} />;
        return (
          <div key={i} className={styles.msgAssistant}>
            <div className={styles.errbubble}>{m.text}</div>
          </div>
        );
      })}
    </div>
  );
}
