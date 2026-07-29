export type ConnectionState = 'Disconnected' | 'Connecting...' | 'Connected' | 'Error';
export type AuthState =
  | 'Saved credentials loaded'
  | 'No saved credentials'
  | 'Authenticating...'
  | 'Authenticated'
  | 'Authentication required'
  | 'Authentication failed';

export type ConnectionConfig = {
  ipAddress: string;
  password: string;
  port: string;
  username: string;
};

export type DemoApp = {
  id: string;
  name: string;
  subtitle: string;
};

export type RepositoryState = {
  authStatus: AuthState;
  deviceName: string;
  isDemoMode: boolean;
  lastAction: string;
  lastMessage: string;
  status: ConnectionState;
  appsList?: Array<{id: string; name: string; icon?: string; kind?: string; category?: string}>;
  wifiNetworks?: Array<{ssid: string; label: string; security?: string; signal?: number}>;
  currentWifi?: string;
  wifiMessage?: string;
  bluetoothDevices?: Array<{mac: string; name: string; label: string; connected?: boolean; paired?: boolean}>;
  currentBluetooth?: string;
  bluetoothMessage?: string;
  soundSpeakers?: Array<{name: string; label: string}>;
  defaultSink?: string;
  soundMessage?: string;
  addAppsMessage?: string;
  kodiImage?: string;
  kodiImagePath?: string;
  volumeLevel?: number;
  brightnessLevel?: number;
};

export type PointerEventType = 'move' | 'tap' | 'click' | 'right_click' | 'scroll';
export type SpecialKey = 'ENTER' | 'SPACE' | 'BACKSPACE' | 'ESCAPE' | 'TAB';

export interface RemoteRepository {
  connect(config?: Partial<ConnectionConfig>): Promise<void>;
  disconnect(): void;
  dispose(): void;
  getDemoApps(): DemoApp[];
  sendAction(action: string): void;
  sendPointerEvent(event: PointerEventType, payload?: { dx?: number; dy?: number }): void;
  sendSpecialKey(key: SpecialKey): void;
  sendText(text: string): void;
  sendSettingsRequest(type: string, payload?: Record<string, any>): void;
  addApp(app: { type: string; name: string; command?: string; url?: string }): void;
  removeApp(appId: string): void;
  startRecording(): void;
  stopRecording(): { payload: Record<string, any>; delayMs: number }[];
  replayMacro(actions: { payload: Record<string, any>; delayMs: number }[]): Promise<void>;
  isRecording(): boolean;
}

type RepositoryListener = (update: Partial<RepositoryState>) => void;

const DEFAULT_PORT = '8765';
const RECONNECT_DELAY_MS = 3000;

const DEMO_APPS: DemoApp[] = [
  { id: 'demo-youtube', name: 'YouTube', subtitle: 'Streaming' },
  { id: 'demo-netflix', name: 'Netflix', subtitle: 'Movies & TV' },
  { id: 'demo-kodi', name: 'Kodi', subtitle: 'Media Center' },
  { id: 'demo-browser', name: 'Browser', subtitle: 'Web Apps' },
];

export class DemoRepository implements RemoteRepository {
  constructor(private readonly emit: RepositoryListener) {}

  async connect(): Promise<void> {
    this.emit({
      authStatus: 'Authenticated',
      deviceName: 'Demo LinuxTV Device',
      isDemoMode: true,
      lastMessage: 'Demo mode is ready. Explore the remote without a server.',
      status: 'Connected',
    });
  }

  disconnect() {
    this.emit({
      authStatus: 'No saved credentials',
      deviceName: '',
      isDemoMode: false,
      lastAction: 'None',
      lastMessage: 'Demo mode closed.',
      status: 'Disconnected',
    });
  }

  dispose() {
    this.disconnect();
  }

  getDemoApps() {
    return DEMO_APPS;
  }

  sendAction(action: string) {
    const label = action.replaceAll('_', ' ');
    this.emit({
      lastAction: label,
      lastMessage: `Demo action: ${label}`,
    });
  }

  sendPointerEvent(event: PointerEventType, payload?: { dx?: number; dy?: number }) {
    if (event === 'move') {
      this.emit({
        lastMessage: `Demo pointer moved ${payload?.dx ?? 0}, ${payload?.dy ?? 0}`,
      });
      return;
    }

    const label =
      event === 'tap'
        ? 'Touchpad tap'
        : event === 'click'
          ? 'Left click'
          : 'Right click';
    this.emit({
      lastAction: label,
      lastMessage: `Demo action: ${label}`,
    });
  }

