type JsonRpcMessage = {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: any;
  result?: any;
  error?: any;
};

export class LspClient {
  private ws: WebSocket;
  private nextId = 1;
  private pending = new Map<
    number,
    { resolve: (v: any) => void; reject: (e: any) => void }
  >();
  private notificationHandlers = new Map<string, (params: any) => void>();
  private openPromiseResolve!: () => void;
  private ready: Promise<void>;
  private workspacePathResolve!: (path: string) => void;
  public workspacePath: Promise<string>;

  constructor(url: string) {
    this.ready = new Promise((resolve) => (this.openPromiseResolve = resolve));
    this.ws = new WebSocket(url);
    this.ws.onopen = () => {
      console.log("[LSP] WebSocket opened");
      this.openPromiseResolve();
    };
    this.workspacePath = new Promise((resolve) => (this.workspacePathResolve = resolve));
    this.ws.onmessage = (ev) => {
      const raw = ev.data as string;
      try {
        const parsed = JSON.parse(raw);
        if (parsed.type === "workspace-info") {
          this.workspacePathResolve(parsed.path);
          return; // jangan diteruskan ke handleData/dispatch
        }
      } catch {}
      this.handleData(raw);
    };
    this.ws.onerror = (e) => console.error("[LSP] WebSocket error", e);
    this.ws.onclose = (e) => {
      console.warn("[LSP] WebSocket closed", {
        code: e.code,
        reason: e.reason,
        wasClean: e.wasClean,
      });
      for (const { reject } of this.pending.values())
        reject(new Error("LSP connection closed"));
      this.pending.clear();
    };
  }

  private handleData(data: string) {
    console.log("[LSP] raw data received:", JSON.stringify(data));

    try {
      const msg: JsonRpcMessage = JSON.parse(data);
      this.dispatch(msg);
    } catch (e) {
      console.error("LSP parse error", e);
    }
  }

  private dispatch(msg: JsonRpcMessage) {
    if (
      msg.id !== undefined &&
      (msg.result !== undefined || msg.error !== undefined)
    ) {
      const p = this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.error) p.reject(msg.error);
      else p.resolve(msg.result);
    } else if (msg.method) {
      const handler = this.notificationHandlers.get(msg.method);
      handler?.(msg.params);
    }
  }

  private send(msg: JsonRpcMessage) {
    const body = JSON.stringify(msg);
    const framed = `Content-Length: ${new TextEncoder().encode(body).length}\r\n\r\n${body}`;
    this.ws.send(framed);
  }

  async request<T = any>(method: string, params?: any): Promise<T> {
    await this.ready;
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  async notify(method: string, params?: any) {
    await this.ready;
    this.send({ jsonrpc: "2.0", method, params });
  }

  onNotification(method: string, handler: (params: any) => void) {
    this.notificationHandlers.set(method, handler);
  }

  dispose() {
    this.ws.close();
  }
}
