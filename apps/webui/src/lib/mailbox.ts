/** Mailbox synchronization: subscribe before snapshots, preserve newer local effects. */
import type { RestClient } from "../client/rest";
import type { SseClient } from "../client/sse";
import type { ConnectionStatus, MailItem, ServerEvent } from "../types";
import { involvesMe } from "./threads";

export interface MailboxState {
  mails: MailItem[];
  loading: boolean;
  loadingMore: boolean;
  syncing: boolean;
  marking: boolean;
  hasMore: boolean;
  notice: string | null;
}

type MailRest = Pick<RestClient, "getInboxPage" | "getMemberMailPage" | "setMailRead">;
type MailSse = Pick<SseClient, "on" | "off" | "getStatus">;

export class MailboxController {
  private state: MailboxState = { mails: [], loading: true, loadingMore: false, syncing: false, marking: false, hasMore: false, notice: null };
  private readonly listeners = new Set<() => void>();
  private readonly versions = new Map<string, { local: number; snapshot: number }>();
  private snapshotAnchor: string | null = null;
  private readonly readOverrides = new Map<string, boolean>();
  private revision = 0;
  private sequence = 0;
  private active = false;
  private generation = 0;
  private connected = false;
  private refreshing = false;
  private refreshAgain = false;
  private initialized = false;
  private nextBefore: string | null = null;

  constructor(private readonly rest: MailRest, private readonly sse: MailSse,
    private readonly username: string, private readonly memberId: string,
    readonly address: string, readonly readOnly: boolean) {}

