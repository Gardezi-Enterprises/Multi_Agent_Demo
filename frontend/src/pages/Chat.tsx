import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamChat, ApiError, type Command, type Download, type TraceEvent } from "../api";
import { renderMarkdown } from "../markdown";
import { Aurora, TreeLogo } from "../components/TreeLogo";
import { AccountModal } from "../components/AccountModal";
import { useToast } from "../components/Toasts";
import { toggleTheme } from "../theme";
import "./Chat.css";

interface Me {
  username: string;
  is_admin: boolean;
  email: string;
}
interface StagedFile {
  name: string;
  path: string;
  size: string;
  readable?: boolean;
}
interface Msg {
  role: "me" | "them";
  text: string;
  files?: { name: string; size?: string }[];
  steps?: TraceEvent[];
  downloads?: Download[];
}
interface Thread {
  id: string;
  title: string;
  msgs: Msg[];
}

const PRETTY: Record<string, string> = {
  create_user: "added a person",
  get_all_users: "looked up everyone",
  edit_user: "updated a record",
  send_email: "sent the message",
  analyze_resume_text: "read the resume",
  generate_resume_document: "built the document",
};
const agentName = (n: string) =>
  n
    .replace("delegate_to_", "")
    .replace(/_/g, " ")
    .replace(/\bagent\b/i, "")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());

const PICKS = [
  ["See everyone", "List the people on record", "/users"],
  [
    "Read a resume",
    "Skills and best-fit department",
    "/analyze Dr Maya Rao, maya@clinic.com. Cardiologist, 12 years. Echocardiography, ACLS, patient care, clinical research.",
  ],
  [
    "Write a resume",
    "A polished document to download",
    "/build Ravi Kumar, DevOps engineer, ravi@example.com, skills Python, AWS, Docker, Terraform",
  ],
  ["Email everyone", "One message to every person on record", "/broadcast the new portal is live from Monday"],
];

// Threads are stored per user so a different sign-in on the same browser never
// sees the previous user's conversations.
const threadsKey = (username: string) => `ta.threads:${username.toLowerCase()}`;

function loadThreads(username: string): Thread[] {
  try {
    return JSON.parse(localStorage.getItem(threadsKey(username)) || "[]");
  } catch {
    return [];
  }
}