  sendSpecialKey(key: SpecialKey) {
    this.emit({
      lastAction: `Key ${key}`,
      lastMessage: `Demo key: ${key}`,
    });
  }

  sendText(text: string) {
    this.emit({
      lastAction: 'Typed text',
      lastMessage: `Demo text sent: ${text}`,
    });
  }

  sendSettingsRequest(type: string, payload?: Record<string, any>) {
    this.emit({
      lastAction: 'Settings Request',
      lastMessage: `Demo settings request: ${type}`,
    });
  }

  addApp(app: { type: string; name: string; command?: string; url?: string }) {
    this.emit({
      lastAction: 'Add App',
      lastMessage: `Demo: Added app "${app.name}"`,
    });
  }

  removeApp(appId: string) {
    this.emit({
      lastAction: 'Remove App',
      lastMessage: `Demo: Removed app ${appId}`,
    });
  }

  startRecording(): void {
    this.emit({ lastMessage: 'Demo: Started recording' });
  }

  stopRecording(): { payload: Record<string, any>; delayMs: number }[] {
    this.emit({ lastMessage: 'Demo: Stopped recording' });
    return [];
  }

  async replayMacro(
    _actions: { payload: Record<string, any>; delayMs: number }[]
  ): Promise<void> {
    this.emit({ lastMessage: 'Demo: Replaying macro' });
  }

  isRecording(): boolean {
    return false;
  }
}

export class RealServerRepository implements RemoteRepository {
  private socket: WebSocket | null = null;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private authStatus: AuthState = 'No saved credentials';
  private pendingOutbound:
    | {
        label: string;
        message: string;
        trackLastAction: boolean;
        updateLastMessage: boolean;
      }
    | null = null;
  private latestConfig: ConnectionConfig = {
    ipAddress: '',
    password: '',
    port: DEFAULT_PORT,
    username: '',
  };
  private shouldReconnect = false;
  private passwordHash: string | null = null; // Store hashed password for challenge-response
  private isRecordingState = false;
  private recordedActions: Array<{ payload: Record<string, any>; delayMs: number }> = [];
  private lastRecordTime = 0;

  constructor(private readonly emit: RepositoryListener) {}

  private sha256(message: string): string {
    // Pure JavaScript SHA-256 implementation for React Native compatibility
    function rotateRight(n: number, x: number): number {
      return (x >>> n) | (x << (32 - n));
    }
    function ch(x: number, y: number, z: number): number {
      return (x & y) ^ (~x & z);
    }
    function maj(x: number, y: number, z: number): number {
      return (x & y) ^ (x & z) ^ (y & z);
    }
    function sigma0(x: number): number {
      return rotateRight(2, x) ^ rotateRight(13, x) ^ rotateRight(22, x);
    }
    function sigma1(x: number): number {
      return rotateRight(6, x) ^ rotateRight(11, x) ^ rotateRight(25, x);
    }
    function gamma0(x: number): number {
      return rotateRight(7, x) ^ rotateRight(18, x) ^ (x >>> 3);
    }
    function gamma1(x: number): number {
      return rotateRight(17, x) ^ rotateRight(19, x) ^ (x >>> 10);
    }

    const K = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];

    const encoder = new TextEncoder();
    const data = encoder.encode(message);
    const bitLen = data.length * 8;
    
    // Padding
    const padLen = data.length + 1 + ((119 - data.length) % 64);
    const padded = new Uint8Array(padLen + 8);
    padded.set(data);
    padded[data.length] = 0x80;
    
    // Append original length in bits as 64-bit big-endian
    const view = new DataView(padded.buffer);
    const hi = Math.floor(bitLen / 0x100000000);
    const lo = bitLen >>> 0;
    view.setUint32(padLen, hi, false);  // big-endian
    view.setUint32(padLen + 4, lo, false);  // big-endian

    // Initialize hash values
    let h0 = 0x6a09e667;
    let h1 = 0xbb67ae85;
    let h2 = 0x3c6ef372;
    let h3 = 0xa54ff53a;
    let h4 = 0x510e527f;
    let h5 = 0x9b05688c;
    let h6 = 0x1f83d9ab;
    let h7 = 0x5be0cd19;

