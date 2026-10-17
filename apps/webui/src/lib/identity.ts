import type { RestClient, WebuiIdentity } from "../client/rest";
import { RestError } from "../client/rest";

type IdentityRest = Pick<RestClient, "identity" | "initializeIdentity">;
export type IdentityState = {
  identity: WebuiIdentity | null;
  busy: boolean;
  error: string | null;
};

/** Load the instance identity before opening any business API or event stream. */
export class IdentityController {
  private state: IdentityState = { identity: null, busy: true, error: null };
  private generation = 0;
  private readonly listeners = new Set<() => void>();
  constructor(private readonly rest: IdentityRest) {}
  getSnapshot = (): IdentityState => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private update(patch: Partial<IdentityState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  async load(): Promise<void> {
    const generation = ++this.generation;
    this.update({ busy: true, error: null });
    try {
      const identity = await this.rest.identity();
      if (generation === this.generation) this.update({ identity });
    } catch (error) {
      if (generation === this.generation) this.update({ error: error instanceof Error ? error.message : String(error) });
    } finally {
      if (generation === this.generation) this.update({ busy: false });
    }
  }
  async initialize(username: string): Promise<void> {
    if (this.state.busy) return;
    ++this.generation;
    this.update({ busy: true, error: null });
    try {
      this.update({ identity: await this.rest.initializeIdentity(username.trim()) });
    } catch (error) {
      if (error instanceof RestError && error.status === 409) {
        await this.load();
        if (!this.state.identity?.username && !this.state.error) this.update({ error: error.message });
      } else {
        this.update({ error: error instanceof Error ? error.message : String(error) });
      }
    } finally {
      this.update({ busy: false });
    }
  }
}