export function Chat({ me, onSignedOut }: { me: Me; onSignedOut: () => void }) {
  const [threads, setThreads] = useState<Thread[]>(() => loadThreads(me.username));
  const [currentId, setCurrentId] = useState<string | null>(threads[0]?.id ?? null);
  const [commands, setCommands] = useState<Command[]>([]);
  const [tab, setTab] = useState<"chats" | "guide">("chats");
  const [sidebarHidden, setSidebarHidden] = useState(window.innerWidth <= 860);
  const [showAccount, setShowAccount] = useState(false);
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [palIdx, setPalIdx] = useState(0);
  const [dragging, setDragging] = useState(false);

  const feedRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const toast = useToast();

  const persist = useCallback(
    (next: Thread[]) => {
      try {
        localStorage.setItem(threadsKey(me.username), JSON.stringify(next.slice(0, 40)));
      } catch {
        /* ignore */
      }
    },
    [me.username],
  );

  useEffect(() => {
    api.meta().then((m) => setCommands(m.commands)).catch(() => toast("Could not reach the server", "err"));
  }, [toast]);

  const current = threads.find((t) => t.id === currentId) || null;

  // Stable so the memoised Message rows don't re-render on every keystroke.
  const handleCopy = useCallback(() => toast("Copied", "ok"), [toast]);

  const scrollDown = useCallback(() => {
    const s = scrollRef.current;
    if (s) s.scrollTop = s.scrollHeight;
  }, []);
  useEffect(scrollDown, [current?.msgs.length, scrollDown]);

  function updateThreads(next: Thread[]) {
    setThreads(next);
    persist(next);
  }

  function newChat() {
    const t: Thread = { id: "t" + Date.now().toString(36), title: "New chat", msgs: [] };
    const next = [t, ...threads].slice(0, 40);
    updateThreads(next);
    setCurrentId(t.id);
    api.reset().catch(() => {});
    if (window.innerWidth <= 860) setSidebarHidden(true);
  }

  function deleteThread(id: string) {
    const next = threads.filter((t) => t.id !== id);
    updateThreads(next);
    if (currentId === id) setCurrentId(next[0]?.id ?? null);
  }

  // slash palette
  const palHits = useMemo(() => {
    const v = input;
    if (!(v.startsWith("/") && !v.includes("\n") && !/^\/\S+\s/.test(v))) return [];
    const q = v.slice(1).toLowerCase();
    return commands.filter((c) => c.name.startsWith(q));
  }, [input, commands]);
  useEffect(() => setPalIdx(0), [input]);

  function choose(i: number) {
    const c = palHits[i];
    if (!c) return;
    if (c.needs_args) {
      setInput(`/${c.name} `);
      inputRef.current?.focus();
    } else {
      void send(`/${c.name}`);
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      try {
        const d = await api.upload(file);
        setStaged((s) => [...s, d]);
        if (d.note) toast(d.note, "info");
      } catch (err) {
        toast(err instanceof ApiError ? err.message : "Upload failed", "err");
      }
    }
  }

  async function send(text?: string) {
    const value = (text ?? input).trim();
    if (!value || busy) return;

    let thread = current;
    let workingThreads = threads;
    if (!thread) {
      thread = { id: "t" + Date.now().toString(36), title: "New chat", msgs: [] };
      workingThreads = [thread, ...threads];
      setCurrentId(thread.id);
      api.reset().catch(() => {});
    }
    const files = staged.slice();
    const userMsg: Msg = { role: "me", text: value, files: files.map((f) => ({ name: f.name, size: f.size })) };
    thread = {
      ...thread,
      title: thread.title === "New chat" ? value.replace(/^\/\w+\s*/, "").slice(0, 42) || value.slice(0, 42) : thread.title,
      msgs: [...thread.msgs, userMsg],
    };
    const applied = workingThreads.map((t) => (t.id === thread!.id ? thread! : t));
    updateThreads(applied);
    setInput("");
    setStaged([]);
    setBusy(true);

    const collected: TraceEvent[] = [];
    const ctl = new AbortController();
    abortRef.current = ctl;

    // Live placeholder message we mutate as frames arrive.
    let live: Msg = { role: "them", text: "", steps: [] };
    const pushLive = () => {
      thread = { ...thread!, msgs: [...thread!.msgs.filter((m) => m !== live), live] };
    };

    try {
      for await (const frame of streamChat(value, files.map((f) => f.path), ctl.signal)) {
        if (frame.type === "trace") {
          collected.push(frame.event);
          live = { ...live, steps: [...collected] };
        } else if (frame.type === "done") {
          live = {
            role: "them",
            text: frame.answer,
            steps: frame.trace?.length ? frame.trace : collected,
            downloads: frame.downloads,
          };
        } else if (frame.type === "error") {
          live = { role: "them", text: "", steps: collected };
          toast(frame.error, "err");
        } else {
          continue;
        }
        pushLive();
        setThreads((prev) => prev.map((t) => (t.id === thread!.id ? thread! : t)));
        scrollDown();
      }
      persist(threads.map((t) => (t.id === thread!.id ? thread! : t)));
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        toast(err instanceof ApiError ? err.message : "Something went wrong", "err");
      }
      // drop the empty live placeholder on abort/failure
      thread = { ...thread!, msgs: thread!.msgs.filter((m) => m !== live || m.text) };
      setThreads((prev) => prev.map((t) => (t.id === thread!.id ? thread! : t)));
    } finally {
      setBusy(false);
      abortRef.current = null;
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (palHits.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPalIdx((i) => (i + 1) % palHits.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPalIdx((i) => (i - 1 + palHits.length) % palHits.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        choose(palIdx);
        return;
      }
      if (e.key === "Escape") {
        setInput("");
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape" && busy) abortRef.current?.abort();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [busy]);

  return (
    <div
      className="app"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.relatedTarget) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer?.files?.length) void uploadFiles(e.dataTransfer.files);
      }}
    >
      <Aurora />
      {!sidebarHidden && window.innerWidth <= 860 && (
        <div className="veil on" onClick={() => setSidebarHidden(true)} />
      )}

      <aside className={"sidebar" + (sidebarHidden ? " hide" : "")}>
        <div className="brand">
          <TreeLogo size={38} thinking={busy} />
          <div>
            <h1>Taseer's Agent</h1>
            <p>Four specialists, one assistant</p>
          </div>
        </div>
        <button className="new-chat" onClick={newChat}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
            <path d="M12 5v14M5 12h14" />
          </svg>
          New chat
        </button>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "chats"} onClick={() => setTab("chats")}>
            Chats
          </button>
          <button role="tab" aria-selected={tab === "guide"} onClick={() => setTab("guide")}>
            Guide
          </button>
        </div>

        {tab === "chats" ? (
          <div className="pane">
            <div className="pane-label">Recent</div>
            {threads.length === 0 && <div className="muted">Nothing yet — ask something to begin.</div>}
            {threads.map((t) => (
              <button
                key={t.id}
                className={"thread" + (t.id === currentId ? " active" : "")}
                onClick={() => {
                  setCurrentId(t.id);
                  if (window.innerWidth <= 860) setSidebarHidden(true);
                }}
              >
                <span className="bullet" />
                <span>{t.title}</span>
                <span
                  className="x"
                  role="button"
                  tabIndex={0}
                  aria-label="Delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteThread(t.id);
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="pane">
            <div className="pane-label">How to use</div>
            {[
              ["1", "Just ask. Your request is routed to the right specialist automatically."],
              ["2", "Type / to pick a command directly."],
              ["3", "Drop in a resume (PDF, DOCX, TXT) to have it read and analysed."],
              ["4", "Documents it creates appear as downloads in the chat."],
            ].map(([n, txt]) => (
              <div className="step" key={n}>
                <i>{n}</i>
                <div>{txt}</div>
              </div>
            ))}
            <div className="pane-label">Commands</div>
            {commands.map((c) => (
              <button key={c.name} className="tile" onClick={() => send(c.example)}>
                <b>
                  <code>/{c.name}</code>
                </b>
                <span>{c.summary}</span>
              </button>
            ))}
          </div>
        )}

        <div className="acct">
          <div className="avatar">{me.username[0]?.toUpperCase()}</div>
          <div className="who">
            <b>{me.username}</b>
            <span>{me.is_admin ? "Administrator" : "Member"}</span>
          </div>
          <button className="gear" aria-label="Account settings" onClick={() => setShowAccount(true)}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z" />
            </svg>
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="bar">
          <button className="ico" aria-label="Toggle sidebar" onClick={() => setSidebarHidden((h) => !h)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 6h18M3 12h18M3 18h18" />
            </svg>
          </button>
          <div className="title">{current?.title ?? "New chat"}</div>
          <button className="ico" aria-label="Switch theme" onClick={toggleTheme}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
            </svg>
          </button>
        </header>

        <div className="scroll" ref={scrollRef}>
          <div className="feed" ref={feedRef}>
            {!current?.msgs.length ? (
              <div className="hero">
                <div className="big">
                  <TreeLogo size={76} />
                </div>
                <h2>How can I help?</h2>
                <p>Ask in your own words — I'll bring in whichever specialist fits.</p>
                <div className="picks">
                  {PICKS.map(([a, b, q]) => (
                    <button className="pick" key={a} onClick={() => send(q)}>
                      <b>{a}</b>
                      <span>{b}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              current.msgs.map((m, i) => <Message key={i} msg={m} onCopy={handleCopy} />)
            )}
            {busy && (
              <div className="think">
                <span className="dots">
                  <i />
                  <i />
                  <i />
                </span>
                <span>Working…</span>
              </div>
            )}
          </div>
        </div>

        <div className="dock">
          <div className="composer">
            {palHits.length > 0 && (
              <div className="palette" role="listbox">
                {palHits.map((c, i) => (
                  <button
                    key={c.name}
                    className={"cmd" + (i === palIdx ? " sel" : "")}
                    onMouseEnter={() => setPalIdx(i)}
                    onClick={() => choose(i)}
                  >
                    <span className="k">/{c.name}</span>
                    <span className="d">{c.summary}</span>
                  </button>
                ))}
              </div>
            )}
            {staged.length > 0 && (
              <div className="queue">
                {staged.map((f) => (
                  <span className="file" key={f.path}>
                    <FileIcon />
                    <span className="nm">{f.name}</span>
                    <span className="sz">{f.size}</span>
                    <button className="rm" aria-label="Remove" onClick={() => setStaged((s) => s.filter((x) => x.path !== f.path))}>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className={"field" + (dragging ? " drop" : "")}>
              <label className="sr" htmlFor="ask">
                Message
              </label>
              <textarea
                id="ask"
                ref={inputRef}
                rows={1}
                placeholder="Ask anything…   /  for commands"
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  const el = e.target;
                  el.style.height = "auto";
                  el.style.height = Math.min(el.scrollHeight, 190) + "px";
                }}
                onKeyDown={onKeyDown}
              />
              <input
                type="file"
                id="pick"
                multiple
                hidden
                accept=".pdf,.docx,.txt,.md,.rtf,.csv"
                onChange={(e) => {
                  if (e.target.files) void uploadFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <button className="ico" aria-label="Attach a file" onClick={() => document.getElementById("pick")?.click()}>
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M21.4 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.2-9.19a4 4 0 015.65 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
                </svg>
              </button>
              <button
                className={"send" + (busy ? " halt" : "")}
                aria-label={busy ? "Stop" : "Send"}
                onClick={() => (busy ? abortRef.current?.abort() : send())}
              >
                {busy ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 12h14M13 6l6 6-6 6" />
                  </svg>
                )}
              </button>
            </div>
            <p className="tip">
              <kbd>Enter</kbd> send · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line · <kbd>Esc</kbd> stop
            </p>
          </div>
        </div>
      </main>

      {showAccount && <AccountModal me={me} onClose={() => setShowAccount(false)} onSignedOut={onSignedOut} />}
    </div>
  );
}

// Defined at module scope (not nested in Chat) so its identity is stable across
// renders — otherwise every keystroke would remount the whole message list and
// the screen would flicker.
const Message = memo(function Message({ msg, onCopy }: { msg: Msg; onCopy: () => void }) {
  if (msg.role === "me") {
    return (
      <div className="turn mine">
        <div className="bubble">{msg.text}</div>
        {msg.files?.length ? (
          <div className="files">
            {msg.files.map((f, i) => (
              <span className="file" key={i}>
                <FileIcon />
                <span className="nm">{f.name}</span>
                <span className="sz">{f.size}</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <>
      {msg.steps?.length ? <Steps events={msg.steps} /> : null}
      {msg.text ? (
        <div className="turn theirs">
          <div className="bubble" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.text) }} />
          {msg.downloads?.length ? (
            <div className="files">
              {msg.downloads.map((d) => {
                const shown = d.download_name || d.name;
                return (
                  <a
                    className="file"
                    key={d.name}
                    href={`/api/download?f=${encodeURIComponent(d.name)}&as=${encodeURIComponent(shown)}`}
                    download={shown}
                  >
                    <DownIcon />
                    <span className="nm">{shown}</span>
                    <span className="sz">{d.size}</span>
                  </a>
                );
              })}
            </div>
          ) : null}
          <div className="msg-tools">
            <button
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(msg.text);
                  onCopy();
                } catch {
                  /* ignore */
                }
              }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="9" y="9" width="13" height="13" rx="2" />
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
              </svg>
              Copy
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
});

function Steps({ events }: { events: TraceEvent[] }) {
  const agents = [...new Set(events.filter((e) => e.kind === "delegation").map((e) => agentName(e.name)))];
  return (
    <details className="steps" open>
      <summary>
        <svg className="arrow" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
          <path d="M9 6l6 6-6 6" />
        </svg>
        <span className="who">{agents.length ? agents.join(" · ") : "Working…"}</span>
      </summary>
      <div className="steps-body">
        {events.map((e, i) => {
          if (e.kind === "delegation") {
            return (
              <div className="row lead" key={i}>
                <span className="pip" />
                <span className="act">{agentName(e.name)}</span>
              </div>
            );
          }
          const cls =
            e.status === "success" ? "ok" : e.status === "partial" ? "warn" : e.status === "running" ? "run" : "err";
          const word = e.status === "running" ? "working" : e.status === "success" ? "done" : e.status;
          return (
            <div className="row sub" key={i}>
              <span className="pip" />
              <span className="act">{PRETTY[e.name] || e.name.replace(/_/g, " ")}</span>
              <span className={"state " + cls}>{word}</span>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
      <path d="M14 2v6h6" />
    </svg>
  );
}
function DownIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
    </svg>
  );
}