  getSnapshot = (): MailboxState => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  private update(patch: Partial<MailboxState>): void {
    if (!this.active) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  start(): void {
    this.generation += 1;
    this.active = true;
    this.connected = false;
    this.sse.on("event", this.onEvent);
    this.sse.on("status", this.onStatus);
    this.onStatus(this.sse.getStatus());
  }
  stop(): void {
    this.active = false;
    this.generation += 1;
    this.refreshing = false;
    this.refreshAgain = false;
    this.sse.off("event", this.onEvent);
    this.sse.off("status", this.onStatus);
  }
  private onStatus = (status: ConnectionStatus): void => {
    const connected = status.state === "connected";
    if (connected && !this.connected) void this.refresh();
    this.connected = connected;
  };
  private normalize(mail: MailItem, realtime: boolean): MailItem {
    const incoming = mail.to.includes(this.address) || mail.cc.includes(this.address);
    const normalized = { ...mail };
    if (this.readOnly || !incoming) delete normalized.read;
    else if (realtime) normalized.read = this.state.mails.find((m) => m.id === mail.id)?.read
      ?? this.readOverrides.get(mail.id) ?? false;
    return normalized;
  }
  private onEvent = (event: ServerEvent): void => {
    if (!this.active || event.eventType !== "mail.delivered") return;
    const mail = (event.payload as { mail?: MailItem } | undefined)?.mail;
    if (!mail || typeof mail.id !== "string" || !involvesMe(mail, this.address)) return;
    // Delivery is an immutable fact, not a local write. In particular it
    // carries no read state and must not outrank an authoritative snapshot.
    if (this.state.mails.some((existing) => existing.id === mail.id)) return;
    this.update({ mails: [...this.state.mails, this.normalize(mail, true)] });
  };
  addLocal(mail: MailItem, notice?: string): void {
    if (!this.active || !involvesMe(mail, this.address)) return;
    this.revision += 1;
    this.versions.set(mail.id, { local: this.revision, snapshot: 0 });
    const mails = new Map(this.state.mails.map((m) => [m.id, m]));
    mails.set(mail.id, this.normalize(mail, true));
    this.update({ mails: [...mails.values()], ...(notice ? { notice } : {}) });
  }
  private page(before?: string) {
    return this.readOnly ? this.rest.getMemberMailPage(this.memberId, { before }) : this.rest.getInboxPage(this.username, { before });
  }
  private mergeSnapshot(incoming: MailItem[], revision: number, sequence: number): void {
    const mails = new Map(this.state.mails.map((m) => [m.id, m]));
    for (const mail of incoming) {
      const version = this.versions.get(mail.id);
      if (version && (version.snapshot > sequence || (version.local > revision && mails.has(mail.id)))) continue;
      const normalized = this.normalize(mail, false);
      if (version && version.local > revision && this.readOverrides.has(mail.id)) normalized.read = this.readOverrides.get(mail.id);
      mails.set(mail.id, normalized);
      this.versions.set(mail.id, { local: version?.local ?? 0, snapshot: sequence });
    }
    this.update({ mails: [...mails.values()] });
  }
  async refresh(): Promise<void> {
    if (!this.active) return;
    if (this.refreshing) { this.refreshAgain = true; return; }
    this.refreshing = true;
    this.update({ syncing: true, notice: null });
    const generation = this.generation;
    // Only a completed snapshot is a safe boundary; local sends can lie ahead of a gap.
    const anchor = this.snapshotAnchor;
    let nextAnchor: string | null = null;
    const wasInitialized = this.initialized;
    const revision = this.revision;
    const sequence = ++this.sequence;
    let before: string | undefined;
    try {
      do {
        const page = await this.page(before);
        if (!this.active || generation !== this.generation) return;
        if (before === undefined) nextAnchor = page.mails.at(-1)?.id ?? null;
        this.mergeSnapshot(page.mails, revision, sequence);
        if (!wasInitialized) {
          this.nextBefore = page.next_before;
          this.update({ hasMore: page.has_more });
        }
        if (!wasInitialized || !page.has_more || page.mails.some((mail) => mail.id === anchor)) break;
        if (!page.next_before || page.next_before === before) throw new Error("邮箱分页游标未前进");
        before = page.next_before;
      } while (this.active);
      this.snapshotAnchor = nextAnchor;
      this.initialized = true;
    } catch (error) {
      if (generation !== this.generation) return;
      this.update({ notice: `MAIL 同步失败: ${error instanceof Error ? error.message : String(error)}` });
    } finally {
      if (generation !== this.generation) return;
      this.refreshing = false;
      this.update({ loading: false, syncing: false });
      if (this.refreshAgain && this.active) { this.refreshAgain = false; void this.refresh(); }
    }
  }
  async loadMore(): Promise<void> {
    if (!this.active || this.state.loadingMore || !this.nextBefore) return;
    this.update({ loadingMore: true });
    const generation = this.generation;
    const revision = this.revision;
    const sequence = ++this.sequence;
    try {
      const page = await this.page(this.nextBefore);
      if (!this.active || generation !== this.generation) return;
      this.mergeSnapshot(page.mails, revision, sequence);
      this.nextBefore = page.next_before;
      this.update({ hasMore: page.has_more });
    } catch (error) {
      if (generation !== this.generation) return;
      this.update({ notice: `历史邮件加载失败: ${error instanceof Error ? error.message : String(error)}` });
    } finally { if (generation === this.generation) this.update({ loadingMore: false }); }
  }
  async setRead(read: boolean, target: { mail_id?: string; thread_id?: string }): Promise<void> {
    if (!this.active || this.readOnly || this.state.marking) return;
    const generation = this.generation;
    this.update({ marking: true });
    try {
      const result = await this.rest.setMailRead(this.username, read, target);
      if (!this.active || generation !== this.generation) return;
      this.revision += 1;
      const ids = new Set(result.mail_ids);
      ids.forEach((id) => {
        this.readOverrides.set(id, result.read);
        this.versions.set(id, { local: this.revision, snapshot: 0 });
      });
      this.update({ mails: this.state.mails.map((mail) => ids.has(mail.id) ? { ...mail, read: result.read } : mail), notice: result.read ? "已标记为已读" : "已标记为未读" });
    } catch (error) {
      if (generation !== this.generation) return;
      this.update({ notice: `标记失败: ${error instanceof Error ? error.message : String(error)}` });
    } finally { if (generation === this.generation) this.update({ marking: false }); }
  }
}
