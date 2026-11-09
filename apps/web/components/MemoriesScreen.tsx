"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Pencil, Trash2, X } from "lucide-react";
import { deleteMemory, listMemories, updateMemory, type Memory } from "@/lib/memories";
import { TopBar } from "./ui";

export function MemoriesScreen({ memoryEnabled }: { memoryEnabled: boolean }) {
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      setMemories(await listMemories());
    } catch {
      setError("Couldn't load your memories. Is the server running?");
      setMemories([]);
    }
  }, []);

  useEffect(() => {
    // Initial fetch on mount; state is set when the request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function saveEdit(memory: Memory) {
    const content = draft.trim();
    if (!content || content === memory.content) return setEditingId(null);
    try {
      await updateMemory(memory.id, content);
      setMemories((list) => list?.map((m) => (m.id === memory.id ? { ...m, content } : m)) ?? null);
      setEditingId(null);
    } catch {
      setError("Couldn't save that change.");
    }
  }

  async function remove(memory: Memory) {
    try {
      await deleteMemory(memory.id);
      setMemories((list) => list?.filter((m) => m.id !== memory.id) ?? null);
    } catch {
      setError("Couldn't delete that memory.");
    }
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <TopBar backHref="/settings" title="Memories" />
      <main className="page-col pb-safe flex flex-1 flex-col gap-space-sm px-margin py-space-md">
        <p className="type-body-md text-on-surface-variant">
          {memoryEnabled
            ? "Things Oryn remembers about you. Edit or delete anything, anytime."
            : "Memory is turned off, so Oryn isn't saving anything new. You can still manage what's saved."}
        </p>

        {error && (
          <p role="alert" className="type-body-sm rounded-xl bg-error-container/60 px-4 py-3 text-on-error-container">
            {error}
          </p>
        )}
        {memories === null && <p className="type-body-md py-space-xl text-center text-outline">Loading…</p>}
        {memories?.length === 0 && !error && (
          <p className="type-body-md py-space-xl text-center text-outline">Nothing saved yet. Talk to Oryn and it will show up here.</p>
        )}

        <ul className="flex flex-col gap-space-sm">
          {memories?.map((memory) => (
            <li key={memory.id} className="rounded-xl bg-surface-container-lowest p-space-md shadow-[0_4px_20px_-2px_rgba(31,32,36,0.04)]">
              {editingId === memory.id ? (
                <div className="flex flex-col gap-space-sm">
                  <label className="sr-only" htmlFor={`memory-${memory.id}`}>Memory</label>
                  <textarea
                    id={`memory-${memory.id}`}
                    autoFocus
                    rows={3}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    className="type-body-md w-full resize-none rounded-lg border border-outline-variant/40 bg-surface p-3 focus:border-primary-container focus:outline-none"
                  />
                  <div className="flex justify-end gap-space-sm">
                    <button type="button" aria-label="Cancel" onClick={() => setEditingId(null)} className="grid size-11 place-items-center rounded-full bg-surface-container text-on-surface-variant">
                      <X size={20} />
                    </button>
                    <button type="button" aria-label="Save" onClick={() => void saveEdit(memory)} className="grid size-11 place-items-center rounded-full bg-primary text-on-primary">
                      <Check size={20} />
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-space-sm">
                  <div className="min-w-0">
                    <p className="type-body-md break-words text-on-surface">{memory.content}</p>
                    {memory.category && (
                      <span className="type-label-sm mt-space-xs inline-block rounded-full bg-primary-fixed/50 px-2.5 py-0.5 text-on-primary-fixed-variant">
                        {memory.category.replace("_", " ")}
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0">
                    <button
                      type="button"
                      aria-label="Edit memory"
                      onClick={() => {
                        setEditingId(memory.id);
                        setDraft(memory.content);
                      }}
                      className="grid size-11 place-items-center rounded-full text-on-surface-variant hover:bg-surface-container"
                    >
                      <Pencil size={18} />
                    </button>
                    <button
                      type="button"
                      aria-label="Delete memory"
                      onClick={() => void remove(memory)}
                      className="grid size-11 place-items-center rounded-full text-error hover:bg-error-container/50"
                    >
                      <Trash2 size={18} />
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
