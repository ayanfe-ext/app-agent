import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  ArrowRight,
  Bot,
  ChevronDown,
  Clock3,
  CreditCard,
  ExternalLink,
  KeyRound,
  LayoutDashboard,
  Loader2,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
  WalletCards,
} from "lucide-react";
import "./styles.css";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const examples = {
  customer: [
    "I want to pay 5000 naira",
    "Create a checkout for Ada Lovelace, ada@example.com, 2500 naira",
    "What can I do here?",
  ],
  merchant: [
    "Pay 4000 naira to 2135554283 at UBA, narration vendor refund",
    "Give me payouts lower than NGN 2000",
    "Show successful payouts",
  ],
};

function cx(...classes) {
  return classes.filter(Boolean).join(" ");
}

function safeJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getResultData(message) {
  return message?.tool_result?.data || message?.tool_result?.raw?.data || null;
}

function amountLabel(amount) {
  if (!amount) return "NGN 0";
  if (typeof amount === "number") return `NGN ${amount.toLocaleString()}`;
  return amount.formatted || `${amount.currency || "NGN"} ${amount.value ?? ""}`;
}

function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (value.includes("success")) return "text-neon border-neon/30 bg-neon/10";
  if (value.includes("fail")) return "text-red-300 border-red-400/30 bg-red-400/10";
  if (value.includes("pending")) return "text-ember border-ember/30 bg-ember/10";
  return "text-white/70 border-white/15 bg-white/5";
}

function useStreamingText(text, speed = 10) {
  const [shown, setShown] = useState("");

  useEffect(() => {
    setShown("");
    if (!text) return;
    let index = 0;
    const timer = window.setInterval(() => {
      index += 2;
      setShown(text.slice(0, index));
      if (index >= text.length) window.clearInterval(timer);
    }, speed);
    return () => window.clearInterval(timer);
  }, [text, speed]);

  return shown;
}

function LoginScreen({ onLogin }) {
  const [actorType, setActorType] = useState("merchant");
  const [accessKey, setAccessKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE_URL}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor_type: actorType, access_key: accessKey }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Login failed");
      onLogin(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen overflow-hidden bg-ink text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(102,242,194,0.24),transparent_28%),radial-gradient(circle_at_85%_20%,rgba(106,169,255,0.22),transparent_30%),linear-gradient(135deg,#05070c_0%,#0d1524_48%,#111827_100%)]" />
      <div className="relative mx-auto flex min-h-screen max-w-6xl items-center px-6 py-10">
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid w-full gap-8 lg:grid-cols-[1.05fr_0.95fr]"
        >
          <div className="flex flex-col justify-center">
            <div className="mb-6 inline-flex w-fit items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-sm text-white/70 backdrop-blur">
              <Sparkles className="h-4 w-4 text-neon" />
              Duplo Atlas Agent payment operations
            </div>
            <h1 className="max-w-3xl text-5xl font-semibold leading-tight tracking-tight md:text-7xl">
              <span className="text-neon">Atlas summit.</span> One agent. Two permissioned payment modes.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-white/68">
              Customer sessions create checkout links. Merchant sessions can create payouts, search payout history,
              inspect structured results, and track payout status changes from polling or Atlas webhooks.
            </p>
          </div>

          <form onSubmit={submit} className="rounded-[2rem] border border-white/12 bg-white/[0.07] p-6 shadow-glow backdrop-blur-2xl">
            <div className="mb-6 flex items-center justify-between">
              <div>
                <p className="text-sm uppercase tracking-[0.32em] text-neon">Authenticate</p>
                <h2 className="mt-2 text-2xl font-semibold">Open agent session</h2>
              </div>
              <ShieldCheck className="h-8 w-8 text-neon" />
            </div>

            <div className="mb-5 grid grid-cols-2 gap-3">
              {["customer", "merchant"].map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setActorType(mode)}
                  className={cx(
                    "rounded-2xl border px-4 py-4 text-left transition",
                    actorType === mode
                      ? "border-neon/60 bg-neon/12 text-white"
                      : "border-white/10 bg-white/5 text-white/60 hover:bg-white/10",
                  )}
                >
                  {mode === "merchant" ? <LayoutDashboard className="mb-3 h-5 w-5" /> : <UserRound className="mb-3 h-5 w-5" />}
                  <span className="block text-sm font-medium capitalize">{mode}</span>
                </button>
              ))}
            </div>

            <label className="mb-2 block text-sm text-white/60">
              {actorType === "merchant" ? "Merchant access key" : "Customer access key"}
            </label>
            <div className="mb-4 flex items-center gap-3 rounded-2xl border border-white/10 bg-black/20 px-4 py-3 focus-within:border-neon/60">
              <KeyRound className="h-5 w-5 text-white/40" />
              <input
                value={accessKey}
                onChange={(event) => setAccessKey(event.target.value)}
                type="password"
                className="w-full bg-transparent text-white outline-none placeholder:text-white/30"
                placeholder="Paste the server-side test key"
              />
            </div>

            {error && <p className="mb-4 rounded-xl border border-red-400/20 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</p>}

            <button
              disabled={loading || !accessKey}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-neon px-5 py-4 font-semibold text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? <Loader2 className="h-5 w-5 animate-spin" /> : <ArrowRight className="h-5 w-5" />}
              Continue
            </button>
          </form>
        </motion.section>
      </div>
    </main>
  );
}

