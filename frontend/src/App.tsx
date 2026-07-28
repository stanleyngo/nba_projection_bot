import { useEffect, useRef, useState } from "react";
import type { CredentialResponse } from "@react-oauth/google";
import type { AskResponse, ConversationSummary } from "./types";
import { askBackend, fetchConversationHistory, fetchConversations } from "./api";
import Topbar from "./components/Topbar";
import Hero from "./components/Hero";
import Sidebar from "./components/Sidebar";
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

  // The Google ID token from sign-in — null means signed out. Deliberately
  // kept in memory only (not localStorage): it's short-lived anyway (~1hr),
  // and this avoids exposing it to any XSS that might read localStorage.
  // Tradeoff: a page refresh signs you out. Revisit if that's annoying in
  // practice — but don't add persistence pre-emptively for a problem you
  // haven't confirmed matters yet.
  const [idToken, setIdToken] = useState<string | null>(null);

  // The signed-in user's past conversations, for the sidebar.
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);

  // Keep the newest message in view as the conversation grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  // Load the sidebar list once, right after sign-in.
  useEffect(() => {
    if (idToken) void refreshConversations(idToken);
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

  function handleSignIn(credentialResponse: CredentialResponse) {
    if (credentialResponse.credential) setIdToken(credentialResponse.credential);
  }

  function handleSignOut() {
    setIdToken(null);
    // A signed-out session shouldn't keep showing another identity's chat.
    setConversationId(null);
    setMessages([]);
    setConversations([]);
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
        <Sidebar conversations={conversations} activeId={conversationId} onSelect={selectConversation} />
      )}

      <div className={styles.app}>
        <Topbar
          onNewChat={newChat}
          signedIn={idToken !== null}
          onSignIn={handleSignIn}
          onSignOut={handleSignOut}
        />

        <div className={styles.scroll} ref={scrollRef}>
          <div className={styles.col}>
            {idToken === null ? (
              <Hero onPick={() => {}} signedOut onSignIn={handleSignIn} />
            ) : messages.length === 0 ? (
              <Hero onPick={(t) => submit(t)} />
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
    </div>
  );
}
