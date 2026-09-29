import * as SecureStore from 'expo-secure-store';

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
  addAppsSuccess?: boolean;
  kodiImage?: string;
  kodiImagePath?: string;
  volumeLevel?: number;
  brightnessLevel?: number;
};

export type PointerEventType = 'move' | 'tap' | 'click' | 'right_click' | 'scroll';
export type SpecialKey =
  | 'ENTER'
  | 'SPACE'
  | 'BACKSPACE'
  | 'ESCAPE'
  | 'TAB'
  | 'DELETE'
  | 'END'
  | 'PAGE_UP'
  | 'PAGE_DOWN'
  | 'F5'
  | 'A'
  | 'C'
  | 'V'
  | 'X'
  | 'Z';
export type KeyModifier = 'ctrl' | 'alt' | 'shift';

export interface RemoteRepository {
  connect(config?: Partial<ConnectionConfig>): Promise<void>;
  disconnect(): void;
  dispose(): void;
  getDemoApps(): DemoApp[];
  sendAction(action: string): void;
  sendPointerEvent(event: PointerEventType, payload?: { dx?: number; dy?: number }): void;
  sendSpecialKey(key: SpecialKey, modifiers?: KeyModifier[]): void;
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

  sendSpecialKey(key: SpecialKey, modifiers?: KeyModifier[]) {
    const label = modifiers?.length ? `${modifiers.join('+')}+${key}` : key;
    this.emit({
      lastAction: `Key ${label}`,
      lastMessage: `Demo key: ${label}`,
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
  private usedToken = false;
  private authMode: 'password' | 'pairing' = 'password';
  private isRecordingState = false;
  private recordedActions: Array<{ payload: Record<string, any>; delayMs: number }> = [];
  private lastRecordTime = 0;

  constructor(private readonly emit: RepositoryListener) {}

  private tokenKey(): string {
    const target = `${this.latestConfig.ipAddress}_${this.latestConfig.port}`;
    return `linuxtv_token_${target.replace(/[^A-Za-z0-9._-]/g, '_')}`;
  }

  private async loadToken(): Promise<string | null> {
    try {
      return await SecureStore.getItemAsync(this.tokenKey());
    } catch {
      return null;
    }
  }

  private async saveToken(token: string) {
    try {
      await SecureStore.setItemAsync(this.tokenKey(), token);
    } catch {
      // Without secure storage the phone just re-authenticates with the password next time.
    }
  }

  private async clearToken() {
    try {
      await SecureStore.deleteItemAsync(this.tokenKey());
    } catch {
      // ignore
    }
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
        void this.authenticate();
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

  sendSpecialKey(key: SpecialKey, modifiers?: KeyModifier[]) {
    const label = modifiers?.length ? `${modifiers.join('+')}+${key}` : key;
    this.sendPayload({ type: 'key', key, ...(modifiers?.length ? { modifiers } : {}) }, `Key ${label}`);
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
    if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
      return false;
    }

    const token = await this.loadToken();
    if (token) {
      this.usedToken = true;
      this.authStatus = 'Authenticating...';
      this.emit({ authStatus: 'Authenticating...' });
      activeSocket.send(JSON.stringify({ type: 'auth_token', token }));
      return true;
    }
    this.usedToken = false;

    const selectedUsername = this.latestConfig.username.trim();
    const selectedPassword = this.latestConfig.password;
    if (this.authMode === 'pairing') {
      // The desktop has no password yet: the "password" field carries the code shown on the TV.
      if (!selectedPassword) {
        this.authStatus = 'Authentication required';
        this.emit({
          authStatus: 'Authentication required',
          lastMessage: 'Enter the pairing code shown on the TV (Settings > Remote Login) as the password',
        });
        return false;
      }
      this.authStatus = 'Authenticating...';
      this.emit({ authStatus: 'Authenticating...' });
      activeSocket.send(JSON.stringify({ type: 'pair', code: selectedPassword.trim() }));
      return true;
    }

    if (!selectedUsername || !selectedPassword) {
      this.authStatus = 'Authentication required';
      this.emit({
        authStatus: 'Authentication required',
        lastMessage: 'Sign in with the desktop username and password',
      });
      return false;
    }

    this.authStatus = 'Authenticating...';
    this.emit({ authStatus: 'Authenticating...' });
    activeSocket.send(
      JSON.stringify({ type: 'auth', username: selectedUsername, password: selectedPassword })
    );
    return true;
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
        token?: string;
        mode?: string;
        sink?: string;
        image?: string;
        path?: string;
        volume?: number;
        brightness?: number;
      };

      if (payload.status === 'auth_ok') {
        if (payload.token) {
          void this.saveToken(payload.token);
        }
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
        if (this.usedToken) {
          // Token was revoked (password changed on the TV): fall back to the password once.
          this.usedToken = false;
          void this.clearToken().then(() => this.authenticate());
          return;
        }
        this.pendingOutbound = null;
        this.authStatus = 'Authentication failed';
        this.emit({
          authStatus: 'Authentication failed',
          lastMessage: payload.error ?? 'Invalid credentials',
        });
        return;
      }

      if (payload.status === 'auth_required') {
        this.authMode = payload.mode === 'pairing' ? 'pairing' : 'password';
        this.authStatus = 'Authentication required';
        this.emit({
          authStatus: 'Authentication required',
          lastMessage: 'Sign in with the desktop username and password',
        });
        void this.authenticate();
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
            addAppsSuccess: payload.status === 'ok',
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
