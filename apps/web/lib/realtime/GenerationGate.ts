import { generationOf, type ServerEvent } from "./protocol";

export type Admission = "new" | "current" | "drop";

/**
 * Tracks which assistant generation the client still cares about. Ids only grow within a
 * session, so "newer" adopts, "same" passes and "older" or retired ids are dropped. A retired id
 * (barge-in, cancel) stays dropped even if the server keeps streaming it for a few more ms.
 */
export class GenerationGate {
  private currentId: number | null = null;
  private retiredThrough = -1;

  get current(): number | null {
    return this.currentId;
  }

  admit(id: number): Admission {
    if (id <= this.retiredThrough) return "drop";
    if (this.currentId === null || id > this.currentId) {
      this.currentId = id;
      return "new";
    }
    return id === this.currentId ? "current" : "drop";
  }

  /** Marks `id` and everything older as dead. Synchronous; later frames of `id` are dropped. */
  retire(id: number): void {
    this.retiredThrough = Math.max(this.retiredThrough, id);
    if (this.currentId !== null && this.currentId <= id) this.currentId = null;
  }

  /** New socket/session: server ids are not comparable to the previous connection's. */
  reset(): void {
    this.currentId = null;
    this.retiredThrough = -1;
  }
}

/**
 * Decides whether a parsed server event may reach the app. Generation-scoped events of a stale
 * generation are dropped; `turn.cancelled` retires its generation once delivered.
 */
export function admitEvent(gate: GenerationGate, event: ServerEvent): boolean {
  const generationId = generationOf(event);
  if (generationId === undefined) return true;
  if (gate.admit(generationId) === "drop") return false;
  if (event.type === "turn.cancelled") gate.retire(generationId);
  return true;
}