    // Process each 512-bit block
    for (let i = 0; i < padded.length; i += 64) {
      const w = new Uint32Array(64);
      for (let j = 0; j < 16; j++) {
        w[j] = view.getUint32(i + j * 4, false);  // big-endian
      }
      for (let j = 16; j < 64; j++) {
        w[j] = (gamma1(w[j - 2]) + w[j - 7] + gamma0(w[j - 15]) + w[j - 16]) | 0;
      }

      let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;

      for (let j = 0; j < 64; j++) {
        const t1 = (h + sigma1(e) + ch(e, f, g) + K[j] + w[j]) | 0;
        const t2 = (sigma0(a) + maj(a, b, c)) | 0;
        h = g;
        g = f;
        f = e;
        e = (d + t1) | 0;
        d = c;
        c = b;
        b = a;
        a = (t1 + t2) | 0;
      }

      h0 = (h0 + a) | 0;
      h1 = (h1 + b) | 0;
      h2 = (h2 + c) | 0;
      h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0;
      h5 = (h5 + f) | 0;
      h6 = (h6 + g) | 0;
      h7 = (h7 + h) | 0;
    }

    // Convert to hex string
    return [h0, h1, h2, h3, h4, h5, h6, h7]
      .map(h => (h >>> 0).toString(16).padStart(8, '0'))
      .join('');
  }

  private async computePasswordHash(password: string): Promise<string> {
    // Use pure JavaScript SHA-256 for React Native compatibility
    return this.sha256(password);
  }

  async connect(config?: Partial<ConnectionConfig>): Promise<void> {
    this.latestConfig = {
      ...this.latestConfig,
      ...config,
      ipAddress: (config?.ipAddress ?? this.latestConfig.ipAddress).trim(),
      port: (config?.port ?? this.latestConfig.port).trim() || DEFAULT_PORT,
    };

    if (!this.latestConfig.ipAddress) {
      this.emit({
        lastMessage: 'Enter the LinuxTV IP address to finish setup.',
        status: 'Disconnected',
      });
      return;
    }

    this.clearReconnectTimer();
    this.shouldReconnect = true;
    this.pendingOutbound = null;
    this.disconnectSocket();

    const target = `${this.latestConfig.ipAddress}:${this.latestConfig.port}`;
    this.emit({
      authStatus:
        this.latestConfig.username.trim() && this.latestConfig.password
          ? 'Saved credentials loaded'
          : 'No saved credentials',
      deviceName: `LinuxTV @ ${target}`,
      isDemoMode: false,
      lastMessage: `Trying ${target}`,
      status: 'Connecting...',
    });

    try {
      const ws = new WebSocket(`ws://${target}`);
      this.socket = ws;

      ws.onopen = () => {
        this.emit({
          deviceName: `LinuxTV @ ${target}`,
          lastMessage: 'Remote session is live',
          status: 'Connected',
        });
        if (this.latestConfig.username.trim() && this.latestConfig.password) {
          void this.authenticate();
        }
      };

      ws.onclose = () => {
        this.socket = null;
        this.emit({
          lastMessage: `Waiting for LinuxTV at ${target}`,
          status: 'Disconnected',
        });
        this.scheduleReconnect();
      };

      ws.onerror = () => {
        this.emit({
          lastMessage: `Unable to reach LinuxTV at ${target}`,
          status: 'Error',
        });
      };

      ws.onmessage = (event) => {
        this.handleMessage(String(event.data));
      };
    } catch {
      this.emit({
        lastMessage: 'Failed to create WebSocket',
        status: 'Error',
      });
      this.scheduleReconnect();
    }
  }

  disconnect() {
    this.shouldReconnect = false;
    this.pendingOutbound = null;
    this.authStatus = 'No saved credentials';
    this.clearReconnectTimer();
    this.disconnectSocket();
    this.emit({
      authStatus: 'No saved credentials',
      deviceName: '',
      isDemoMode: false,
      lastAction: 'None',
      lastMessage: 'Disconnected from LinuxTV.',
      status: 'Disconnected',
    });
  }

  dispose() {
    this.disconnect();
  }

  getDemoApps() {
    return [];
  }

  sendAction(action: string) {
    this.sendPayload({ action }, action);
  }

  sendPointerEvent(event: PointerEventType, payload?: { dx?: number; dy?: number }) {
    this.sendPayload(
      { type: 'pointer', event, ...payload },
      event === 'tap' ? 'Touchpad tap' : event === 'click' ? 'Left click' : 'Right click',
      {
        queueWhenAuthNeeded: event !== 'move',
        trackLastAction: event !== 'move',
        updateLastMessage: event !== 'move',
      }
    );
  }

  sendSpecialKey(key: SpecialKey) {
    this.sendPayload({ type: 'key', key }, `Key ${key}`);
  }

  sendText(text: string) {
    this.sendPayload({ type: 'text', text }, 'Typed text');
  }

  sendSettingsRequest(type: string, payload?: Record<string, any>) {
    this.sendPayload({ type, ...payload }, `Settings: ${type}`);
  }

  addApp(app: { type: string; name: string; command?: string; url?: string }) {
    const payload = {
      type: 'add_app',
      kind: app.type,
      name: app.name,
      ...(app.type === 'native' ? { command: app.command } : { url: app.url }),
    };
    this.sendPayload(payload, 'Add App');
  }

  removeApp(appId: string) {
    const payload = {
      type: 'remove_app',
      id: appId,
    };
    this.sendPayload(payload, 'Remove App');
  }

  startRecording(): void {
    this.isRecordingState = true;
    this.recordedActions = [];
    this.lastRecordTime = Date.now();
    this.emit({ lastMessage: 'Recording started' });
  }

  stopRecording(): { payload: Record<string, any>; delayMs: number }[] {
    this.isRecordingState = false;
    this.emit({ lastMessage: 'Recording stopped' });
    return this.recordedActions;
  }

  isRecording(): boolean {
    return this.isRecordingState;
  }

  async replayMacro(actions: { payload: Record<string, any>; delayMs: number }[]): Promise<void> {
    if (this.isRecordingState) {
      console.warn('Cannot replay a macro while recording.');
      return;
    }

    this.emit({ lastMessage: 'Replaying macro...' });

    for (const action of actions) {
      if (action.delayMs > 0) {
        await new Promise(resolve => setTimeout(resolve, action.delayMs));
      }
      this.sendPayload(action.payload, 'Replay Action', { updateLastMessage: false });
    }
    this.emit({ lastMessage: 'Macro replay finished.' });
  }

  private async authenticate() {
    const activeSocket = this.socket;
    const selectedUsername = this.latestConfig.username.trim();
    const selectedPassword = this.latestConfig.password;

    if (!selectedUsername || !selectedPassword) {
      this.authStatus = 'Authentication required';
      this.emit({
        authStatus: 'Authentication required',
        lastMessage: 'Sign in with the desktop username and password',
      });
      return false;
    }

    if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
      this.authStatus = 'Saved credentials loaded';
      this.emit({
        authStatus: 'Saved credentials loaded',
        lastMessage: 'Credentials saved on this phone. Waiting for LinuxTV to come online.',
      });
      return true;
    }

    this.authStatus = 'Authenticating...';
    this.emit({ authStatus: 'Authenticating...' });
    
    // Request a challenge from the server
    activeSocket.send(
      JSON.stringify({
        type: 'auth_challenge',
      })
    );
    return true;
  }

  private async handleAuthChallenge(nonce: string) {
    const activeSocket = this.socket;
    if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
      return;
    }

    const selectedUsername = this.latestConfig.username.trim();
    const selectedPassword = this.latestConfig.password;

    console.log('[Auth] Starting challenge-response auth');
    console.log('[Auth] Username:', selectedUsername);
    console.log('[Auth] Nonce:', nonce);

    // Compute password hash if not already done
    if (!this.passwordHash) {
      this.passwordHash = await this.computePasswordHash(selectedPassword);
      console.log('[Auth] Computed password hash:', this.passwordHash);
    }

    // Compute response: SHA-256(password_hash:nonce) using pure JS implementation
    const challengeString = `${this.passwordHash}:${nonce}`;
    console.log('[Auth] Challenge string:', challengeString);
    const responseHash = this.sha256(challengeString);
    console.log('[Auth] Response hash:', responseHash);

    // Send challenge response
    activeSocket.send(
      JSON.stringify({
        type: 'auth_response',
        username: selectedUsername,
        response: responseHash,
      })
    );
  }

  private sendPayload(
    payload: Record<string, unknown>,
    label: string,
    options?: {
      queueWhenAuthNeeded?: boolean;
      trackLastAction?: boolean;
      updateLastMessage?: boolean;
    }
  ) {
    const {
      queueWhenAuthNeeded = true,
      trackLastAction = true,
      updateLastMessage = true,
    } = options ?? {};

    if (this.isRecordingState) {
      // Skip recording pointer move events to avoid flooding the macro
      if (!(payload.type === 'pointer' && payload.event === 'move')) {
        const now = Date.now();
        const delayMs = this.lastRecordTime > 0 ? now - this.lastRecordTime : 0;
        // Deep copy of payload
        this.recordedActions.push({ payload: JSON.parse(JSON.stringify(payload)), delayMs });
        this.lastRecordTime = now;
      }
    }



    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      this.emit({ lastMessage: 'Waiting for LinuxTV to come online' });
      this.scheduleReconnect();
      return;
    }

    const message = JSON.stringify(payload);

    if (
      this.authStatus !== 'Authenticated' &&
      this.latestConfig.username.trim() &&
      this.latestConfig.password
    ) {
      this.pendingOutbound = queueWhenAuthNeeded
        ? {
            label,
            message,
            trackLastAction,
            updateLastMessage,
          }
        : null;
      if (queueWhenAuthNeeded) {
        void this.authenticate();
        return;
      }
    }

    this.socket.send(message);
    if (trackLastAction) {
      this.emit({ lastAction: label });
    }
    if (updateLastMessage) {
      this.emit({ lastMessage: `Sending ${label}` });
    }
  }

  private handleMessage(rawMessage: string) {
    try {
      const payload = JSON.parse(rawMessage) as {
        action?: string;
        apps?: Array<{id: string; name: string; icon?: string}>;
        error?: string;
        event?: string;
        key?: string;
        nonce?: string;
        status?: string;
        type?: string;
        networks?: Array<{ssid: string; label: string; security?: string; signal?: number}>;
        current_wifi?: string;
        message?: string;
        devices?: Array<{mac: string; name: string; label: string; connected?: boolean; paired?: boolean}>;
        current_bluetooth?: string;
        speakers?: Array<{name: string; label: string}>;
        default_sink?: string;
        success?: boolean;
        sink?: string;
        image?: string;
        path?: string;
        volume?: number;
        brightness?: number;
      };

      // Handle auth challenge from server
      if (payload.type === 'auth_challenge' && payload.nonce) {
        void this.handleAuthChallenge(payload.nonce);
        return;
      }

      if (payload.status === 'auth_ok') {
        this.authStatus = 'Authenticated';
        this.emit({
          authStatus: 'Authenticated',
          lastMessage: 'Authentication successful',
        });
        if (this.pendingOutbound && this.socket?.readyState === WebSocket.OPEN) {
          const pendingOutbound = this.pendingOutbound;
          this.pendingOutbound = null;
          this.socket.send(pendingOutbound.message);
          if (pendingOutbound.trackLastAction) {
            this.emit({ lastAction: pendingOutbound.label });
          }
          if (pendingOutbound.updateLastMessage) {
            this.emit({ lastMessage: `Sent ${pendingOutbound.label}` });
          }
        }
        return;
      }

      if (payload.status === 'auth_error') {
        this.pendingOutbound = null;
        this.authStatus = 'Authentication failed';
        this.emit({
          authStatus: 'Authentication failed',
          lastMessage: payload.error ?? 'Invalid credentials',
        });
        return;
      }

      if (payload.status === 'auth_required') {
        this.authStatus = 'Authentication required';
        this.emit({
          authStatus: 'Authentication required',
          lastMessage: 'Sign in with the desktop username and password',
        });
        if (this.latestConfig.username.trim() && this.latestConfig.password) {
          void this.authenticate();
        }
        return;
      }

      if (payload.status === 'ok') {
        this.authStatus = 'Authenticated';
        this.emit({
          authStatus: 'Authenticated',
        });

        // Handle apps list response
        if (payload.type === 'apps_list' && payload.apps) {
          this.emit({
            appsList: payload.apps as Array<{id: string; name: string; icon?: string}>,
          });
          return;
        }

        if (payload.type === 'pointer') {
          if (payload.event === 'tap') {
            this.emit({
              lastAction: 'Touchpad tap',
              lastMessage: 'Touchpad tap',
            });
          } else if (payload.event === 'click') {
            this.emit({
              lastAction: 'Left click',
              lastMessage: 'Left click',
            });
          } else if (payload.event === 'right_click') {
            this.emit({
              lastAction: 'Right click',
              lastMessage: 'Right click',
            });
          }
          return;
        }

        if (payload.type === 'text') {
          this.emit({ lastMessage: 'Typed text in the active app' });
          return;
        }

        if (payload.type === 'key') {
          const keyLabel = `Key ${payload.key ?? ''}`.trim();
          this.emit({
            lastAction: keyLabel,
            lastMessage: `Sent ${payload.key ?? 'key'}`,
          });
          return;
        }

        // Handle WiFi list response
        if (payload.type === 'wifi_list') {
          this.emit({
            wifiNetworks: payload.networks || [],
            currentWifi: payload.current_wifi || '',
            wifiMessage: payload.message || '',
          });
          return;
        }

        // Handle WiFi connect response
        if (payload.type === 'wifi_connected') {
          this.emit({
            wifiMessage: payload.message || '',
            currentWifi: payload.current_wifi || '',
          });
          return;
        }

        // Handle Bluetooth list response
        if (payload.type === 'bluetooth_list') {
          this.emit({
            bluetoothDevices: payload.devices || [],
            currentBluetooth: payload.current_bluetooth || '',
            bluetoothMessage: payload.message || '',
          });
          return;
        }

        // Handle Bluetooth connect response
        if (payload.type === 'bluetooth_connected') {
          this.emit({
            bluetoothMessage: payload.message || '',
            currentBluetooth: payload.current_bluetooth || '',
          });
          return;
        }

        // Handle Bluetooth remove response
        if (payload.type === 'bluetooth_removed') {
          this.emit({
            bluetoothMessage: payload.message || '',
          });
          return;
        }

        // Handle Sound list response
        if (payload.type === 'sound_list') {
          this.emit({
            soundSpeakers: payload.speakers || [],
            defaultSink: payload.default_sink || '',
            soundMessage: payload.message || '',
          });
          return;
        }

        // Handle Sound set response
        if (payload.type === 'sound_set') {
          this.emit({
            soundMessage: payload.message || '',
          });
          return;
        }

        // Handle Add App response
        if (payload.type === 'app_added') {
          this.emit({
            addAppsMessage: payload.message || '',
          });
          return;
        }

        // Handle Remove App response
        if (payload.type === 'app_removed') {
          this.emit({
            addAppsMessage: payload.message || '',
          });
          return;
        }
        
        // Handle Kodi Image response
        if (payload.type === 'kodi_image') {
          this.emit({
            kodiImage: payload.image || '',
            kodiImagePath: payload.path || '',
          });
          return;
        }

        // Handle Volume level response
        if (payload.type === 'volume_level' && payload.volume !== undefined) {
          console.log('[Repository] Received volume level:', payload.volume);
          this.emit({
            volumeLevel: payload.volume as number,
          });
          return;
        }

        // Handle Brightness level response
        if (payload.type === 'brightness_level' && payload.brightness !== undefined) {
          console.log('[Repository] Received brightness level:', payload.brightness);
          this.emit({
            brightnessLevel: payload.brightness as number,
          });
          return;
        }

        this.emit({ lastMessage: `Sent ${payload.action ?? 'command'}` });
        return;
      }
    } catch {
      this.emit({ lastMessage: rawMessage });
    }
  }

  private clearReconnectTimer() {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  private disconnectSocket() {
    if (!this.socket) {
      return;
    }

    this.socket.onopen = null;
    this.socket.onclose = null;
    this.socket.onerror = null;
    this.socket.onmessage = null;
    this.socket.close();
    this.socket = null;
  }

  private scheduleReconnect() {
    this.clearReconnectTimer();
    if (!this.shouldReconnect || !this.latestConfig.ipAddress.trim()) {
      return;
    }

    this.reconnectTimeout = setTimeout(() => {
      void this.connect();
    }, RECONNECT_DELAY_MS);
  }
}
