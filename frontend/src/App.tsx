import { useEffect, useRef, useState } from "react";
import type { AskResponse } from "./types";
import { askBackend } from "./api";
import Topbar from "./components/Topbar";
import Hero from "./components/Hero";
import MessageStream, { type Message } from "./components/MessageStream";
import ThinkingIndicator from "./components/ThinkingIndicator";
import Composer from "./components/Composer";
import styles from "./App.module.css";

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the newest message in view as the conversation grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  async function submit(textArg?: string) {
    const text = (textArg ?? input).trim();
    if (!text || pending) return;

    setMessages((m) => [...m, { kind: "user", text }]);
    setInput("");
    setPending(true);

    try {
      const data: AskResponse = await askBackend(text, conversationId);
      setConversationId(data.conversation_id);
      setMessages((m) => [...m, { kind: "assistant", data }]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Something went wrong.";
      setMessages((m) => [...m, { kind: "error", text: msg }]);
      // Restore the question so nothing is lost on a failed turn.
      setInput((cur) => (cur.trim() === "" ? text : cur));
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
    <div className={styles.app}>
      <Topbar onNewChat={newChat} />

      <div className={styles.scroll} ref={scrollRef}>
        <div className={styles.col}>
          {messages.length === 0 ? (
            <Hero onPick={(t) => submit(t)} />
          ) : (
            <MessageStream messages={messages} />
          )}
          {pending && <ThinkingIndicator />}
        </div>
      </div>

      <Composer value={input} onChange={setInput} onSubmit={submit} disabled={pending} />
    </div>
  );
}
