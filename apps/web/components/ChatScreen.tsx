"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Mic, SendHorizontal } from "lucide-react";
import { useChatSession } from "@/hooks/useChatSession";
import { MAX_TEXT_CHARS } from "@/lib/realtime/protocol";
import { IconLink, TopBar } from "./ui";

/** Height of the visible viewport, so the composer stays above the on-screen keyboard (iOS included). */
function useVisibleHeight(): number | null {
  const [height, setHeight] = useState<number | null>(null);
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const update = () => setHeight(viewport.height);
    update();
    viewport.addEventListener("resize", update);
    return () => viewport.removeEventListener("resize", update);
  }, []);
  return height;
}

export function ChatScreen({ language }: { language: string }) {
  const chat = useChatSession({ language });
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const visibleHeight = useVisibleHeight();
  const ready = chat.connection === "ready";

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [chat.messages, chat.typing, visibleHeight]);

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (chat.send(draft)) {
      setDraft("");
      if (inputRef.current) inputRef.current.style.height = "auto";
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  const statusText =
    chat.connection === "ready"
      ? null
      : chat.connection === "closed"
        ? (chat.error ?? "Disconnected")
        : chat.connection === "reconnecting"
          ? "Reconnecting…"
          : "Connecting…";

  return (
    <div className="flex flex-col" style={{ height: visibleHeight ? `${visibleHeight}px` : "100dvh" }}>
      <TopBar
        wide
        backHref="/talk"
        title="Chat"
        right={
          <IconLink href="/talk" label="Switch to voice">
            <Mic size={22} />
          </IconLink>
        }
      />

      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="page-col-wide flex min-h-full flex-col justify-end gap-space-sm px-margin py-space-md">
          {chat.messages.length === 0 && (
            <p className="type-body-md py-space-xl text-center text-outline">Say hello — I&apos;m listening.</p>
          )}
          {chat.messages.map((message) => (
            <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
              <p
                className={`type-body-md max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-4 py-3 ${
                  message.role === "user"
                    ? "rounded-tr-sm bg-primary-container/20 text-on-surface"
                    : "rounded-tl-sm border border-outline-variant/30 bg-surface-container-lowest text-on-surface shadow-[0_4px_20px_-2px_rgba(31,32,36,0.04)]"
                }`}
              >
                {message.text}
                {message.interrupted && <span className="type-label-sm ml-1 text-outline">· interrupted</span>}
              </p>
            </div>
          ))}
          {chat.typing && (
            <div className="flex justify-start" aria-label="Oryn is typing">
              <div className="flex items-center gap-1.5 rounded-2xl rounded-tl-sm border border-outline-variant/30 bg-surface-container-lowest px-4 py-4">
                {[0, 150, 300].map((delay) => (
                  <span key={delay} className="size-2 animate-bounce rounded-full bg-primary-container" style={{ animationDelay: `${delay}ms` }} />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="pb-safe border-t border-outline-variant/20 bg-surface/95 backdrop-blur-xl">
        <div className="page-col-wide px-margin pt-space-sm">
          {(statusText || chat.error) && (
            <p role="status" className="type-body-sm pb-space-xs text-center text-on-surface-variant">
              {statusText ?? chat.error}
            </p>
          )}
          <form onSubmit={submit} className="flex items-end gap-space-sm pb-space-sm">
            <label className="sr-only" htmlFor="composer">Message</label>
            <textarea
              id="composer"
              ref={inputRef}
              rows={1}
              value={draft}
              maxLength={MAX_TEXT_CHARS}
              onChange={(event) => {
                setDraft(event.target.value);
                event.target.style.height = "auto";
                event.target.style.height = `${Math.min(event.target.scrollHeight, 128)}px`;
              }}
              onKeyDown={onKeyDown}
              placeholder="Type a message…"
              className="type-body-md max-h-32 min-h-12 flex-1 resize-none rounded-3xl border border-outline-variant/40 bg-surface-container-lowest px-5 py-3 text-on-surface placeholder:text-outline/70 focus:border-primary-container focus:outline-none focus:ring-4 focus:ring-primary-container/25"
            />
            <button
              type="submit"
              aria-label="Send"
              disabled={!ready || !draft.trim()}
              className="grid size-12 shrink-0 place-items-center rounded-full bg-primary text-on-primary shadow-md transition-all active:scale-90 disabled:opacity-40"
            >
              <SendHorizontal size={22} />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
