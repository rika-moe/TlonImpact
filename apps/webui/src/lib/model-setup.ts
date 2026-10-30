import type { ModelSetupRequest, ModelSetupStatus, RestClient } from "../client/rest";

type SetupRest = Pick<RestClient, "modelSetupStatus" | "configureModels">;
type SetupState = { status: ModelSetupStatus | null; busy: boolean; error: string | null; saved: boolean };

/** Holds no credentials: the form owns and clears its password input. */
export class ModelSetupController {
  private state: SetupState = { status: null, busy: false, error: null, saved: false };
  private readonly listeners = new Set<() => void>();
  private generation = 0;
  constructor(private readonly rest: SetupRest) {}
  getSnapshot = (): SetupState => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private update(patch: Partial<SetupState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  async load(): Promise<void> {
    const generation = ++this.generation;
    this.update({ busy: true, error: null });
    try {
      const status = await this.rest.modelSetupStatus();
      if (generation === this.generation) this.update({ status });
    } catch {
      if (generation === this.generation) this.update({ error: "无法读取模型配置，请重试。" });
    } finally {
      if (generation === this.generation) this.update({ busy: false });
    }
  }
  async save(body: ModelSetupRequest): Promise<boolean> {
    if (this.state.busy) return false;
    ++this.generation;
    this.update({ busy: true, error: null, saved: false });
    try {
      const status = await this.rest.configureModels(body);
      if (status.required) throw new Error("配置尚未完成，请重试。");
      this.update({ status, saved: true });
      return true;
    } catch (error) {
      // Server errors are sanitized. Never include the request/password here.
      this.update({ error: error instanceof Error ? error.message : "配置失败，请重试。" });
      return false;
    } finally {
      this.update({ busy: false });
    }
  }
}