function ThoughtAccordion({ message }) {
  const [open, setOpen] = useState(false);
  const steps = [
    { label: "Thinking", detail: "Interpreted the request and selected the correct payment mode." },
    { label: "Searching", detail: message.actorType === "merchant" ? "Checked merchant-capable actions and structured results." : "Checked customer checkout capability." },
    { label: "Executing", detail: message.status === "awaiting_confirmation" ? "Waiting for confirmation before moving money." : "Returned the latest safe response." },
  ];

  return (
    <div className="mt-3 rounded-2xl border border-white/10 bg-white/[0.04]">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-4 py-3 text-sm text-white/60">
        <span className="inline-flex items-center gap-2"><Activity className="h-4 w-4 text-cobalt" /> Agent process</span>
        <ChevronDown className={cx("h-4 w-4 transition", open && "rotate-180")} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <div className="space-y-3 px-4 pb-4">
              {steps.map((step, index) => (
                <div key={step.label} className="flex gap-3 text-sm">
                  <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/8 text-xs text-neon">{index + 1}</span>
                  <div>
                    <p className="font-medium text-white/80">{step.label}</p>
                    <p className="text-white/48">{step.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function ResultCard({ message, onTrack, trackedSourceRef }) {
  const result = message.tool_result;
  const data = getResultData(message);
  if (!result && !message.checkout_url) return null;

  if (message.checkout_url) {
    return (
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-4 rounded-3xl border border-neon/20 bg-neon/10 p-4">
        <div className="mb-3 flex items-center gap-3">
          <CreditCard className="h-5 w-5 text-neon" />
          <div>
            <p className="text-xs uppercase tracking-[0.24em] text-white/35">Checkout ready</p>
            <h3 className="font-semibold">Payment link created</h3>
          </div>
        </div>
        <a href={message.checkout_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-2xl bg-neon px-4 py-2 text-sm font-semibold text-ink">
          Open checkout <ExternalLink className="h-4 w-4" />
        </a>
      </motion.div>
    );
  }

  if (Array.isArray(data)) {
    return <PayoutTable rows={data} />;
  }

  if (data?.sourceReference || data?.reference || data?.status) {
    return (
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-4 rounded-3xl border border-white/10 bg-black/20 p-4">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.24em] text-white/35">Structured result</p>
            <h3 className="mt-1 text-lg font-semibold">{data.recipientAccountName || data.customer?.name || "Payment result"}</h3>
          </div>
          <span className={cx("rounded-full border px-3 py-1 text-xs", statusTone(data.status || data.checkoutStatus))}>{data.status || data.checkoutStatus || "Created"}</span>
        </div>
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <Info label="Amount" value={amountLabel(data.amount || data.checkoutAmount)} />
          <Info label="Reference" value={data.reference || data.checkoutReference || "N/A"} />
          <Info label="Source reference" value={data.sourceReference || "N/A"} />
          <Info label="Bank" value={data.recipientBankName || data.paymentChannel || "N/A"} />
        </div>
        {data.sourceReference && (
          <button onClick={() => onTrack(data.sourceReference)} className="mt-4 inline-flex items-center gap-2 rounded-2xl border border-neon/30 bg-neon/10 px-4 py-2 text-sm text-neon transition hover:bg-neon/15">
            <Clock3 className="h-4 w-4" />
            {trackedSourceRef === data.sourceReference ? "Refresh payout status" : "Track payout status"}
          </button>
        )}
      </motion.div>
    );
  }

  return (
    <pre className="mt-4 max-h-72 overflow-auto rounded-2xl border border-white/10 bg-black/30 p-4 text-xs text-white/70">
      {safeJson(result)}
    </pre>
  );
}

function PayoutTable({ rows }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-4 overflow-hidden rounded-3xl border border-white/10 bg-black/20">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <h3 className="font-semibold">Payout results</h3>
        <span className="text-xs text-white/45">{rows.length} rows</span>
      </div>
      <div className="max-h-72 overflow-auto">
        <table className="w-full min-w-[680px] text-left text-sm">
          <thead className="bg-white/[0.04] text-xs uppercase tracking-[0.2em] text-white/40">
            <tr>
              <th className="px-4 py-3">Recipient</th>
              <th className="px-4 py-3">Bank</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Reference</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.reference || row.sourceReference} className="border-t border-white/7">
                <td className="px-4 py-3">{row.recipientAccountName || "-"}</td>
                <td className="px-4 py-3 text-white/60">{row.recipientBankName || "-"}</td>
                <td className="px-4 py-3">{amountLabel(row.amount)}</td>
                <td className="px-4 py-3"><span className={cx("rounded-full border px-2 py-1 text-xs", statusTone(row.status))}>{row.status}</span></td>
                <td className="px-4 py-3 font-mono text-xs text-white/55">{row.reference || row.sourceReference}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </motion.div>
  );
}

function Info({ label, value }) {
  return (
    <div className="rounded-2xl border border-white/8 bg-white/[0.03] px-4 py-3">
      <p className="text-xs uppercase tracking-[0.18em] text-white/35">{label}</p>
      <p className="mt-1 truncate font-medium text-white/80">{value}</p>
    </div>
  );
}

function ChatMessage({ message, onSuggestion, onTrack, trackedSourceRef }) {
  const isUser = message.role === "user";
  const streamed = useStreamingText(isUser ? message.content : message.content, isUser ? 0 : 8);

  return (
    <motion.article
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className={cx("flex gap-3", isUser && "justify-end")}
    >
      {!isUser && <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-neon/12 text-neon"><Bot className="h-5 w-5" /></div>}
      <div className={cx("max-w-[86%] rounded-[1.5rem] border px-4 py-3", isUser ? "border-cobalt/30 bg-cobalt/15" : "border-white/10 bg-white/[0.06]")}>
        <p className="whitespace-pre-wrap leading-7 text-white/86">{streamed}</p>
        {!isUser && <ThoughtAccordion message={message} />}
        {!isUser && <ResultCard message={message} onTrack={onTrack} trackedSourceRef={trackedSourceRef} />}
        {!isUser && (
          <div className="mt-4 flex flex-wrap gap-2">
            {(message.suggestions || []).map((suggestion) => (
              <button key={suggestion} onClick={() => onSuggestion(suggestion)} className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-white/60 hover:border-neon/40 hover:text-neon">
                {suggestion}
              </button>
            ))}
          </div>
        )}
      </div>
    </motion.article>
  );
}

function CanvasPanel({ open, latest, statusCard, onClose }) {
  const data = getResultData(latest);
  return (
    <aside className={cx("hidden border-l border-white/10 bg-white/[0.045] backdrop-blur-2xl lg:block", open ? "w-[38%]" : "w-0 overflow-hidden")}>
      <div className="flex h-full flex-col">
        <header className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <div>
            <p className="text-xs uppercase tracking-[0.24em] text-neon">Canvas</p>
            <h2 className="font-semibold">Live workspace</h2>
          </div>
          <button onClick={onClose} className="rounded-xl border border-white/10 p-2 text-white/60 hover:text-white"><PanelRightClose className="h-5 w-5" /></button>
        </header>
        <div className="flex-1 overflow-auto p-5">
          {statusCard && <StatusCard status={statusCard} />}
          {Array.isArray(data) ? (
            <PayoutTable rows={data} />
          ) : data ? (
            <div className="rounded-3xl border border-white/10 bg-black/20 p-5">
              <h3 className="mb-4 text-lg font-semibold">Selected result</h3>
              <pre className="max-h-[65vh] overflow-auto text-xs leading-6 text-white/65">{safeJson(data)}</pre>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center rounded-3xl border border-dashed border-white/10 p-8 text-center text-white/45">
              <WalletCards className="mb-4 h-10 w-10 text-neon" />
              <p>Structured results, payout status, tables, and transaction data will appear here.</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

function StatusCard({ status }) {
  return (
    <div className="mb-5 rounded-3xl border border-white/10 bg-black/20 p-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold">Payout status</h3>
        <span className={cx("rounded-full border px-3 py-1 text-xs", statusTone(status.status))}>
          {status.checking ? "Checking" : status.status || "Checking"}
        </span>
      </div>
      <div className="grid gap-3 text-sm">
        <Info label="Source reference" value={status.sourceReference || "N/A"} />
        <Info label="Transaction reference" value={status.reference || "N/A"} />
        <Info label="Status source" value={status.statusSource || "Pending lookup"} />
        <Info label="Last checked" value={status.checkedAt || "N/A"} />
      </div>
      {status.error && <p className="mt-3 rounded-2xl border border-red-400/20 bg-red-500/10 px-3 py-2 text-sm text-red-200">{status.error}</p>}
    </div>
  );
}

function InputBar({ value, onChange, onSubmit, disabled, actorType }) {
  return (
    <form onSubmit={onSubmit} className="rounded-[1.75rem] border border-white/12 bg-white/[0.08] p-2 shadow-glow backdrop-blur-2xl">
      <div className="flex items-center gap-2">
        <div className="hidden rounded-2xl bg-white/8 px-3 py-2 text-xs text-white/50 sm:block">{actorType}</div>
        <input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          placeholder={actorType === "merchant" ? "Ask for payouts, create a payout, or search transaction history..." : "Ask to create a checkout link..."}
          className="min-w-0 flex-1 bg-transparent px-4 py-3 text-white outline-none placeholder:text-white/30 disabled:opacity-50"
        />
        <button disabled={disabled || !value.trim()} className="flex h-12 w-12 items-center justify-center rounded-2xl bg-neon text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40">
          {disabled ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
        </button>
      </div>
    </form>
  );
}

function App() {
  const [session, setSession] = useState(() => {
    const raw = window.localStorage.getItem("agentSession");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      window.localStorage.removeItem("agentSession");
      return null;
    }
  });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [canvasOpen, setCanvasOpen] = useState(true);
  const [trackedSourceRef, setTrackedSourceRef] = useState("");
  const [statusCard, setStatusCard] = useState(null);
  const scrollRef = useRef(null);

  const latestStructured = useMemo(() => [...messages].reverse().find((message) => message.tool_result), [messages]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // useEffect(() => {
  //   if (!trackedSourceRef || !session || session.actor_type !== "merchant") return;
  //   let cancelled = false;
  //   async function check() {
  //     try {
  //       const response = await fetch(`${API_BASE_URL}/merchant/payout/status/${trackedSourceRef}`, {
  //         headers: { Authorization: `Bearer ${session.access_token}` },
  //       });
  //       const data = await response.json();
  //       if (!response.ok) throw new Error(data.detail || "Unable to check payout status");
  //       if (cancelled) return;
  //       setStatusCard({ ...data, checking: false, checkedAt: new Date().toLocaleTimeString() });
  //     } catch (err) {
  //       if (cancelled) return;
  //       setStatusCard((current) => ({
  //         ...(current || { sourceReference: trackedSourceRef }),
  //         checking: false,
  //         statusSource: "status check failed",
  //         checkedAt: new Date().toLocaleTimeString(),
  //         error: err.message,
  //       }));
  //     }
  //   }
  //   check();
  //   const timer = window.setInterval(check, 8000);
  //   return () => {
  //     cancelled = true;
  //     window.clearInterval(timer);
  //   };
  // }, [trackedSourceRef, session]);

  function handleLogin(data) {
    const nextSession = { ...data, conversation_id: null };
    setSession(nextSession);
    window.localStorage.setItem("agentSession", JSON.stringify(nextSession));
    setMessages([
      {
        id: crypto.randomUUID(),
        role: "agent",
        actorType: nextSession.actor_type,
        content: nextSession.actor_type === "merchant" ? "Merchant mode is active. I can help with checkout, payouts, and payout history." : "Customer mode is active. I can help create a checkout link.",
        status: "ready",
        suggestions: examples[nextSession.actor_type],
      },
    ]);
  }

  function logout() {
    window.localStorage.removeItem("agentSession");
    setSession(null);
    setMessages([]);
    setStatusCard(null);
    setTrackedSourceRef("");
  }

  function trackPayout(sourceReference) {
    setCanvasOpen(true);
    setTrackedSourceRef(sourceReference);
    setStatusCard((current) => ({
      ...(current?.sourceReference === sourceReference ? current : {}),
      sourceReference,
      checking: true,
      statusSource: "checking Atlas status",
      checkedAt: new Date().toLocaleTimeString(),
    }));
  }

  async function sendMessage(event, override) {
    event?.preventDefault();
    const content = (override || input).trim();
    if (!content || busy || !session) return;
    setInput("");
    setBusy(true);
    const userMessage = { id: crypto.randomUUID(), role: "user", content };
    setMessages((current) => [...current, userMessage]);

    try {
      const endpoint = session.actor_type === "merchant" ? "/merchant/conversation" : "/conversation";
      const body = {
        conversation_id: session.conversation_id || undefined,
        message: { role: "user", content },
      };
      const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
        },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Request failed");
      if (data.conversation_id) {
        const nextSession = { ...session, conversation_id: data.conversation_id };
        setSession(nextSession);
        window.localStorage.setItem("agentSession", JSON.stringify(nextSession));
      }
      const resultData = data.tool_result?.data;
      if (resultData?.sourceReference) trackPayout(resultData.sourceReference);
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "agent",
          actorType: session.actor_type,
          content: data.assistant_message,
          status: data.status,
          checkout_url: data.checkout_url,
          tool_result: data.tool_result,
          suggestions: session.actor_type === "merchant" ? examples.merchant : examples.customer,
        },
      ]);
    } catch (err) {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "agent",
          actorType: session.actor_type,
          content: err.message,
          status: "error",
          suggestions: ["Try again", "What can I do here?"],
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  if (!session) return <LoginScreen onLogin={handleLogin} />;

  return (
    <main className="h-screen overflow-hidden bg-ink text-white">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_15%_0%,rgba(102,242,194,0.14),transparent_30%),radial-gradient(circle_at_82%_8%,rgba(106,169,255,0.16),transparent_30%),linear-gradient(135deg,#05070c_0%,#0c111d_55%,#0f1725_100%)]" />
      <div className="relative flex h-full">
        <section className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-white/10 px-4 py-4 md:px-6">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-neon/12 text-neon"><MessageSquareText className="h-5 w-5" /></div>
              <div>
                <h1 className="font-semibold">Atlas Summit</h1>
                <p className="text-sm text-white/45">{session.actor_type === "merchant" ? "Merchant workspace" : "Customer checkout"}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={() => setCanvasOpen(!canvasOpen)} className="rounded-2xl border border-white/10 p-3 text-white/60 hover:text-white">
                {canvasOpen ? <PanelRightClose className="h-5 w-5" /> : <PanelRightOpen className="h-5 w-5" />}
              </button>
              <button onClick={logout} className="rounded-2xl border border-white/10 px-4 py-3 text-sm text-white/60 hover:text-white">Logout</button>
            </div>
          </header>

          <div ref={scrollRef} className="flex-1 space-y-5 overflow-y-auto px-4 py-6 md:px-6">
            <AnimatePresence initial={false}>
              {messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  onSuggestion={(suggestion) => sendMessage(null, suggestion)}
                  onTrack={trackPayout}
                  trackedSourceRef={trackedSourceRef}
                />
              ))}
            </AnimatePresence>
          </div>

          <div className="px-4 pb-5 md:px-6">
            <div className="mb-3 flex gap-2 overflow-x-auto pb-1">
              {examples[session.actor_type].map((suggestion) => (
                <button key={suggestion} onClick={() => sendMessage(null, suggestion)} className="shrink-0 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-white/55 hover:border-neon/40 hover:text-neon">
                  {suggestion}
                </button>
              ))}
            </div>
            <InputBar value={input} onChange={setInput} onSubmit={sendMessage} disabled={busy} actorType={session.actor_type} />
          </div>
        </section>

        <CanvasPanel open={canvasOpen} latest={latestStructured} statusCard={statusCard} onClose={() => setCanvasOpen(false)} />
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
