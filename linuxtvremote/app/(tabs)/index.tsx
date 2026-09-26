import { useEffect, useRef, useState } from 'react';
import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Haptics from 'expo-haptics';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { LinearGradient } from 'expo-linear-gradient';
import {
  Alert,
  AppState,
  AppStateStatus,
  DeviceEventEmitter,
  Image as RNImage,
  Modal,
  PanResponder,
  Pressable,
  ScrollView,
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextStyle,
  View,
  ViewStyle,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import type { ComponentProps, ReactNode } from 'react';

import {
  DemoRepository,
  type AuthState,
  type ConnectionState,
  type KeyModifier,
  RealServerRepository,
  type RemoteRepository,
  type RepositoryState,
  type SpecialKey,
} from '@/lib/remote-repository';

const HOST_KEY = 'linuxtv_remote_host';
const PORT_KEY = 'linuxtv_remote_port';
const USERNAME_KEY = 'linuxtv_remote_username';
const PASSWORD_KEY = 'linuxtv_remote_password';
const DEMO_MODE_KEY = 'linuxtv_remote_demo_mode';
const SYSTEMS_KEY = 'linuxtv_remote_systems';
const ACTIVE_SYSTEM_ID_KEY = 'linuxtv_remote_active_system_id';
const DEFAULT_PORT = '8765';
const MACROS_KEY = 'linuxtv_macros';
const REMOTE_REPEAT_DELAY_MS = 320;
const REMOTE_REPEAT_INTERVAL_MS = 90;

type ScreenRepositoryState = RepositoryState & {
  authStatus: AuthState;
  status: ConnectionState;
};

type TabType = 'remote' | 'apps' | 'keyboard' | 'touchpad' | 'macros';

type SavedSystem = {
  id: string;
  ipAddress: string;
  name: string;
  password: string;
  port: string;
  username: string;
};

type SavedMacro = {
  id: string;
  name: string;
  createdAt: number;
  actions: { payload: Record<string, any>; delayMs: number }[];
};

const DEFAULT_REPOSITORY_STATE: ScreenRepositoryState = {
  authStatus: 'No saved credentials',
  deviceName: '',
  isDemoMode: false,
  lastAction: 'None',
  lastMessage: 'Looking for saved LinuxTV remote info',
  status: 'Disconnected',
};

const createSystemId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const buildSystemName = (name: string, ipAddress: string) => name.trim() || ipAddress.trim();

const parseStoredSystems = (storedValue: string | null): SavedSystem[] => {
  if (!storedValue) {
    return [];
  }

  try {
    const parsedValue = JSON.parse(storedValue) as unknown;
    if (!Array.isArray(parsedValue)) {
      return [];
    }

    return parsedValue
      .map((entry) => {
        if (!entry || typeof entry !== 'object') {
          return null;
        }

        const candidate = entry as Partial<SavedSystem>;
        const ipAddress = candidate.ipAddress?.trim() ?? '';
        if (!ipAddress) {
          return null;
        }

        return {
          id: candidate.id?.trim() || createSystemId(),
          ipAddress,
          name: buildSystemName(candidate.name ?? '', ipAddress),
          password: candidate.password ?? '',
          port: candidate.port?.trim() || DEFAULT_PORT,
          username: candidate.username ?? '',
        };
      })
      .filter((entry): entry is SavedSystem => Boolean(entry));
  } catch {
    return [];
  }
};

// Glass surface: a translucent tint clipped to the wrapped view's own border
// radius. Real native blur (expo-blur's BlurView) doesn't respect rounded
// clipping reliably on Android -- it shows up as a rectangular light patch
// poking out from behind the rounded/circular content -- so this stays a
// plain tint instead of a literal blur.
function GlassSurface({
  style,
  tint = 'rgba(59, 66, 82, 0.55)',
  children,
}: {
  style?: StyleProp<ViewStyle>;
  tint?: string;
  children?: ReactNode;
}) {
  return (
    <View style={[styles.glassClip, style, { backgroundColor: tint }]}>
      {children}
    </View>
  );
}

export default function RemoteScreen() {
  const [systemName, setSystemName] = useState('');
  const [ipAddress, setIpAddress] = useState('');
  const [port, setPort] = useState(DEFAULT_PORT);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [savedSystems, setSavedSystems] = useState<SavedSystem[]>([]);
  const [activeSystemId, setActiveSystemId] = useState<string | null>(null);
  const activeSystem = savedSystems.find((system) => system.id === activeSystemId) ?? null;
  const [editingSystemId, setEditingSystemId] = useState<string | null>(null);
  const [hasSavedSetup, setHasSavedSetup] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);
  const [isMenuVisible, setIsMenuVisible] = useState(false);
  const [isSystemEditorVisible, setIsSystemEditorVisible] = useState(false);
  const [activeTab, setActiveTab] = useState<TabType>('remote');
  const [keyboardDraft, setKeyboardDraft] = useState('');
  const [activeModifiers, setActiveModifiers] = useState<Set<KeyModifier>>(new Set());
  const [volumeLevel, setVolumeLevel] = useState(50);
  const [isMuted, setIsMuted] = useState(false);
  const [brightnessLevel, setBrightnessLevel] = useState(70);
  const [serverApps, setServerApps] = useState<
    { id: string; name: string; icon?: string; kind?: string; category?: string }[]
  >([]);
  const [isAddAppVisible, setIsAddAppVisible] = useState(false);
  const [newAppName, setNewAppName] = useState('');
  const [newAppType, setNewAppType] = useState<'native' | 'web'>('native');
  const [newAppCommand, setNewAppCommand] = useState('');
  const [newAppUrl, setNewAppUrl] = useState('');
  const [repositoryState, setRepositoryState] = useState<ScreenRepositoryState>(
    DEFAULT_REPOSITORY_STATE
  );
  const [isWifiVisible, setIsWifiVisible] = useState(false);
  const [isBluetoothVisible, setIsBluetoothVisible] = useState(false);
  const [isSoundVisible, setIsSoundVisible] = useState(false);
  const [wifiNetworks, setWifiNetworks] = useState<{ssid: string; label: string; security?: string; signal?: number}[]>([]);
  const [currentWifi, setCurrentWifi] = useState('');
  const [wifiPassword, setWifiPassword] = useState('');
  const [wifiLoading, setWifiLoading] = useState(false);
  const [wifiMessage, setWifiMessage] = useState('');
  const [bluetoothDevices, setBluetoothDevices] = useState<{mac: string; name: string; label: string; connected?: boolean; paired?: boolean}[]>([]);
  const [currentBluetooth, setCurrentBluetooth] = useState('');
  const [bluetoothLoading, setBluetoothLoading] = useState(false);
  const [bluetoothMessage, setBluetoothMessage] = useState('');
  const [soundSpeakers, setSoundSpeakers] = useState<{name: string; label: string}[]>([]);
  const [defaultSink, setDefaultSink] = useState('');
  const [soundLoading, setSoundLoading] = useState(false);
  const [soundMessage, setSoundMessage] = useState('');
  const [addAppMode, setAddAppMode] = useState<'custom' | null>(null);
  const [addAppsMessage, setAddAppsMessage] = useState('');
  const [addAppsSuccess, setAddAppsSuccess] = useState<boolean | null>(null);
  const [selectedWifiNetwork, setSelectedWifiNetwork] = useState<{ssid: string; security: string} | null>(null);
  const repositoryRef = useRef<RemoteRepository | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [isReplaying, setIsReplaying] = useState(false);
  const [savedMacros, setSavedMacros] = useState<SavedMacro[]>([]);
  const [editingMacro, setEditingMacro] = useState<SavedMacro | null>(null);
  const [isSaveMacroVisible, setIsSaveMacroVisible] = useState(false);
  const [newMacroName, setNewMacroName] = useState('');
  const repeatTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const repeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const touchpadGestureRef = useRef({ lastDx: 0, lastDy: 0 });
  const scrollGestureRef = useRef({ lastScrollTime: 0 });

  const applyRepositoryUpdate = (update: Partial<RepositoryState>) => {
    setRepositoryState((current) => ({ ...current, ...update }));
    setIsRecording(repositoryRef.current?.isRecording() ?? false);
  };

  const createRepository = (isDemoMode: boolean) => {
    repositoryRef.current?.dispose();
    const nextRepository = isDemoMode
      ? new DemoRepository(applyRepositoryUpdate)
      : new RealServerRepository(applyRepositoryUpdate);
    repositoryRef.current = nextRepository;
    return nextRepository;
  };

  const clearRepeatTimers = () => {
    if (repeatTimeoutRef.current) {
      clearTimeout(repeatTimeoutRef.current);
      repeatTimeoutRef.current = null;
    }
    if (repeatIntervalRef.current) {
      clearInterval(repeatIntervalRef.current);
      repeatIntervalRef.current = null;
    }
  };

  const populateFormFromSystem = (system: SavedSystem | null) => {
    setSystemName(system?.name ?? '');
    setIpAddress(system?.ipAddress ?? '');
    setPort(system?.port ?? DEFAULT_PORT);
    setUsername(system?.username ?? '');
    setPassword(system?.password ?? '');
  };

  const persistSystems = async (systems: SavedSystem[], nextActiveSystemId?: string | null) => {
    const activeId =
      nextActiveSystemId === undefined
        ? activeSystemId
        : nextActiveSystemId;
    const activeSystem = systems.find((system) => system.id === activeId) ?? null;

    if (systems.length) {
      await SecureStore.setItemAsync(SYSTEMS_KEY, JSON.stringify(systems));
    } else {
      await SecureStore.deleteItemAsync(SYSTEMS_KEY);
    }

    if (activeSystem) {
      await Promise.all([
        SecureStore.setItemAsync(ACTIVE_SYSTEM_ID_KEY, activeSystem.id),
        SecureStore.setItemAsync(HOST_KEY, activeSystem.ipAddress),
        SecureStore.setItemAsync(PORT_KEY, activeSystem.port),
        SecureStore.setItemAsync(USERNAME_KEY, activeSystem.username),
        SecureStore.setItemAsync(PASSWORD_KEY, activeSystem.password),
      ]);
    } else {
      await Promise.all([
        SecureStore.deleteItemAsync(ACTIVE_SYSTEM_ID_KEY),
        SecureStore.deleteItemAsync(HOST_KEY),
        SecureStore.deleteItemAsync(PORT_KEY),
        SecureStore.deleteItemAsync(USERNAME_KEY),
        SecureStore.deleteItemAsync(PASSWORD_KEY),
      ]);
    }
  };

  const connectToSystem = async (system: SavedSystem) => {
    const repository = createRepository(false);
    applyRepositoryUpdate({
      authStatus:
        system.username.trim() && system.password
          ? 'Saved credentials loaded'
          : 'No saved credentials',
      isDemoMode: false,
      lastMessage: `Saved ${system.ipAddress}:${system.port}. Waiting for LinuxTV.`,
    });

    await repository.connect({
      ipAddress: system.ipAddress,
      password: system.password,
      port: system.port,
      username: system.username,
    });
  };

  const activateDemoMode = async (persist = true) => {
    const repository = createRepository(true);
    if (persist) {
      await Promise.all([
        SecureStore.setItemAsync(DEMO_MODE_KEY, 'true'),
        persistSystems([], null),
      ]);
    }
    setSavedSystems([]);
    setActiveSystemId(null);
    populateFormFromSystem(null);
    setHasSavedSetup(true);
    setActiveTab('remote');
    setKeyboardDraft('');
    await repository.connect();
  };

  const switchToSystem = async (system: SavedSystem, persistActive = true) => {
    clearRepeatTimers();
    setIsMenuVisible(false);
    setHasSavedSetup(true);
    setActiveSystemId(system.id);
    populateFormFromSystem(system);
    setActiveTab('remote');
    setKeyboardDraft('');

    if (persistActive) {
      await Promise.all([
        SecureStore.deleteItemAsync(DEMO_MODE_KEY),
        persistSystems(savedSystems, system.id),
      ]);
    }

    await connectToSystem(system);
  };

  const resetEditorToActiveSystem = () => {
    const activeSystem = savedSystems.find((system) => system.id === activeSystemId) ?? null;
    setEditingSystemId(activeSystem?.id ?? null);
    populateFormFromSystem(activeSystem);
  };

  const openAddSystemEditor = () => {
    setIsMenuVisible(false);
    setEditingSystemId(null);
    populateFormFromSystem(null);
    setIsSystemEditorVisible(true);
  };

  const openEditSystemEditor = () => {
    setIsMenuVisible(false);
    resetEditorToActiveSystem();
    setIsSystemEditorVisible(true);
  };

  const saveSystemAndConnect = async () => {
    const cleanedIpAddress = ipAddress.trim();
    const cleanedPort = port.trim() || DEFAULT_PORT;
    const cleanedUsername = username.trim();
    const cleanedName = buildSystemName(systemName, cleanedIpAddress);

    if (!cleanedIpAddress) {
      Alert.alert('Missing address', 'Enter the LinuxTV IP address first.');
      return;
    }

    const systemId = editingSystemId ?? createSystemId();
    const nextSystem: SavedSystem = {
      id: systemId,
      ipAddress: cleanedIpAddress,
      name: cleanedName,
      password,
      port: cleanedPort,
      username: cleanedUsername,
    };

    const existingIndex = savedSystems.findIndex((system) => system.id === systemId);
    const nextSystems =
      existingIndex >= 0
        ? savedSystems.map((system) => (system.id === systemId ? nextSystem : system))
        : [...savedSystems, nextSystem];

    setSavedSystems(nextSystems);
    setEditingSystemId(systemId);
    setIsSystemEditorVisible(false);
    setHasSavedSetup(true);

    await Promise.all([
      SecureStore.deleteItemAsync(DEMO_MODE_KEY),
      persistSystems(nextSystems, systemId),
    ]);

    await switchToSystem(nextSystem, false);
  };

  const clearSavedSetup = async () => {
    clearRepeatTimers();
    repositoryRef.current?.dispose();
    repositoryRef.current = null;

    await Promise.all([
      persistSystems([], null),
      SecureStore.deleteItemAsync(DEMO_MODE_KEY),
    ]);

    setSavedSystems([]);
    setActiveSystemId(null);
    setEditingSystemId(null);
    setHasSavedSetup(false);
    setIsMenuVisible(false);
    setIsSystemEditorVisible(false);
    setActiveTab('remote');
    setKeyboardDraft('');
    populateFormFromSystem(null);
    setRepositoryState({
      ...DEFAULT_REPOSITORY_STATE,
      lastMessage: 'Saved systems removed. Enter the LinuxTV info again.',
    });
  };

  const removeSystem = async (systemId: string) => {
    const nextSystems = savedSystems.filter((system) => system.id !== systemId);
    const nextActiveSystem = nextSystems[0] ?? null;

    setIsMenuVisible(false);
    setIsSystemEditorVisible(false);

    if (!nextActiveSystem) {
      await clearSavedSetup();
      return;
    }

    setSavedSystems(nextSystems);
    await Promise.all([
      SecureStore.deleteItemAsync(DEMO_MODE_KEY),
      persistSystems(nextSystems, nextActiveSystem.id),
    ]);
    await switchToSystem(nextActiveSystem, false);
  };

  const confirmRemoveActiveSystem = () => {
    const activeSystem = savedSystems.find((system) => system.id === activeSystemId);
    if (!activeSystem) {
      return;
    }

    Alert.alert(
      'Remove system?',
      savedSystems.length === 1
        ? 'This will remove the last saved LinuxTV system from this phone.'
        : `Remove ${activeSystem.name} from saved systems?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: () => {
            void removeSystem(activeSystem.id);
          },
        },
      ]
    );
  };

  const confirmLogout = () => {
    setIsMenuVisible(false);
    Alert.alert(
      repositoryState.isDemoMode ? 'Exit demo mode?' : 'Remove all saved systems?',
      repositoryState.isDemoMode
        ? 'Return to the connection screen and leave the mock device.'
        : 'This removes every saved LinuxTV system, including usernames and passwords, from this phone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: repositoryState.isDemoMode ? 'Exit Demo' : 'Remove All',
          style: 'destructive',
          onPress: () => {
            void clearSavedSetup();
          },
        },
      ]
    );
  };

  const sendAction = (action: string) => {
    repositoryRef.current?.sendAction(action);
  };

  const sendSettingsRequest = (type: string, payload: Record<string, any> = {}) => {
    repositoryRef.current?.sendSettingsRequest(type, payload);
  };

  // WiFi handlers
  const fetchWifiNetworks = () => {
    setWifiLoading(true);
    setWifiMessage('');
    setSelectedWifiNetwork(null);
    sendSettingsRequest('get_wifi');
  };

  const selectWifiNetwork = (ssid: string, security: string) => {
    setSelectedWifiNetwork({ ssid, security });
    if (!security || security.toLowerCase() === 'open') {
      // Connect immediately for open networks
      setWifiLoading(true);
      setWifiMessage('Connecting...');
      sendSettingsRequest('connect_wifi', { ssid, password: '', security });
    } else {
      setWifiPassword('');
      setWifiMessage(`Enter password for ${ssid}`);
    }
  };

  const connectToSelectedWifi = () => {
    if (!selectedWifiNetwork) {
      setWifiMessage('Please select a network first.');
      return;
    }
    if (selectedWifiNetwork.security && selectedWifiNetwork.security.toLowerCase() !== 'open' && !wifiPassword) {
      setWifiMessage('Please enter the Wi-Fi password.');
      return;
    }
    setWifiLoading(true);
    setWifiMessage('Connecting...');
    sendSettingsRequest('connect_wifi', { 
      ssid: selectedWifiNetwork.ssid, 
      password: wifiPassword, 
      security: selectedWifiNetwork.security 
    });
  };

  // Bluetooth handlers
  const fetchBluetoothDevices = () => {
    setBluetoothLoading(true);
    setBluetoothMessage('');
    sendSettingsRequest('get_bluetooth');
  };

  const connectToBluetooth = (mac: string) => {
    if (!mac) {
      setBluetoothMessage('Please select a device.');
      return;
    }
    setBluetoothLoading(true);
    setBluetoothMessage('Connecting...');
    sendSettingsRequest('connect_bluetooth', { mac });
  };

  const removeBluetoothDevice = (mac: string) => {
    if (!mac) {
      setBluetoothMessage('Please select a device.');
      return;
    }
    setBluetoothLoading(true);
    setBluetoothMessage('Removing...');
    sendSettingsRequest('remove_bluetooth', { mac });
  };

  // Sound handlers
  const fetchSoundDevices = () => {
    setSoundLoading(true);
    setSoundMessage('');
    sendSettingsRequest('get_sound');
  };

  const setSoundDevice = (sink: string) => {
    if (!sink) {
      setSoundMessage('Please select an audio device.');
      return;
    }
    setSoundLoading(true);
    setSoundMessage('Setting default device...');
    sendSettingsRequest('set_sound', { sink });
  };

  const launchApp = (appId: string) => {
    sendAction(`LAUNCH_APP:${appId}`);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
  };

  const fetchApps = () => {
    // Request apps from server
    sendAction('GET_APPS');
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const showAddApp = () => {
    setAddAppMode('custom');
    setIsAddAppVisible(true);
    setServerApps([]);
  };

  const addNewApp = () => {
    if (!newAppName.trim()) {
      Alert.alert('Missing name', 'Please enter an app name.');
      return;
    }

    if (newAppType === 'native' && !newAppCommand.trim()) {
      Alert.alert('Missing command', 'Please enter the app command.');
      return;
    }

    if (newAppType === 'web' && !newAppUrl.trim()) {
      Alert.alert('Missing URL', 'Please enter the app URL.');
      return;
    }

    // Reset response state
    setAddAppsSuccess(null);
    setAddAppsMessage('');

    if (repositoryRef.current) {
      repositoryRef.current.addApp({
        type: newAppType,
        name: newAppName.trim(),
        command: newAppType === 'native' ? newAppCommand.trim() : undefined,
        url: newAppType === 'web' ? newAppUrl.trim() : undefined,
      });
    }
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    
    // Reset form
    setNewAppName('');
    setNewAppCommand('');
    setNewAppUrl('');
    setIsAddAppVisible(false);
  };

  // Show success/error alert when server responds
  useEffect(() => {
    if (addAppsSuccess !== null) {
      if (addAppsSuccess) {
        Alert.alert('App Added', addAppsMessage || 'The app has been added to LinuxTV. Refresh the list to see it.');
        // Auto refresh after adding
        setTimeout(() => fetchApps(), 1000);
      } else {
        Alert.alert('Add App Failed', addAppsMessage || 'Failed to add app. Please try again.');
      }
      // Reset state after showing alert
      setAddAppsSuccess(null);
      setAddAppsMessage('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addAppsSuccess, addAppsMessage]);

  const removeApp = (appId: string, appName: string) => {
    Alert.alert(
      'Remove App',
      `Are you sure you want to remove "${appName}"?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: () => {
            if (repositoryRef.current) {
              repositoryRef.current.removeApp(appId);
            }
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
            
            Alert.alert('App Removed', `"${appName}" has been removed.`);
            
            // Auto refresh after removing
            setTimeout(() => fetchApps(), 1000);
          },
        },
      ]
    );
  };

  const moveApp = (appId: string, kind: string, direction: 'left' | 'right') => {
    sendSettingsRequest('reorder_app', { id: appId, kind, direction });
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    // The desktop mutates its own config; ask it for the fresh order shortly after.
    setTimeout(() => fetchApps(), 400);
  };

  const adjustVolume = (direction: 'up' | 'down') => {
    if (direction === 'up') {
      setVolumeLevel(prev => Math.min(prev + 5, 100));
      sendAction('VOLUME_UP');
    } else {
      setVolumeLevel(prev => Math.max(prev - 5, 0));
      sendAction('VOLUME_DOWN');
    }
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const dragVolume = (pct: number) => setVolumeLevel(pct);
  const commitVolume = (pct: number) => sendSettingsRequest('set_volume', { volume: pct });

  const toggleMute = () => {
    // Server handles MUTE as a toggle, so just send MUTE action
    sendAction('MUTE');
    setIsMuted(!isMuted);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
  };

  const adjustBrightness = (direction: 'up' | 'down') => {
    if (direction === 'up') {
      setBrightnessLevel(prev => Math.min(prev + 5, 100));
      sendAction('BRIGHTNESS_UP');
    } else {
      setBrightnessLevel(prev => Math.max(prev - 5, 0));
      sendAction('BRIGHTNESS_DOWN');
    }
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const dragBrightness = (pct: number) => setBrightnessLevel(pct);
  const commitBrightness = (pct: number) => sendSettingsRequest('set_brightness', { brightness: pct });

  const fetchVolumeLevel = async () => {
    if (repositoryState.isDemoMode) return;
    
    try {
      console.log('[Remote] Fetching volume level...');
      sendSettingsRequest('get_volume', {});
    } catch (error) {
      console.warn('Failed to fetch volume:', error);
    }
  };

  const fetchBrightnessLevel = async () => {
    if (repositoryState.isDemoMode) return;
    
    try {
      console.log('[Remote] Fetching brightness level...');
      sendSettingsRequest('get_brightness', {});
    } catch (error) {
      console.warn('Failed to fetch brightness:', error);
    }
  };

  const toggleRecording = () => {
    if (isRecording) {
      const recordedActions = repositoryRef.current?.stopRecording() ?? [];
      setIsRecording(false);
      if (recordedActions.length > 0) {
        setEditingMacro(null); // Ensure we are in "new macro" mode
        setNewMacroName(`Macro ${new Date().toLocaleTimeString()}`);
        setIsSaveMacroVisible(true);
        // Temporarily store actions in a ref to be saved
        (repositoryRef as any).current.tempRecordedActions = recordedActions;
      } else {
        Alert.alert('Recording Stopped', 'No actions were recorded.');
      }
    } else {
      repositoryRef.current?.startRecording();
      setIsRecording(true);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    }
  };

  const saveOrUpdateMacro = async () => {
    const name = newMacroName.trim();
    if (!name) {
      Alert.alert('Name Required', 'Please enter a name for the macro.');
      return;
    }

    let nextMacros: SavedMacro[];

    if (editingMacro) {
      // Update existing macro
      nextMacros = savedMacros.map(m =>
        m.id === editingMacro.id ? { ...m, name } : m
      );
      Alert.alert('Macro Renamed', `Macro has been renamed to "${name}".`);
    } else {
      // Save new macro
      const recordedActions = (repositoryRef as any).current.tempRecordedActions;
      if (!recordedActions || recordedActions.length === 0) {
        setIsSaveMacroVisible(false);
        return;
      }
      const newMacro: SavedMacro = {
        id: createSystemId(),
        name: name || 'Untitled Macro',
        createdAt: Date.now(),
        actions: recordedActions,
      };
      nextMacros = [...savedMacros, newMacro];
      (repositoryRef as any).current.tempRecordedActions = null;
      Alert.alert('Macro Saved', `"${newMacro.name}" has been saved.`);
    }

    setSavedMacros(nextMacros);
    await AsyncStorage.setItem(MACROS_KEY, JSON.stringify(nextMacros));
      setIsSaveMacroVisible(false);
    setNewMacroName('');
    setEditingMacro(null);
  };

  const replayMacro = async (macro: SavedMacro) => {
    setIsMenuVisible(false);
    setIsReplaying(true);
    await repositoryRef.current?.replayMacro(macro.actions);
    setIsReplaying(false);
  };

  const openRenameMacroEditor = (macro: SavedMacro) => {
    setEditingMacro(macro);
    setNewMacroName(macro.name);
    setIsSaveMacroVisible(true);
    setIsMenuVisible(false); // Close main menu if open
  };

  const deleteMacro = async (macroId: string) => {
    const nextMacros = savedMacros.filter(m => m.id !== macroId);
    setSavedMacros(nextMacros);
    await AsyncStorage.setItem(MACROS_KEY, JSON.stringify(nextMacros));
  };

  const confirmPowerAction = (action: 'SHUTDOWN' | 'REBOOT' | 'SLEEP' | 'UPDATE') => {
    setIsMenuVisible(false);
    
    if (action === 'UPDATE') {
      // Handle update separately with its own confirmation
      if (repositoryState.isDemoMode) {
        sendAction('UPDATE');
        return;
      }

      Alert.alert('Update System', 'Start system update? This will run apt update && apt upgrade and may take several minutes.', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Update',
          style: 'default',
          onPress: () => sendAction('UPDATE'),
        },
      ]);
      return;
    }
    
    const actionLabel = action === 'SHUTDOWN' ? 'Shutdown' : action === 'REBOOT' ? 'Reboot' : 'Sleep';

    if (repositoryState.isDemoMode) {
      sendAction(action);
      return;
    }

    const message =
      action === 'SHUTDOWN'
        ? 'Shut down the LinuxTV system now?'
        : action === 'REBOOT'
        ? 'Reboot the LinuxTV system now?'
        : 'Put the LinuxTV system to sleep?';

    Alert.alert(actionLabel, message, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: actionLabel,
        style: action === 'SLEEP' ? 'default' : 'destructive',
        onPress: () => sendAction(action),
      },
    ]);
  };

  const createRepeatingActionHandlers = (action: string) => ({
    onPress: () => sendAction(action),
    onPressIn: () => {
      clearRepeatTimers();
      repeatTimeoutRef.current = setTimeout(() => {
        sendAction(action);
        repeatIntervalRef.current = setInterval(() => {
          sendAction(action);
        }, REMOTE_REPEAT_INTERVAL_MS);
      }, REMOTE_REPEAT_DELAY_MS);
    },
    onPressOut: clearRepeatTimers,
  });

  const sendKeyboardText = () => {
    const text = keyboardDraft.trim();
    if (!text) {
      return;
    }
    repositoryRef.current?.sendText(text);
    setKeyboardDraft('');
  };

  const sendSpecialKey = (key: SpecialKey, modifiers?: KeyModifier[]) => {
    repositoryRef.current?.sendSpecialKey(key, modifiers);
  };

  // Sticky modifiers: tap Ctrl/Alt/Shift to arm it, then tap any quick key --
  // there's no way to hold two on-screen buttons at once like a real keyboard.
  const toggleModifier = (mod: KeyModifier) => {
    setActiveModifiers((current) => {
      const next = new Set(current);
      if (next.has(mod)) {
        next.delete(mod);
      } else {
        next.add(mod);
      }
      return next;
    });
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const sendQuickKey = (key: SpecialKey) => {
    const modifiers = activeModifiers.size ? Array.from(activeModifiers) : undefined;
    sendSpecialKey(key, modifiers);
    setActiveModifiers(new Set());
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  };

  const sendShortcut = (key: SpecialKey) => {
    sendSpecialKey(key, ['ctrl']);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
  };

  const sendPointerEvent = (
    event: 'move' | 'tap' | 'click' | 'right_click',
    payload?: { dx?: number; dy?: number }
  ) => {
    repositoryRef.current?.sendPointerEvent(event, payload);
  };

  useEffect(() => {
    let active = true;

    const loadSavedSetup = async () => {
      const [
        storedSystemsValue,
        storedActiveSystemId,
        storedHost,
        storedPort,
        storedUsername,
        storedPassword,
        storedDemoMode,
      ] = await Promise.all([
        SecureStore.getItemAsync(SYSTEMS_KEY),
        SecureStore.getItemAsync(ACTIVE_SYSTEM_ID_KEY),
        SecureStore.getItemAsync(HOST_KEY),
        SecureStore.getItemAsync(PORT_KEY),
        SecureStore.getItemAsync(USERNAME_KEY),
        SecureStore.getItemAsync(PASSWORD_KEY),
        SecureStore.getItemAsync(DEMO_MODE_KEY),
      ]);

      if (!active) {
        return;
      }

      const savedDemoMode = storedDemoMode === 'true';
      let nextSystems = parseStoredSystems(storedSystemsValue);

      if (!nextSystems.length && storedHost?.trim()) {
        const migratedSystem: SavedSystem = {
          id: createSystemId(),
          ipAddress: storedHost.trim(),
          name: buildSystemName('', storedHost.trim()),
          password: storedPassword ?? '',
          port: storedPort?.trim() || DEFAULT_PORT,
          username: storedUsername ?? '',
        };
        nextSystems = [migratedSystem];
        await persistSystems(nextSystems, migratedSystem.id);
      }

      setSavedSystems(nextSystems);
      setIsHydrated(true);

      if (savedDemoMode) {
        setHasSavedSetup(true);
        await activateDemoMode(false);
        return;
      }

      const nextActiveSystem =
        nextSystems.find((system) => system.id === storedActiveSystemId) ?? nextSystems[0] ?? null;

      setActiveSystemId(nextActiveSystem?.id ?? null);
      populateFormFromSystem(nextActiveSystem);
      setHasSavedSetup(Boolean(nextActiveSystem));
      setRepositoryState({
        ...DEFAULT_REPOSITORY_STATE,
        authStatus:
          nextActiveSystem?.username.trim() && nextActiveSystem.password
            ? 'Saved credentials loaded'
            : 'No saved credentials',
        lastMessage: nextActiveSystem
          ? `Saved ${nextActiveSystem.ipAddress}:${nextActiveSystem.port}. Waiting for LinuxTV.`
          : 'Enter the LinuxTV info once to keep this remote paired.',
      });

      if (nextActiveSystem) {
        if (nextActiveSystem.id !== storedActiveSystemId) {
          await persistSystems(nextSystems, nextActiveSystem.id);
        }
        await connectToSystem(nextActiveSystem);
      }
    };

    const loadMacros = async () => {
      try {
        const storedMacros = await AsyncStorage.getItem(MACROS_KEY);
        if (storedMacros) {
          setSavedMacros(JSON.parse(storedMacros));
        }
      } catch (e) {
        console.error('Failed to load macros', e);
      }
    };

    void loadSavedSetup();
    void loadMacros();

    return () => {
      active = false;
      clearRepeatTimers();
      repositoryRef.current?.dispose();
      repositoryRef.current = null;
    };
    // This hydrates saved state once on mount, including migration from legacy keys.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update server apps when repository state changes
  useEffect(() => {
    if (repositoryState.appsList && repositoryState.appsList.length > 0) {
      setServerApps(repositoryState.appsList);
    }
  }, [repositoryState.appsList]);

  // Update WiFi state when repository state changes
  useEffect(() => {
    if (repositoryState.wifiNetworks !== undefined) {
      setWifiNetworks(repositoryState.wifiNetworks || []);
    }
    if (repositoryState.currentWifi !== undefined) {
      setCurrentWifi(repositoryState.currentWifi || '');
    }
    if (repositoryState.wifiMessage !== undefined) {
      setWifiMessage(repositoryState.wifiMessage || '');
      setWifiLoading(false);
    }
  }, [repositoryState.wifiNetworks, repositoryState.currentWifi, repositoryState.wifiMessage]);

  // Update Bluetooth state when repository state changes
  useEffect(() => {
    if (repositoryState.bluetoothDevices !== undefined) {
      setBluetoothDevices(repositoryState.bluetoothDevices || []);
    }
    if (repositoryState.currentBluetooth !== undefined) {
      setCurrentBluetooth(repositoryState.currentBluetooth || '');
    }
    if (repositoryState.bluetoothMessage !== undefined) {
      setBluetoothMessage(repositoryState.bluetoothMessage || '');
      setBluetoothLoading(false);
    }
  }, [repositoryState.bluetoothDevices, repositoryState.currentBluetooth, repositoryState.bluetoothMessage]);

  // Update Sound state when repository state changes
  useEffect(() => {
    if (repositoryState.soundSpeakers !== undefined) {
      setSoundSpeakers(repositoryState.soundSpeakers || []);
    }
    if (repositoryState.defaultSink !== undefined) {
      setDefaultSink(repositoryState.defaultSink || '');
    }
    if (repositoryState.soundMessage !== undefined) {
      setSoundMessage(repositoryState.soundMessage || '');
      setSoundLoading(false);
    }
  }, [repositoryState.soundSpeakers, repositoryState.defaultSink, repositoryState.soundMessage]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextState: AppStateStatus) => {
      if (nextState !== 'active' || !hasSavedSetup || !isHydrated || repositoryState.isDemoMode) {
        return;
      }

      void repositoryRef.current?.connect({
        ipAddress,
        password,
        port,
        username,
      });
    });

    return () => {
      subscription.remove();
    };
  }, [hasSavedSetup, ipAddress, isHydrated, password, port, repositoryState.isDemoMode, username]);

  // Fetch volume and brightness levels when remote tab is active and connected
  useEffect(() => {
    if (activeTab !== 'remote' || repositoryState.isDemoMode || !activeSystem?.ipAddress || repositoryState.status !== 'Connected') {
      return;
    }

    // Fetch fresh values from desktop
    fetchVolumeLevel();
    fetchBrightnessLevel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, activeSystem?.id, repositoryState.isDemoMode, repositoryState.status]);

  // Update local state when repository state changes
  useEffect(() => {
    if (repositoryState.volumeLevel !== undefined) {
      console.log('[Remote] Setting volume to:', repositoryState.volumeLevel);
      setVolumeLevel(repositoryState.volumeLevel);
    }
  }, [repositoryState.volumeLevel]);

  useEffect(() => {
    if (repositoryState.brightnessLevel !== undefined) {
      console.log('[Remote] Setting brightness to:', repositoryState.brightnessLevel);
      setBrightnessLevel(repositoryState.brightnessLevel);
    }
  }, [repositoryState.brightnessLevel]);

  // Handle add app response from server
  useEffect(() => {
    if (repositoryState.addAppsMessage !== undefined) {
      setAddAppsMessage(repositoryState.addAppsMessage);
    }
    if (repositoryState.addAppsSuccess !== undefined) {
      setAddAppsSuccess(repositoryState.addAppsSuccess);
    }
  }, [repositoryState.addAppsMessage, repositoryState.addAppsSuccess]);

  // Keep app awake when connected to maintain WebSocket connection
  useEffect(() => {
    let isActive = true;

    const manageKeepAwake = async () => {
      if (repositoryState.status === 'Connected' && isActive) {
        try {
          await activateKeepAwakeAsync('websocket-connection');
        } catch (error) {
          console.error('Failed to activate keep awake:', error);
        }
      } else if (repositoryState.status !== 'Connected') {
        try {
          deactivateKeepAwake('websocket-connection');
        } catch (error) {
          console.error('Failed to deactivate keep awake:', error);
        }
      }
    };

    manageKeepAwake();

    return () => {
      isActive = false;
      // Cleanup: deactivate when component unmounts
      deactivateKeepAwake('websocket-connection');
    };
  }, [repositoryState.status]);

  // Listen to hardware volume button events from Android native code
  useEffect(() => {
    // Only intercept volume buttons when connected and not on keyboard tab
    if (repositoryState.status !== 'Connected' || repositoryState.isDemoMode || activeTab === 'keyboard') {
      console.log('Volume buttons disabled - status:', repositoryState.status, 'demo:', repositoryState.isDemoMode, 'tab:', activeTab);
      return;
    }

    console.log('Volume buttons enabled for tab:', activeTab);
    const subscription = DeviceEventEmitter.addListener(
      'volumeButtonPressed',
      (params: { direction: string }) => {
        console.log('Volume button pressed:', params.direction);
        // Update local volume state and send command to desktop
        if (params.direction === 'up') {
          setVolumeLevel(prev => Math.min(prev + 5, 100));
          sendAction('VOLUME_UP');
        } else if (params.direction === 'down') {
          setVolumeLevel(prev => Math.max(prev - 5, 0));
          sendAction('VOLUME_DOWN');
        }
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      }
    );

    return () => {
      subscription.remove();
    };
  }, [repositoryState.status, repositoryState.isDemoMode, activeTab]);

  const showLoginScreen = !hasSavedSetup;
  const statusAccentColor =
    repositoryState.status === 'Connected'
      ? '#A3BE8C'
      : repositoryState.status === 'Connecting...'
        ? '#EBCB8B'
        : '#BF616A';
  const tabItems: { key: TabType; label: string; icon: ComponentProps<typeof Ionicons>['name'] }[] = [
    { key: 'remote', label: 'Remote', icon: 'phone-portrait-outline' },
    { key: 'apps', label: 'Apps', icon: 'grid' },
    { key: 'keyboard', label: 'Keyboard', icon: 'text' },
    { key: 'touchpad', label: 'Touchpad', icon: 'hand-left' },
    { key: 'macros', label: 'Macros', icon: 'recording' },
  ];

  const touchpadResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        touchpadGestureRef.current = { lastDx: 0, lastDy: 0 };
      },
      onPanResponderMove: (_event, gestureState) => {
        const deltaX = Math.round((gestureState.dx - touchpadGestureRef.current.lastDx) * 1.2);
        const deltaY = Math.round((gestureState.dy - touchpadGestureRef.current.lastDy) * 1.2);

        if (Math.abs(deltaX) < 2 && Math.abs(deltaY) < 2) {
          return;
        }

        touchpadGestureRef.current = { lastDx: gestureState.dx, lastDy: gestureState.dy };
        sendPointerEvent('move', { dx: deltaX, dy: deltaY });
      },
      onPanResponderRelease: (_event, gestureState) => {
        if (Math.abs(gestureState.dx) < 8 && Math.abs(gestureState.dy) < 8) {
          sendPointerEvent('tap');
        }
      },
    })
  ).current;

  return (
    <SafeAreaView style={styles.safeArea}>
      <LinearGradient
        colors={['#2E3440', '#3B4252', '#2E3440']}
        locations={[0, 0.55, 1]}
        style={styles.ambientBackground}
      />
      <View style={styles.container}>
        {repositoryState.isDemoMode ? (
          <View style={styles.demoBanner}>
            <Text style={styles.demoBannerText}>Demo Mode</Text>
          </View>
        ) : null}
        {isRecording ? (
          <View style={[styles.demoBanner, { backgroundColor: '#BF616A' }]}>
            <Ionicons name="mic-circle" size={16} color="#ECEFF4" />
            <Text style={[styles.demoBannerText, { color: '#ECEFF4' }]}>RECORDING</Text>
          </View>
        ) : null}
        {isReplaying ? (
          <View style={[styles.demoBanner, { backgroundColor: '#88C0D0' }]}>
            <Ionicons name="play-circle" size={16} color="#ECEFF4" />
            <Text style={[styles.demoBannerText, { color: '#ECEFF4' }]}>REPLAYING MACRO</Text>
          </View>
        ) : null}

        <GlassSurface style={styles.header} tint="rgba(59, 66, 82, 0.72)">
          <View style={styles.headerLeft}>
            <View
              style={[
                styles.statusPill,
                repositoryState.status === 'Connected'
                  ? styles.statusPillOnline
                  : styles.statusPillOffline,
              ]}>
              <View
                style={[
                  styles.statusDot,
                  repositoryState.status === 'Connected'
                    ? styles.statusOnline
                    : styles.statusOffline,
                ]}
              />
            </View>
            <View style={styles.headerTextWrap}>
              <Text style={styles.title} numberOfLines={1}>LinuxTV</Text>
              <View style={styles.headerMetaRow}>
                <Text style={styles.deviceName} numberOfLines={1}>
                  {repositoryState.isDemoMode
                    ? 'Mock remote session'
                    : activeSystem?.name || repositoryState.deviceName || 'No system selected'}
                </Text>
                {!showLoginScreen ? (
                  <>
                    <View style={styles.metaDot} />
                    <Text
                      style={[styles.headerStatusText, { color: statusAccentColor }]}
                      numberOfLines={1}>
                      {repositoryState.status === 'Connected' ? 'Connected' : repositoryState.lastMessage}
                    </Text>
                  </>
                ) : null}
              </View>
            </View>
          </View>
          {hasSavedSetup ? (
            <Pressable
              style={({ pressed }) => [
                styles.menuButton,
                pressed && styles.pressed,
                isMenuVisible && styles.menuButtonActive,
              ]}
              onPress={() => setIsMenuVisible((current) => !current)}
              hitSlop={6}>
              <Ionicons name="settings-sharp" size={20} color={isMenuVisible ? '#81A1C1' : '#D8DEE9'} />
            </Pressable>
          ) : null}
        </GlassSurface>

        {showLoginScreen ? (
          <View style={styles.loginContainer}>
            <View style={styles.loginHeader}>
              <Ionicons name="tv" size={64} color="#88C0D0" />
              <Text style={styles.helperText}>Add your first LinuxTV system</Text>
              <Text style={styles.helperSubtext}>
                Save multiple systems here, then switch between them from the settings gear.
              </Text>
            </View>
            <TextInput
              value={systemName}
              onChangeText={setSystemName}
              placeholder="System name"
              placeholderTextColor="#D8DEE9"
              autoCapitalize="words"
              autoCorrect={false}
              style={styles.input}
            />
            <View style={styles.addressRow}>
              <TextInput
                value={ipAddress}
                onChangeText={setIpAddress}
                placeholder="IP address"
                placeholderTextColor="#D8DEE9"
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="numbers-and-punctuation"
                style={[styles.input, styles.ipInput]}
              />
              <TextInput
                value={port}
                onChangeText={setPort}
                placeholder="Port"
                placeholderTextColor="#D8DEE9"
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="number-pad"
                style={[styles.input, styles.portInput]}
              />
            </View>
            <TextInput
              value={username}
              onChangeText={setUsername}
              placeholder="Username"
              placeholderTextColor="#D8DEE9"
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
            />
            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholder="Password"
              placeholderTextColor="#D8DEE9"
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
            />
            <Pressable
              style={[styles.actionButton, styles.primaryButton]}
              onPress={saveSystemAndConnect}>
              <Text style={styles.primaryButtonText}>Save & Connect</Text>
            </Pressable>
            <Pressable
              style={[styles.actionButton, styles.secondaryButton]}
              onPress={() => {
                void activateDemoMode();
              }}>
              <Text style={styles.secondaryButtonText}>Skip Connection</Text>
            </Pressable>
          </View>
        ) : (
          <View style={styles.remoteContainer}>
            {activeTab === 'remote' && (
              <ScrollView
                style={styles.remoteScroll}
                contentContainerStyle={styles.remoteScrollContent}
                showsVerticalScrollIndicator={false}>
                <View style={styles.remoteControl}>
                  {/* Three Column Layout: Volume | D-Pad | Brightness */}
                  <View style={styles.controlMainLayout}>
                    {/* Volume Control - Left */}
                    <View style={styles.controlCard}>
                      <View style={styles.controlHeader}>
                        <Ionicons name="volume-high" size={20} color="#A3BE8C" />
                        <Text style={styles.controlLabel}>Volume</Text>
                      </View>
                      <View style={styles.controlSliderVertical}>
                        <Pressable
                          style={({ pressed }) => [styles.controlBtn, pressed && styles.pressed]}
                          onPress={() => adjustVolume('up')}>
                          <Ionicons name="add" size={24} color="#ECEFF4" />
                        </Pressable>
                        <VerticalSlider
                          value={volumeLevel}
                          onChange={dragVolume}
                          onCommit={commitVolume}
                          color="#A3BE8C"
                          icon="volume-high"
                        />
                        <Pressable
                          style={({ pressed }) => [styles.controlBtn, pressed && styles.pressed]}
                          onPress={() => adjustVolume('down')}>
                          <Ionicons name="remove" size={24} color="#ECEFF4" />
                        </Pressable>
                      </View>
                      <Text style={styles.controlValue}>{volumeLevel}%</Text>
                    </View>

                    {/* Center Column - D-Pad and Controls */}
                    <View style={styles.centerControlColumn}>
                      {/* D-Pad */}
                      <View style={styles.dpadContainer}>
                        <GlassSurface style={styles.dpadCircle} tint="rgba(59, 66, 82, 0.72)">
                          {/* Top Button */}
                          <Pressable
                            style={({ pressed }) => [styles.dpadButton, styles.dpadTop, pressed && styles.pressed]}
                            onPressIn={() => {
                              createRepeatingActionHandlers('UP').onPressIn();
                              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                            }}
                            onPressOut={() => {
                              createRepeatingActionHandlers('UP').onPressOut();
                            }}
                            onPress={() => sendAction('UP')}>
                            <Ionicons name="caret-up" size={36} color="#ECEFF4" />
                          </Pressable>
                          
                          {/* Left Button */}
                          <Pressable
                            style={({ pressed }) => [styles.dpadButton, styles.dpadLeft, pressed && styles.pressed]}
                            onPressIn={() => {
                              createRepeatingActionHandlers('LEFT').onPressIn();
                              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                            }}
                            onPressOut={() => {
                              createRepeatingActionHandlers('LEFT').onPressOut();
                            }}
                            onPress={() => sendAction('LEFT')}>
                            <Ionicons name="caret-back" size={36} color="#ECEFF4" />
                          </Pressable>
                          
                          {/* Center OK Button */}
                          <Pressable
                            style={({ pressed }) => [styles.okButton, pressed && styles.pressedOk]}
                            onPress={() => {
                              sendAction('SELECT');
                              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                            }}>
                            <Text style={styles.okButtonText}>OK</Text>
                          </Pressable>
                          
                          {/* Right Button */}
                          <Pressable
                            style={({ pressed }) => [styles.dpadButton, styles.dpadRight, pressed && styles.pressed]}
                            onPressIn={() => {
                              createRepeatingActionHandlers('RIGHT').onPressIn();
                              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                            }}
                            onPressOut={() => {
                              createRepeatingActionHandlers('RIGHT').onPressOut();
                            }}
                            onPress={() => sendAction('RIGHT')}>
                            <Ionicons name="caret-forward" size={36} color="#ECEFF4" />
                          </Pressable>
                          
                          {/* Bottom Button */}
                          <Pressable
                            style={({ pressed }) => [styles.dpadButton, styles.dpadBottom, pressed && styles.pressed]}
                            onPressIn={() => {
                              createRepeatingActionHandlers('DOWN').onPressIn();
                              Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                            }}
                            onPressOut={() => {
                              createRepeatingActionHandlers('DOWN').onPressOut();
                            }}
                            onPress={() => sendAction('DOWN')}>
                            <Ionicons name="caret-down" size={36} color="#ECEFF4" />
                          </Pressable>
                        </GlassSurface>
                      </View>
                    </View>

                    {/* Brightness Control - Right */}
                    <View style={styles.controlCard}>
                      <View style={styles.controlHeader}>
                        <Ionicons name="sunny" size={20} color="#EBCB8B" />
                        <Text style={styles.controlLabel}>Brightness</Text>
                      </View>
                      <View style={styles.controlSliderVertical}>
                        <Pressable
                          style={({ pressed }) => [styles.controlBtn, pressed && styles.pressed]}
                          onPress={() => adjustBrightness('up')}>
                          <Ionicons name="add" size={24} color="#ECEFF4" />
                        </Pressable>
                        <VerticalSlider
                          value={brightnessLevel}
                          onChange={dragBrightness}
                          onCommit={commitBrightness}
                          color="#EBCB8B"
                          icon="sunny"
                        />
                        <Pressable
                          style={({ pressed }) => [styles.controlBtn, pressed && styles.pressed]}
                          onPress={() => adjustBrightness('down')}>
                          <Ionicons name="remove" size={24} color="#ECEFF4" />
                        </Pressable>
                      </View>
                      <Text style={styles.controlValue}>{brightnessLevel}%</Text>
                    </View>
                  </View>

                  {/* Navigation Buttons */}
                  <View style={styles.actionButtonsRow}>
                    <ControlButton
                      icon="arrow-back"
                      label="Back"
                      onPress={() => {
                        sendAction('BACK');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                    <ControlButton
                      icon="home"
                      label="Home"
                      onPress={() => {
                        sendAction('HOME');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                    <ControlButton
                      icon="menu"
                      label="Menu"
                      onPress={() => {
                        sendAction('MENU');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                    <ControlButton
                      icon="expand"
                      label="Fullscreen"
                      onPress={() => {
                        sendAction('TOGGLE_FULLSCREEN');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                      }}
                      style={[styles.actionButtonSmall, styles.fullscreenButtonSmall]}
                      textStyle={styles.fullscreenButtonTextSmall}
                    />
                  </View>

                  {/* Media Controls */}
                  <View style={styles.actionButtonsRow}>
                    <ControlButton
                      icon="close"
                      label="Close"
                      onPress={() => {
                        sendAction('CLOSE_APP');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={[styles.actionButtonSmall, styles.closeButtonSmall]}
                      textStyle={styles.closeButtonTextSmall}
                    />
                    <ControlButton
                      icon={repositoryState.lastAction === 'PLAY_PAUSE' ? "pause" : "play"}
                      label="Play"
                      onPress={() => {
                        sendAction('PLAY_PAUSE');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                      }}
                      style={[styles.actionButtonSmall, styles.playButtonSmall]}
                      textStyle={styles.playButtonTextSmall}
                    />
                    <ControlButton
                      icon="information-circle"
                      label="Info"
                      onPress={() => {
                        sendAction('INFO');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                  </View>

                  {/* Media Track Controls */}
                  <View style={styles.actionButtonsRow}>
                    <ControlButton
                      icon="play-skip-back"
                      label="Previous"
                      onPress={() => {
                        sendAction('PREVIOUS_TRACK');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                    <ControlButton
                      icon="stop"
                      label="Stop"
                      onPress={() => {
                        sendAction('STOP_MEDIA');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                    <ControlButton
                      icon="play-skip-forward"
                      label="Next"
                      onPress={() => {
                        sendAction('NEXT_TRACK');
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}
                      style={styles.actionButtonSmall}
                      textStyle={styles.actionButtonText}
                    />
                  </View>

                  {/* Bottom Actions including Mute */}
                  <View style={styles.actionButtonsRow}>
                    <ControlButton
                      icon={isMuted ? "volume-mute" : "volume-high"}
                      label={isMuted ? 'Unmute' : 'Mute'}
                      onPress={toggleMute}
                      style={[styles.actionButtonSmall, styles.muteButton]}
                      textStyle={styles.muteButtonText}
                    />
                  </View>

                  {/* Macro Recording Controls */}
                  <View style={styles.actionButtonsRow}>
                    <ControlButton
                      icon={isRecording ? "stop-circle" : "mic-circle"}
                      label={isRecording ? "Stop Recording" : "Record Macro"}
                      onPress={() => {
                        toggleRecording();
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                      }}
                      style={[styles.actionButtonSmall, isRecording && styles.recordingButton]}
                      textStyle={isRecording ? styles.closeButtonTextSmall : styles.actionButtonText}
                    />
                  </View>
                </View>
              </ScrollView>
            )}

            {activeTab === 'apps' && (
              <ScrollView
                style={styles.remoteScroll}
                contentContainerStyle={styles.remoteScrollContent}
                showsVerticalScrollIndicator={false}>
                <View style={styles.appsContainer}>
                  <View style={styles.appsHeader}>
                    <Text style={styles.appsTitle}>Apps</Text>
                    <View style={styles.appsHeaderButtons}>
                      <Pressable
                        style={({ pressed }) => [styles.refreshButton, pressed && styles.pressed]}
                        onPress={fetchApps}>
                        <Ionicons name="refresh" size={20} color="#88C0D0" />
                      </Pressable>
                      <Pressable
                        style={({ pressed }) => [styles.refreshButton, pressed && styles.pressed]}
                        onPress={showAddApp}>
                        <Ionicons name="add" size={20} color="#88C0D0" />
                      </Pressable>
                    </View>
                  </View>
                  <Text style={styles.appsSubtitle}>Tap an app to launch it on LinuxTV</Text>
                  
                  {/* Add App Form */}
                  {isAddAppVisible && addAppMode === 'custom' && (
                      <View style={styles.addAppForm}>
                        <TextInput
                          style={styles.appInput}
                          placeholder="App Name"
                          placeholderTextColor="#D8DEE9"
                          value={newAppName}
                          onChangeText={setNewAppName}
                        />
                        
                        <View style={styles.appTypeSelector}>
                          <Pressable
                            style={[styles.appTypeButton, newAppType === 'native' && styles.appTypeButtonActive]}
                            onPress={() => setNewAppType('native')}>
                            <Ionicons name="desktop" size={16} color={newAppType === 'native' ? '#88C0D0' : '#D8DEE9'} />
                            <Text style={[styles.appTypeText, newAppType === 'native' && styles.appTypeTextActive]}>
                              Native
                            </Text>
                          </Pressable>
                          <Pressable
                            style={[styles.appTypeButton, newAppType === 'web' && styles.appTypeButtonActive]}
                            onPress={() => setNewAppType('web')}>
                            <Ionicons name="globe" size={16} color={newAppType === 'web' ? '#88C0D0' : '#D8DEE9'} />
                            <Text style={[styles.appTypeText, newAppType === 'web' && styles.appTypeTextActive]}>
                              Web
                            </Text>
                          </Pressable>
                        </View>
                        
                        {newAppType === 'native' ? (
                          <TextInput
                            style={styles.appInput}
                            placeholder="Command (e.g., vlc, firefox)"
                            placeholderTextColor="#D8DEE9"
                            value={newAppCommand}
                            onChangeText={setNewAppCommand}
                          />
                        ) : (
                          <TextInput
                            style={styles.appInput}
                            placeholder="URL (e.g., https://youtube.com)"
                            placeholderTextColor="#D8DEE9"
                            value={newAppUrl}
                            onChangeText={setNewAppUrl}
                            keyboardType="url"
                            autoCapitalize="none"
                          />
                        )}
                        
                        <View style={styles.addAppButtons}>
                          <Pressable
                            style={[styles.addAppActionButton, styles.cancelButton]}
                            onPress={() => { setIsAddAppVisible(false); setAddAppMode(null); }}>
                            <Text style={styles.cancelButtonText}>Cancel</Text>
                          </Pressable>
                          <Pressable
                            style={[styles.addAppActionButton, styles.saveButton]}
                            onPress={addNewApp}>
                            <Text style={styles.saveButtonText}>Add App</Text>
                          </Pressable>
                        </View>
                      </View>
                    )}
                  
                  <View style={styles.appsGrid}>
                    {/* Native Apps Section */}
                    {serverApps.filter(app => app.kind === 'native').length > 0 && (
                      <>
                        <View style={styles.sectionHeader}>
                          <Ionicons name="desktop" size={18} color="#88C0D0" />
                          <Text style={styles.sectionTitle}>Apps</Text>
                        </View>
                        {serverApps
                          .filter(app => app.kind === 'native')
                          .map((app, index, list) => (
                            <AppTile
                              key={app.id}
                              app={app}
                              fallbackIcon="desktop"
                              onLaunch={() => launchApp(app.id)}
                              onRemove={() => removeApp(app.id, app.name)}
                              onMoveLeft={index > 0 ? () => moveApp(app.id, 'native', 'left') : undefined}
                              onMoveRight={index < list.length - 1 ? () => moveApp(app.id, 'native', 'right') : undefined}
                            />
                          ))}
                      </>
                    )}

                    {/* Web Apps Section */}
                    {serverApps.filter(app => app.kind === 'web').length > 0 && (
                      <>
                        <View style={styles.sectionHeader}>
                          <Ionicons name="globe" size={18} color="#88C0D0" />
                          <Text style={styles.sectionTitle}>Streaming</Text>
                        </View>
                        {serverApps
                          .filter(app => app.kind === 'web')
                          .map((app, index, list) => (
                            <AppTile
                              key={app.id}
                              app={app}
                              fallbackIcon="globe"
                              onLaunch={() => launchApp(app.id)}
                              onRemove={() => removeApp(app.id, app.name)}
                              onMoveLeft={index > 0 ? () => moveApp(app.id, 'web', 'left') : undefined}
                              onMoveRight={index < list.length - 1 ? () => moveApp(app.id, 'web', 'right') : undefined}
                            />
                          ))}
                      </>
                    )}
                  </View>
                  
                  {serverApps.length === 0 && (
                    <View style={styles.emptyApps}>
                      <Ionicons name="apps" size={64} color="#D8DEE9" />
                      <Text style={styles.emptyAppsText}>No apps found</Text>
                      <Text style={styles.emptyAppsSubtext}>Tap refresh to load apps from server</Text>
                    </View>
                  )}
                </View>
              </ScrollView>
            )}

            {activeTab === 'keyboard' && (
              <ScrollView
                style={styles.remoteScroll}
                contentContainerStyle={styles.remoteScrollContent}
                showsVerticalScrollIndicator={false}>
                <View style={styles.keyboardContainer}>
                  <View style={styles.keyboardInputWrapper}>
                    <TextInput
                      value={keyboardDraft}
                      onChangeText={setKeyboardDraft}
                      placeholder="Type text..."
                      placeholderTextColor="#D8DEE9"
                      autoCapitalize="none"
                      autoCorrect={false}
                      multiline
                      style={styles.keyboardInput}
                    />
                    <Pressable
                      style={[styles.actionButton, styles.primaryButton, styles.sendButton]}
                      onPress={() => {
                        sendKeyboardText();
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                      }}>
                      <Ionicons name="paper-plane" size={20} color="#ECEFF4" />
                      <Text style={styles.primaryButtonText}>Send</Text>
                    </Pressable>
                  </View>

                  <View style={styles.keyboardSection}>
                    <Text style={styles.groupLabel}>Modifiers · hold for next key</Text>
                    <View style={styles.specialKeysRow}>
                      {(['ctrl', 'alt', 'shift'] as KeyModifier[]).map((mod) => {
                        const active = activeModifiers.has(mod);
                        return (
                          <Pressable
                            key={mod}
                            style={({ pressed }) => [
                              styles.keyButton,
                              active && styles.modifierButtonActive,
                              pressed && styles.pressed,
                            ]}
                            onPress={() => toggleModifier(mod)}>
                            <Text style={[styles.keyButtonText, active && styles.modifierButtonTextActive]}>
                              {mod === 'ctrl' ? 'Ctrl' : mod === 'alt' ? 'Alt' : 'Shift'}
                            </Text>
                          </Pressable>
                        );
                      })}
                    </View>
                  </View>

                  <View style={styles.keyboardSection}>
                    <Text style={styles.groupLabel}>Quick Keys</Text>
                    <View style={styles.specialKeysRow}>
                      <ControlButton
                        icon="checkmark-circle"
                        label="Enter"
                        onPress={() => sendQuickKey('ENTER')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="ellipse-outline"
                        label="Space"
                        onPress={() => sendQuickKey('SPACE')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="backspace-outline"
                        label="Backspace"
                        onPress={() => sendQuickKey('BACKSPACE')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                    </View>
                    <View style={styles.specialKeysRow}>
                      <ControlButton
                        icon="close-circle"
                        label="Esc"
                        onPress={() => sendQuickKey('ESCAPE')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="arrow-forward"
                        label="Tab"
                        onPress={() => sendQuickKey('TAB')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="arrow-back"
                        label="Shift+Tab"
                        onPress={() => {
                          sendAction('SHIFT_TAB');
                          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                        }}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                    </View>
                    <View style={styles.specialKeysRow}>
                      <ControlButton
                        icon="trash-outline"
                        label="Delete"
                        onPress={() => sendQuickKey('DELETE')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="arrow-forward-circle-outline"
                        label="End"
                        onPress={() => sendQuickKey('END')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="refresh-outline"
                        label="Refresh"
                        onPress={() => sendQuickKey('F5')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                    </View>
                    <View style={styles.specialKeysRow}>
                      <ControlButton
                        icon="arrow-up-circle-outline"
                        label="Page Up"
                        onPress={() => sendQuickKey('PAGE_UP')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="arrow-down-circle-outline"
                        label="Page Down"
                        onPress={() => sendQuickKey('PAGE_DOWN')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                    </View>
                  </View>

                  <View style={styles.keyboardSection}>
                    <Text style={styles.groupLabel}>Shortcuts</Text>
                    <View style={styles.specialKeysRow}>
                      <ControlButton
                        icon="copy-outline"
                        label="Copy"
                        onPress={() => sendShortcut('C')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="clipboard-outline"
                        label="Paste"
                        onPress={() => sendShortcut('V')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="cut-outline"
                        label="Cut"
                        onPress={() => sendShortcut('X')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                    </View>
                    <View style={styles.specialKeysRow}>
                      <ControlButton
                        icon="checkbox-outline"
                        label="Select All"
                        onPress={() => sendShortcut('A')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                      <ControlButton
                        icon="arrow-undo-outline"
                        label="Undo"
                        onPress={() => sendShortcut('Z')}
                        style={styles.keyButton}
                        textStyle={styles.keyButtonText}
                      />
                    </View>
                  </View>
                </View>
              </ScrollView>
            )}

            {activeTab === 'macros' && (
              <ScrollView
                style={styles.remoteScroll}
                contentContainerStyle={styles.remoteScrollContent}
                showsVerticalScrollIndicator={false}>
                <View style={styles.appsContainer}>
                  <View style={styles.appsHeader}>
                    <Text style={styles.appsTitle}>Macros</Text>
                    <View style={styles.appsHeaderButtons}>
                      <Pressable
                        style={({ pressed }) => [
                          styles.refreshButton,
                          pressed && styles.pressed,
                          isRecording && { backgroundColor: '#BF616A', borderColor: '#BF616A' },
                        ]}
                        onPress={toggleRecording}>
                        <Ionicons
                          name={isRecording ? 'stop-circle' : 'mic-circle'}
                          size={20}
                          color="#ECEFF4"
                        />
                      </Pressable>
                    </View>
                  </View>
                  <Text style={styles.appsSubtitle}>
                    {isRecording
                      ? 'Recording actions... Press stop to save.'
                      : 'Record and replay sequences of actions.'}
                  </Text>

                  {savedMacros.length === 0 && !isRecording && (
                    <View style={styles.emptyApps}>
                      <Ionicons name="list" size={64} color="#D8DEE9" />
                      <Text style={styles.emptyAppsText}>No macros saved</Text>
                      <Text style={styles.emptyAppsSubtext}>
                        Press the record button to create one.
                      </Text>
                    </View>
                  )}

                  {savedMacros.map(macro => (
                    <View key={macro.id} style={styles.systemRow}>
                      <Pressable style={styles.systemRowLeft} onPress={() => replayMacro(macro)}>
                        <Ionicons name="play-circle" size={24} color="#88C0D0" />
                        <View style={styles.systemRowText}>
                          <Text style={styles.systemName}>{macro.name}</Text>
                          <Text style={styles.systemMeta}>{macro.actions.length} actions</Text>
                        </View>
                      </Pressable>
                      <Pressable style={styles.bluetoothRemoveButton} onPress={() => openRenameMacroEditor(macro)}>
                        <Ionicons name="create-outline" size={18} color="#D8DEE9" />
                      </Pressable>
                      <Pressable
                        style={styles.bluetoothRemoveButton}
                        onPress={() => {
                          Alert.alert('Delete Macro?', `Are you sure you want to delete "${macro.name}"?`, [
                            { text: 'Cancel', style: 'cancel' },
                            { text: 'Delete', style: 'destructive', onPress: () => deleteMacro(macro.id) },
                          ]);
                        }}>
                        <Ionicons name="trash-outline" size={18} color="#BF616A" />
                      </Pressable>
                    </View>
                  ))}
                </View>
              </ScrollView>
            )}

            {activeTab === 'touchpad' && (
              <View style={styles.touchpadContainer}>
                <View style={styles.touchpadWrapper}>
                  <View style={styles.touchpadSurface} {...touchpadResponder.panHandlers}>
                    <View style={styles.touchpadInner}>
                      <Ionicons name="hand-left" size={48} color="#88C0D0" />
                      <Text style={styles.touchpadText}>Touchpad</Text>
                      <Text style={styles.touchpadHint}>Tap to click • Drag to move</Text>
                    </View>
                  </View>
                  <View style={styles.scrollBar}>
                    <Pressable
                      style={styles.scrollButton}
                      onPress={() => {
                        // Send scroll up event
                        repositoryRef.current?.sendPointerEvent('scroll', { dx: 0, dy: -1 });
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}>
                      <Ionicons name="chevron-up" size={24} color="#D8DEE9" />
                    </Pressable>
                    <View
                      style={styles.scrollTrack}
                      {...PanResponder.create({
                        onStartShouldSetPanResponder: () => true,
                        onMoveShouldSetPanResponder: () => true,
                        onPanResponderMove: (evt, gestureState) => {
                          const now = Date.now();
                          const timeSinceLastScroll = now - scrollGestureRef.current.lastScrollTime;
                          
                          // Throttle scroll events to max 20 per second (50ms interval)
                          if (timeSinceLastScroll < 50) {
                            return;
                          }
                          
                          if (gestureState.dy < -10) {
                            // Send scroll down event (swipe up = scroll down)
                            repositoryRef.current?.sendPointerEvent('scroll', { dx: 0, dy: 1 });
                            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                            scrollGestureRef.current.lastScrollTime = now;
                          } else if (gestureState.dy > 10) {
                            // Send scroll up event (swipe down = scroll up)
                            repositoryRef.current?.sendPointerEvent('scroll', { dx: 0, dy: -1 });
                            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                            scrollGestureRef.current.lastScrollTime = now;
                          }
                        },
                      }).panHandlers}
                    />
                    <Pressable
                      style={styles.scrollButton}
                      onPress={() => {
                        // Send scroll down event
                        repositoryRef.current?.sendPointerEvent('scroll', { dx: 0, dy: 1 });
                        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                      }}>
                      <Ionicons name="chevron-down" size={24} color="#D8DEE9" />
                    </Pressable>
                  </View>
                </View>
                <View style={styles.touchpadButtons}>
                  <ControlButton
                    label="Click"
                    onPress={() => {
                      sendPointerEvent('click');
                      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                    }}
                    style={styles.touchpadButton}
                    textStyle={styles.touchpadButtonText}
                  />
                  <ControlButton
                    label="Right Click"
                    onPress={() => {
                      sendPointerEvent('right_click');
                      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
                    }}
                    style={styles.touchpadButton}
                    textStyle={styles.touchpadButtonText}
                  />
                </View>
              </View>
            )}
          </View>
        )}
      </View>

      {!showLoginScreen && (
        <View style={styles.tabBarContainer}>
          <GlassSurface style={styles.tabBar} tint="rgba(59, 66, 82, 0.72)">
            {tabItems.map((tab) => (
              <Pressable
                key={tab.key}
                style={({ pressed }) => [
                  styles.tabItem,
                  activeTab === tab.key && styles.tabItemActive,
                  pressed && styles.pressed,
                ]}
                onPress={() => {
                  setActiveTab(tab.key);
                  Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
                }}>
                <Ionicons
                  name={tab.icon}
                  size={22}
                  color={activeTab === tab.key ? '#81A1C1' : '#D8DEE9'}
                />
                <Text
                  style={[
                    styles.tabItemText,
                    activeTab === tab.key && styles.tabItemTextActive,
                  ]}>
                  {tab.label}
                </Text>
              </Pressable>
            ))}
          </GlassSurface>
        </View>
      )}

      <Modal
        transparent
        animationType="fade"
        visible={isMenuVisible}
        onRequestClose={() => setIsMenuVisible(false)}>
        <Pressable style={styles.menuOverlay} onPress={() => setIsMenuVisible(false)}>
          <Pressable onPress={() => undefined} style={styles.menuSheetWrap}>
          <GlassSurface style={styles.menuSheet} tint="rgba(59, 66, 82, 0.88)">
            {/* Header */}
            <View style={styles.menuHeader}>
              <Ionicons name="settings" size={24} color="#88C0D0" />
              <Text style={styles.menuHeaderTitle}>Settings</Text>
            </View>
            
            <View style={styles.menuDivider} />
            
            {!repositoryState.isDemoMode ? (
              <>
                {/* Systems Section */}
                <View style={styles.menuSection}>
                  <Text style={styles.menuSectionTitle}>Systems</Text>
                  {savedSystems.map((system) => (
                    <Pressable
                      key={system.id}
                      style={({ pressed }) => [
                        styles.systemRow,
                        system.id === activeSystemId && styles.systemRowActive,
                        pressed && styles.menuItemPressed,
                      ]}
                      onPress={() => {
                        void switchToSystem(system);
                      }}>
                      <View style={styles.systemRowLeft}>
                        <Ionicons 
                          name={system.id === activeSystemId ? "radio-button-on" : "radio-button-off"} 
                          size={18} 
                          color={system.id === activeSystemId ? "#A3BE8C" : "#D8DEE9"} 
                        />
                        <View style={styles.systemRowText}>
                          <Text style={styles.systemName}>{system.name}</Text>
                          <Text style={styles.systemMeta}>
                            {system.ipAddress}:{system.port}
                          </Text>
                        </View>
                      </View>
                      {system.id === activeSystemId && (
                        <View style={styles.activeBadge}>
                          <Text style={styles.activeBadgeText}>Active</Text>
                        </View>
                      )}
                    </Pressable>
                  ))}
                </View>

                <View style={styles.menuDivider} />
                
                {/* System Actions */}
                <Pressable
                  style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
                  onPress={openAddSystemEditor}>
                  <View style={styles.menuItemContent}>
                    <Ionicons name="add-circle" size={20} color="#88C0D0" />
                    <Text style={styles.menuItemText}>Add System</Text>
                  </View>
                </Pressable>
                
                {activeSystem && (
                  <Pressable
                    style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
                    onPress={openEditSystemEditor}>
                    <View style={styles.menuItemContent}>
                      <Ionicons name="create" size={20} color="#88C0D0" />
                      <Text style={styles.menuItemText}>Edit System</Text>
                    </View>
                  </Pressable>
                )}
                
                {activeSystem && (
                  <Pressable
                    style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
                    onPress={confirmRemoveActiveSystem}>
                    <View style={styles.menuItemContent}>
                      <Ionicons name="trash" size={20} color="#BF616A" />
                      <Text style={styles.menuItemDangerText}>Remove System</Text>
                    </View>
                  </Pressable>
                )}
                
                <View style={styles.menuDivider} />
              </>
            ) : null}

            {/* Device Settings */}
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => {
                setIsMenuVisible(false);
                setIsWifiVisible(true);
                fetchWifiNetworks();
              }}>
              <View style={styles.menuItemContent}>
                <Ionicons name="wifi" size={20} color="#88C0D0" />
                <Text style={styles.menuItemText}>Wi-Fi</Text>
              </View>
            </Pressable>
            
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => {
                setIsMenuVisible(false);
                setIsBluetoothVisible(true);
                fetchBluetoothDevices();
              }}>
              <View style={styles.menuItemContent}>
                <Ionicons name="bluetooth" size={20} color="#88C0D0" />
                <Text style={styles.menuItemText}>Bluetooth</Text>
              </View>
            </Pressable>
            
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => {
                setIsMenuVisible(false);
                setIsSoundVisible(true);
                fetchSoundDevices();
              }}>
              <View style={styles.menuItemContent}>
                <Ionicons name="volume-high" size={20} color="#88C0D0" />
                <Text style={styles.menuItemText}>Sound</Text>
              </View>
            </Pressable>
            
            <View style={styles.menuDivider} />

            {/* System Actions */}
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => confirmPowerAction('UPDATE')}>
              <View style={styles.menuItemContent}>
                <Ionicons name="download" size={20} color="#88C0D0" />
                <Text style={styles.menuItemText}>Update System</Text>
              </View>
            </Pressable>
            
            {/* Power Actions */}
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => confirmPowerAction('SHUTDOWN')}>
              <View style={styles.menuItemContent}>
                <Ionicons name="power" size={20} color="#BF616A" />
                <Text style={styles.menuItemDangerText}>Shutdown</Text>
              </View>
            </Pressable>
            
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => confirmPowerAction('REBOOT')}>
              <View style={styles.menuItemContent}>
                <Ionicons name="refresh" size={20} color="#BF616A" />
                <Text style={styles.menuItemDangerText}>Reboot</Text>
              </View>
            </Pressable>
            
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={() => confirmPowerAction('SLEEP')}>
              <View style={styles.menuItemContent}>
                <Ionicons name="moon" size={20} color="#EBCB8B" />
                <Text style={styles.menuItemWarningText}>Sleep</Text>
              </View>
            </Pressable>
            
            <View style={styles.menuDivider} />
            
            <Pressable
              style={({ pressed }) => [styles.menuItem, pressed && styles.menuItemPressed]}
              onPress={confirmLogout}>
              <View style={styles.menuItemContent}>
                <Ionicons name="log-out" size={20} color="#BF616A" />
                <Text style={styles.menuItemDangerText}>
                  {repositoryState.isDemoMode ? 'Exit Demo' : 'Logout & Clear Systems'}
                </Text>
              </View>
            </Pressable>
          </GlassSurface>
          </Pressable>
        </Pressable>
      </Modal>

      <Modal
        transparent
        animationType="slide"
        visible={isSystemEditorVisible}
        onRequestClose={() => setIsSystemEditorVisible(false)}>
        <Pressable
          style={styles.editorOverlay}
          onPress={() => {
            setIsSystemEditorVisible(false);
            resetEditorToActiveSystem();
          }}>
          <Pressable onPress={() => undefined}>
          <GlassSurface style={styles.editorSheet} tint="rgba(59, 66, 82, 0.88)">
            <Text style={styles.editorTitle}>
              {editingSystemId ? 'Edit system' : 'Add system'}
            </Text>
            <Text style={styles.editorSubtitle}>
              Save another LinuxTV target and switch to it from the gear anytime.
            </Text>
            <TextInput
              value={systemName}
              onChangeText={setSystemName}
              placeholder="System name"
              placeholderTextColor="#D8DEE9"
              autoCapitalize="words"
              autoCorrect={false}
              style={styles.input}
            />
            <View style={styles.addressRow}>
              <TextInput
                value={ipAddress}
                onChangeText={setIpAddress}
                placeholder="IP address"
                placeholderTextColor="#D8DEE9"
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="numbers-and-punctuation"
                style={[styles.input, styles.ipInput]}
              />
              <TextInput
                value={port}
                onChangeText={setPort}
                placeholder="Port"
                placeholderTextColor="#D8DEE9"
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="number-pad"
                style={[styles.input, styles.portInput]}
              />
            </View>
            <TextInput
              value={username}
              onChangeText={setUsername}
              placeholder="Username"
              placeholderTextColor="#D8DEE9"
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
            />
            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholder="Password"
              placeholderTextColor="#D8DEE9"
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
            />
            <Pressable
              style={[styles.actionButton, styles.primaryButton]}
              onPress={saveSystemAndConnect}>
              <Text style={styles.primaryButtonText}>
                {editingSystemId ? 'Save & Switch' : 'Add & Switch'}
              </Text>
            </Pressable>
            <Pressable
              style={[styles.actionButton, styles.ghostButton]}
              onPress={() => {
                setIsSystemEditorVisible(false);
                resetEditorToActiveSystem();
              }}>
              <Text style={styles.ghostButtonText}>Cancel</Text>
            </Pressable>
          </GlassSurface>
          </Pressable>
        </Pressable>
      </Modal>

      {/* WiFi Settings Modal */}
      <Modal
        transparent
        animationType="slide"
        visible={isWifiVisible}
        onRequestClose={() => setIsWifiVisible(false)}>
        <Pressable style={styles.settingsOverlay} onPress={() => setIsWifiVisible(false)}>
          <Pressable onPress={() => undefined} style={styles.settingsSheetWrap}>
          <GlassSurface style={styles.settingsSheet} tint="rgba(59, 66, 82, 0.88)">
            <View style={styles.settingsHeader}>
              <Ionicons name="wifi" size={24} color="#88C0D0" />
              <Text style={styles.settingsTitle}>Wi-Fi Settings</Text>
              <Pressable onPress={() => setIsWifiVisible(false)} style={styles.settingsCloseButton}>
                <Ionicons name="close" size={24} color="#D8DEE9" />
              </Pressable>
            </View>
            
            <ScrollView style={styles.settingsContent}>
              <Text style={styles.settingsSectionTitle}>Available Networks</Text>
              
              {wifiLoading ? (
                <View style={styles.settingsLoading}>
                  <Ionicons name="sync" size={32} color="#88C0D0" />
                  <Text style={styles.settingsLoadingText}>Scanning networks...</Text>
                </View>
              ) : (
                <>
                  {wifiNetworks.map((network) => (
                    <Pressable
                      key={network.ssid}
                      style={[
                        styles.networkItem,
                        currentWifi === network.ssid && styles.networkItemActive,
                        selectedWifiNetwork?.ssid === network.ssid && styles.networkItemActive,
                      ]}
                      onPress={() => {
                        if (currentWifi === network.ssid) {
                          setWifiMessage(`Already connected to ${network.ssid}`);
                        } else {
                          selectWifiNetwork(network.ssid, network.security || '');
                        }
                      }}>
                      <View style={styles.networkItemLeft}>
                        <Ionicons 
                          name={currentWifi === network.ssid ? "checkmark-circle" : "wifi"} 
                          size={20} 
                          color={currentWifi === network.ssid ? "#A3BE8C" : "#D8DEE9"} 
                        />
                        <View style={styles.networkItemText}>
                          <Text style={styles.networkName}>{network.label || network.ssid}</Text>
                          {network.security && network.security.toLowerCase() !== 'open' && (
                            <Ionicons name="lock-closed" size={12} color="#D8DEE9" />
                          )}
                        </View>
                      </View>
                      {network.signal && (
                        <Text style={styles.networkSignal}>{network.signal}%</Text>
                      )}
                    </Pressable>
                  ))}
                  
                  {wifiNetworks.length === 0 && (
                    <View style={styles.settingsEmpty}>
                      <Ionicons name="wifi-outline" size={48} color="#D8DEE9" />
                      <Text style={styles.settingsEmptyText}>No networks found</Text>
                    </View>
                  )}
                </>
              )}
              
              {selectedWifiNetwork && selectedWifiNetwork.security && selectedWifiNetwork.security.toLowerCase() !== 'open' && (
                <View style={styles.settingsSection}>
                  <Text style={styles.settingsSectionTitle}>Password for {selectedWifiNetwork.ssid}</Text>
                  <TextInput
                    value={wifiPassword}
                    onChangeText={setWifiPassword}
                    placeholder="Enter Wi-Fi password"
                    placeholderTextColor="#D8DEE9"
                    secureTextEntry
                    style={styles.settingsInput}
                  />
                  <Pressable
                    style={styles.settingsActionButton}
                    onPress={connectToSelectedWifi}>
                    <Ionicons name="checkmark" size={20} color="#A3BE8C" />
                    <Text style={styles.settingsActionButtonText}>Connect</Text>
                  </Pressable>
                </View>
              )}
              
              {wifiMessage ? (
                <View style={[
                  styles.settingsMessage,
                  wifiMessage.includes('Connected') && styles.settingsMessageSuccess,
                  wifiMessage.includes('Could not') && styles.settingsMessageError,
                ]}>
                  <Text style={styles.settingsMessageText}>{wifiMessage}</Text>
                </View>
              ) : null}
              
              <Pressable
                style={styles.settingsActionButton}
                onPress={fetchWifiNetworks}>
                <Ionicons name="refresh" size={20} color="#88C0D0" />
                <Text style={styles.settingsActionButtonText}>Refresh Networks</Text>
              </Pressable>
            </ScrollView>
          </GlassSurface>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Bluetooth Settings Modal */}
      <Modal
        transparent
        animationType="slide"
        visible={isBluetoothVisible}
        onRequestClose={() => setIsBluetoothVisible(false)}>
        <Pressable style={styles.settingsOverlay} onPress={() => setIsBluetoothVisible(false)}>
          <Pressable onPress={() => undefined} style={styles.settingsSheetWrap}>
          <GlassSurface style={styles.settingsSheet} tint="rgba(59, 66, 82, 0.88)">
            <View style={styles.settingsHeader}>
              <Ionicons name="bluetooth" size={24} color="#88C0D0" />
              <Text style={styles.settingsTitle}>Bluetooth Settings</Text>
              <Pressable onPress={() => setIsBluetoothVisible(false)} style={styles.settingsCloseButton}>
                <Ionicons name="close" size={24} color="#D8DEE9" />
              </Pressable>
            </View>
            
            <ScrollView style={styles.settingsContent}>
              <Text style={styles.settingsSectionTitle}>Devices</Text>
              
              {bluetoothLoading ? (
                <View style={styles.settingsLoading}>
                  <Ionicons name="sync" size={32} color="#88C0D0" />
                  <Text style={styles.settingsLoadingText}>Scanning devices...</Text>
                </View>
              ) : (
                <>
                  {bluetoothDevices.map((device) => (
                    <View key={device.mac} style={styles.bluetoothItem}>
                      <View style={styles.bluetoothItemLeft}>
                        <Ionicons 
                          name={device.connected ? "bluetooth" : "bluetooth-outline"} 
                          size={20} 
                          color={device.connected ? "#A3BE8C" : "#D8DEE9"} 
                        />
                        <View style={styles.bluetoothItemText}>
                          <Text style={styles.bluetoothName}>{device.name || device.label}</Text>
                          <Text style={styles.bluetoothMac}>{device.mac}</Text>
                        </View>
                      </View>
                      <View style={styles.bluetoothActions}>
                        {!device.connected && (
                          <Pressable
                            style={styles.bluetoothConnectButton}
                            onPress={() => connectToBluetooth(device.mac)}>
                            <Text style={styles.bluetoothConnectText}>Connect</Text>
                          </Pressable>
                        )}
                        {device.connected && (
                          <Text style={styles.bluetoothConnectedText}>Connected</Text>
                        )}
                        <Pressable
                          style={styles.bluetoothRemoveButton}
                          onPress={() => {
                            Alert.alert(
                              'Remove Device',
                              `Remove ${device.name}?`,
                              [
                                { text: 'Cancel', style: 'cancel' },
                                { text: 'Remove', style: 'destructive', onPress: () => removeBluetoothDevice(device.mac) },
                              ]
                            );
                          }}>
                          <Ionicons name="trash-outline" size={18} color="#BF616A" />
                        </Pressable>
                      </View>
                    </View>
                  ))}
                  
                  {bluetoothDevices.length === 0 && (
                    <View style={styles.settingsEmpty}>
                      <Ionicons name="bluetooth-outline" size={48} color="#D8DEE9" />
                      <Text style={styles.settingsEmptyText}>No devices found</Text>
                    </View>
                  )}
                </>
              )}
              
              {bluetoothMessage ? (
                <View style={[
                  styles.settingsMessage,
                  bluetoothMessage.includes('Connected') && styles.settingsMessageSuccess,
                  bluetoothMessage.includes('Could not') && styles.settingsMessageError,
                ]}>
                  <Text style={styles.settingsMessageText}>{bluetoothMessage}</Text>
                </View>
              ) : null}
              
              <Pressable
                style={styles.settingsActionButton}
                onPress={fetchBluetoothDevices}>
                <Ionicons name="refresh" size={20} color="#88C0D0" />
                <Text style={styles.settingsActionButtonText}>Refresh Devices</Text>
              </Pressable>
            </ScrollView>
          </GlassSurface>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Sound Settings Modal */}
      <Modal
        transparent
        animationType="slide"
        visible={isSoundVisible}
        onRequestClose={() => setIsSoundVisible(false)}>
        <Pressable style={styles.settingsOverlay} onPress={() => setIsSoundVisible(false)}>
          <Pressable onPress={() => undefined} style={styles.settingsSheetWrap}>
          <GlassSurface style={styles.settingsSheet} tint="rgba(59, 66, 82, 0.88)">
            <View style={styles.settingsHeader}>
              <Ionicons name="volume-high" size={24} color="#88C0D0" />
              <Text style={styles.settingsTitle}>Sound Settings</Text>
              <Pressable onPress={() => setIsSoundVisible(false)} style={styles.settingsCloseButton}>
                <Ionicons name="close" size={24} color="#D8DEE9" />
              </Pressable>
            </View>
            
            <ScrollView style={styles.settingsContent}>
              <Text style={styles.settingsSectionTitle}>Audio Output Devices</Text>
              
              {soundLoading ? (
                <View style={styles.settingsLoading}>
                  <Ionicons name="sync" size={32} color="#88C0D0" />
                  <Text style={styles.settingsLoadingText}>Loading devices...</Text>
                </View>
              ) : (
                <>
                  {soundSpeakers.map((speaker) => (
                    <Pressable
                      key={speaker.name}
                      style={[
                        styles.speakerItem,
                        defaultSink === speaker.name && styles.speakerItemActive,
                      ]}
                      onPress={() => {
                        if (defaultSink !== speaker.name) {
                          setSoundDevice(speaker.name);
                        }
                      }}>
                      <View style={styles.speakerItemLeft}>
                        <Ionicons 
                          name={defaultSink === speaker.name ? "volume-high" : "volume-low"} 
                          size={20} 
                          color={defaultSink === speaker.name ? "#A3BE8C" : "#D8DEE9"} 
                        />
                        <Text style={styles.speakerName}>{speaker.label}</Text>
                      </View>
                      {defaultSink === speaker.name && (
                        <View style={styles.speakerActiveBadge}>
                          <Text style={styles.speakerActiveText}>Default</Text>
                        </View>
                      )}
                    </Pressable>
                  ))}
                  
                  {soundSpeakers.length === 0 && (
                    <View style={styles.settingsEmpty}>
                      <Ionicons name="volume-mute-outline" size={48} color="#D8DEE9" />
                      <Text style={styles.settingsEmptyText}>No audio devices found</Text>
                    </View>
                  )}
                </>
              )}
              
              {soundMessage ? (
                <View style={[
                  styles.settingsMessage,
                  soundMessage.includes('updated') && styles.settingsMessageSuccess,
                  soundMessage.includes('Failed') && styles.settingsMessageError,
                ]}>
                  <Text style={styles.settingsMessageText}>{soundMessage}</Text>
                </View>
              ) : null}
              
              <Pressable
                style={styles.settingsActionButton}
                onPress={fetchSoundDevices}>
                <Ionicons name="refresh" size={20} color="#88C0D0" />
                <Text style={styles.settingsActionButtonText}>Refresh Devices</Text>
              </Pressable>
            </ScrollView>
          </GlassSurface>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Save Macro Modal */}
      <Modal
        transparent
        animationType="slide"
        visible={isSaveMacroVisible}
        onRequestClose={() => setIsSaveMacroVisible(false)}>
        <Pressable
          style={styles.editorOverlay}
          onPress={() => setIsSaveMacroVisible(false)}>
          <Pressable onPress={() => undefined}>
          <GlassSurface style={styles.editorSheet} tint="rgba(59, 66, 82, 0.88)">
            <Text style={styles.editorTitle}>{editingMacro ? 'Rename Macro' : 'Save Macro'}</Text>
            <Text style={styles.editorSubtitle}>
              {editingMacro ? 'Enter a new name for this macro.' : 'Give this recorded sequence of actions a name.'}
            </Text>
            <TextInput
              value={newMacroName}
              onChangeText={setNewMacroName}
              placeholder="Macro name"
              placeholderTextColor="#D8DEE9"
              autoCapitalize="words"
              style={styles.input}
            />
            <Pressable
              style={[styles.actionButton, styles.primaryButton]}
              onPress={saveOrUpdateMacro}>
              <Text style={styles.primaryButtonText}>{editingMacro ? 'Rename' : 'Save Macro'}</Text>
            </Pressable>
            <Pressable
              style={[styles.actionButton, styles.ghostButton]}
              onPress={() => setIsSaveMacroVisible(false)}>
              <Text style={styles.ghostButtonText}>Cancel</Text>
            </Pressable>
          </GlassSurface>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

function ControlButton({
  label,
  onPress,
  onPressIn,
  onPressOut,
  style,
  textStyle,
  icon,
  iconSize = 20,
}: {
  label: string;
  icon?: ComponentProps<typeof Ionicons>['name'];
  onPress: () => void;
  onPressIn?: () => void;
  onPressOut?: () => void;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>
  iconSize?: number;
}) {
  return (
    <Pressable
      style={({ pressed }) => [style, pressed && styles.pressed]}
      onPress={onPress}
      onPressIn={onPressIn}
      onPressOut={onPressOut}>
      {icon && <Ionicons name={icon} size={iconSize} color="#D8DEE9" style={styles.buttonIcon}/>}
      <Text style={textStyle}>{label}</Text>
    </Pressable>
  );
}

// Drag-to-set vertical glass slider (volume/brightness). `onChange` fires on
// every move for an instantly responsive fill; `onCommit` is throttled
// during the drag and always fires once on release, so it's the one that
// should actually talk to the network.
function VerticalSlider({
  value,
  onChange,
  onCommit,
  color,
  icon,
  height = 220,
}: {
  value: number;
  onChange: (pct: number) => void;
  onCommit: (pct: number) => void;
  color: string;
  icon: ComponentProps<typeof Ionicons>['name'];
  height?: number;
}) {
  const trackHeightRef = useRef(height);
  const startValueRef = useRef(value);
  const valueRef = useRef(value);
  const lastSentRef = useRef(0);
  valueRef.current = value;

  const responder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        startValueRef.current = valueRef.current;
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
      },
      onPanResponderMove: (_evt, gestureState) => {
        const delta = (-gestureState.dy / trackHeightRef.current) * 100;
        const next = Math.round(Math.max(0, Math.min(100, startValueRef.current + delta)));
        if (next !== valueRef.current) {
          onChange(next);
        }
        const now = Date.now();
        if (now - lastSentRef.current > 80) {
          lastSentRef.current = now;
          onCommit(next);
        }
      },
      onPanResponderRelease: () => {
        onCommit(valueRef.current);
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      },
    })
  ).current;

  return (
    <View
      style={[styles.sliderTrackVertical, { height }]}
      onLayout={(event) => {
        trackHeightRef.current = event.nativeEvent.layout.height || height;
      }}
      {...responder.panHandlers}>
      <View style={[styles.sliderTrackFillVertical, { height: `${value}%`, backgroundColor: color }]} />
      <Ionicons name={icon} size={14} color="rgba(236, 239, 244, 0.35)" style={styles.sliderTrackIcon} />
      <View
        style={[
          styles.sliderThumb,
          { bottom: `${Math.max(0, Math.min(92, value))}%`, borderColor: color, shadowColor: color },
        ]}
      />
    </View>
  );
}

function AppTile({
  app,
  fallbackIcon,
  onLaunch,
  onRemove,
  onMoveLeft,
  onMoveRight,
}: {
  app: { id: string; name: string; icon?: string };
  fallbackIcon: ComponentProps<typeof Ionicons>['name'];
  onLaunch: () => void;
  onRemove: () => void;
  onMoveLeft?: () => void;
  onMoveRight?: () => void;
}) {
  return (
    <Pressable
      style={({ pressed }) => [styles.appTile, pressed && styles.appTilePressed]}
      onPress={onLaunch}>
      <View style={styles.tileMoveCluster}>
        <Pressable
          style={({ pressed }) => [
            styles.tileMoveButton,
            !onMoveLeft && styles.tileMoveButtonDisabled,
            pressed && onMoveLeft && styles.pressed,
          ]}
          onPress={onMoveLeft}
          disabled={!onMoveLeft}
          hitSlop={8}>
          <Ionicons name="chevron-back" size={12} color={onMoveLeft ? '#D8DEE9' : 'rgba(216, 222, 233, 0.25)'} />
        </Pressable>
        <Pressable
          style={({ pressed }) => [
            styles.tileMoveButton,
            !onMoveRight && styles.tileMoveButtonDisabled,
            pressed && onMoveRight && styles.pressed,
          ]}
          onPress={onMoveRight}
          disabled={!onMoveRight}
          hitSlop={8}>
          <Ionicons name="chevron-forward" size={12} color={onMoveRight ? '#D8DEE9' : 'rgba(216, 222, 233, 0.25)'} />
        </Pressable>
      </View>
      <Pressable
        style={({ pressed }) => [styles.tileDeleteButton, pressed && styles.pressed]}
        onPress={onRemove}
        hitSlop={8}>
        <Ionicons name="trash" size={13} color="#BF616A" />
      </Pressable>
      <View style={styles.tileIconWrap}>
        {app.icon ? (
          <RNImage source={{ uri: app.icon }} style={styles.tileIconImage} resizeMode="contain" />
        ) : (
          <Ionicons name={fallbackIcon} size={32} color="#88C0D0" />
        )}
      </View>
      <Text style={styles.tileName} numberOfLines={1} ellipsizeMode="tail">
        {app.name}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#2E3440',
  },
  ambientBackground: {
    ...StyleSheet.absoluteFillObject,
  },
  container: {
    flex: 1,
    paddingHorizontal: 12,
    paddingTop: 16,
    paddingBottom: 0,
  },
  glassClip: {
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: 'rgba(236, 239, 244, 0.08)',
  },
  // ... existing styles
  demoBanner: {
    alignSelf: 'flex-start',
    backgroundColor: '#EBCB8B',
    borderRadius: 999,
    marginBottom: 12,
    paddingHorizontal: 12,
    paddingVertical: 6,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  demoBannerText: {
    color: '#2E3440',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 20,
    gap: 12,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.3,
    shadowRadius: 12,
  },
  headerLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    flex: 1,
    minWidth: 0,
  },
  headerTextWrap: {
    flex: 1,
    minWidth: 0,
  },
  headerMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 3,
  },
  title: {
    color: '#ECEFF4',
    fontSize: 24,
    fontWeight: '800',
    letterSpacing: -0.3,
  },
  deviceName: {
    color: '#D8DEE9',
    fontSize: 12.5,
    fontWeight: '500',
    flexShrink: 1,
  },
  metaDot: {
    width: 3,
    height: 3,
    borderRadius: 1.5,
    backgroundColor: 'rgba(76, 86, 106, 0.5)',
    marginHorizontal: 6,
    flexShrink: 0,
  },
  headerStatusText: {
    fontSize: 12.5,
    fontWeight: '600',
    flexShrink: 1,
  },
  statusPill: {
    width: 38,
    height: 38,
    borderRadius: 19,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    flexShrink: 0,
  },
  statusPillOnline: {
    backgroundColor: 'rgba(163, 190, 140, 0.12)',
    borderColor: 'rgba(163, 190, 140, 0.35)',
  },
  statusPillOffline: {
    backgroundColor: 'rgba(191, 97, 106, 0.1)',
    borderColor: 'rgba(191, 97, 106, 0.3)',
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  statusOnline: {
    backgroundColor: '#A3BE8C',
    shadowColor: '#A3BE8C',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.8,
    shadowRadius: 4,
  },
  statusOffline: {
    backgroundColor: '#BF616A',
    shadowColor: '#BF616A',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.8,
    shadowRadius: 4,
  },
  menuButton: {
    width: 38,
    height: 38,
    borderRadius: 19,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  menuButtonActive: {
    borderColor: '#88C0D0',
    backgroundColor: 'rgba(76, 86, 106, 0.18)',
  },
  menuButtonText: {
    color: '#D8DEE9',
    fontSize: 18,
  },
  loginContainer: {
    flex: 1,
    justifyContent: 'center',
    gap: 14,
  },
  loginHeader: {
    alignItems: 'center',
    gap: 12,
    marginBottom: 8,
  },
  helperText: {
    color: '#ECEFF4',
    fontSize: 20,
    fontWeight: '700',
    textAlign: 'center',
  },
  helperSubtext: {
    color: '#88C0D0',
    fontSize: 13,
    lineHeight: 18,
    textAlign: 'center',
    marginTop: -4,
  },
  remoteContainer: {
    flex: 1,
    justifyContent: 'space-between',
  },
  remoteScroll: {
    flex: 1,
  },
  remoteScrollContent: {
    gap: 20,
    paddingBottom: 24,
    paddingTop: 8,
  },
  remoteControl: {
    gap: 18,
  },
  appsContainer: {
    gap: 6,
  },
  appsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  appsHeaderButtons: {
    flexDirection: 'row',
    gap: 8,
  },
  appsTitle: {
    color: '#ECEFF4',
    fontSize: 24,
    fontWeight: '800',
  },
  refreshButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  appsSubtitle: {
    color: '#D8DEE9',
    fontSize: 13,
    marginBottom: 14,
  },
  appsGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 12,
  },
  sectionHeader: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 8,
    marginBottom: 4,
    paddingHorizontal: 4,
  },
  sectionTitle: {
    color: '#88C0D0',
    fontSize: 16,
    fontWeight: '700',
  },
  appTile: {
    width: '47%',
    backgroundColor: 'rgba(59, 66, 82, 0.5)',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(236, 239, 244, 0.08)',
    paddingVertical: 16,
    paddingHorizontal: 12,
    alignItems: 'center',
    gap: 10,
    position: 'relative',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.25,
    shadowRadius: 8,
  },
  appTilePressed: {
    opacity: 0.85,
    transform: [{ scale: 0.97 }],
  },
  tileDeleteButton: {
    position: 'absolute',
    top: 8,
    right: 8,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: 'rgba(46, 52, 64, 0.7)',
    borderWidth: 1,
    borderColor: 'rgba(236, 239, 244, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
  tileMoveCluster: {
    position: 'absolute',
    top: 8,
    left: 8,
    flexDirection: 'row',
    gap: 4,
    zIndex: 10,
  },
  tileMoveButton: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: 'rgba(46, 52, 64, 0.7)',
    borderWidth: 1,
    borderColor: 'rgba(236, 239, 244, 0.1)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  tileMoveButtonDisabled: {
    opacity: 0.4,
  },
  tileIconWrap: {
    width: 64,
    height: 64,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    backgroundColor: 'rgba(136, 192, 208, 0.12)',
    borderColor: 'rgba(136, 192, 208, 0.4)',
  },
  tileIconImage: {
    width: 40,
    height: 40,
    borderRadius: 10,
  },
  tileName: {
    color: '#ECEFF4',
    fontSize: 14,
    fontWeight: '700',
    textAlign: 'center',
  },
  emptyApps: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 60,
    gap: 12,
  },
  emptyAppsText: {
    color: '#D8DEE9',
    fontSize: 18,
    fontWeight: '600',
  },
  emptyAppsSubtext: {
    color: '#D8DEE9',
    fontSize: 13,
  },
  addAppForm: {
    backgroundColor: 'rgba(59, 66, 82, 0.55)',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    padding: 16,
    marginBottom: 16,
    gap: 12,
  },
  addAppTitle: {
    color: '#ECEFF4',
    fontSize: 18,
    fontWeight: '700',
    marginBottom: 4,
  },
  appInput: {
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    padding: 14,
    color: '#ECEFF4',
    fontSize: 15,
  },
  appTypeSelector: {
    flexDirection: 'row',
    gap: 8,
  },
  appTypeButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    padding: 12,
    borderRadius: 10,
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  appTypeButtonActive: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderColor: '#88C0D0',
  },
  appTypeText: {
    color: '#D8DEE9',
    fontSize: 14,
    fontWeight: '600',
  },
  appTypeTextActive: {
    color: '#88C0D0',
  },
  addAppButtons: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 4,
  },
  addAppActionButton: {
    flex: 1,
    padding: 14,
    borderRadius: 12,
    alignItems: 'center',
  },
  cancelButton: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  cancelButtonText: {
    color: '#D8DEE9',
    fontSize: 15,
    fontWeight: '700',
  },
  saveButton: {
    backgroundColor: '#A3BE8C',
  },
  saveButtonText: {
    color: '#ECEFF4',
    fontSize: 15,
    fontWeight: '700',
  },
  dpadContainer: {
    alignItems: 'center',
    marginBottom: 16,
  },
  dpadCircle: {
    width: 240,
    height: 240,
    borderRadius: 120,
    borderWidth: 2,
    borderColor: 'rgba(236, 239, 244, 0.14)',
    position: 'relative',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
  },
  dpadButton: {
    position: 'absolute',
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: 'rgba(76, 86, 106, 0.14)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
  },
  dpadTop: {
    top: 12,
    left: 88,
  },
  dpadBottom: {
    bottom: 12,
    left: 88,
  },
  dpadLeft: {
    left: 12,
    top: 88,
  },
  dpadRight: {
    right: 12,
    top: 88,
  },
  dpadButtonText: {
    color: '#ECEFF4',
    fontSize: 28,
    fontWeight: '800',
  },
  okButton: {
    position: 'absolute',
    top: 85,
    left: 85,
    width: 70,
    height: 70,
    borderRadius: 35,
    backgroundColor: 'rgba(136, 192, 208, 0.35)',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#88C0D0',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.7,
    shadowRadius: 10,
    borderWidth: 2,
    borderColor: '#81A1C1',
  },
  okButtonText: {
    color: '#ECEFF4',
    fontSize: 18,
    fontWeight: '800',
    letterSpacing: 0.5,
  },
  pressedOk: {
    transform: [{ scale: 0.95 }],
    opacity: 0.9,
  },
  controlMainLayout: {
    flexDirection: 'row',
    gap: 20,
    alignItems: 'flex-start',
    justifyContent: 'center',
    paddingHorizontal: 8,
  },
  centerControlColumn: {
    flex: 1,
    gap: 16,
  },
  volumeContainer: {
    alignItems: 'center',
    gap: 8,
    width: 80,
  },
  brightnessContainer: {
    alignItems: 'center',
    gap: 8,
    width: 80,
  },
  controlCard: {
    width: 80,
    gap: 10,
    alignItems: 'center',
  },
  controlHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    justifyContent: 'center',
  },
  controlLabel: {
    color: '#D8DEE9',
    fontSize: 11,
    fontWeight: '600',
  },
  controlSliderVertical: {
    height: 230,
    alignItems: 'center',
    gap: 8,
  },
  controlBtn: {
    width: 52,
    height: 52,
    borderRadius: 10,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  controlTrack: {
    flex: 1,
    width: 12,
    borderRadius: 6,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    overflow: 'hidden',
    justifyContent: 'flex-end',
  },
  controlTrackFill: {
    width: '100%',
    backgroundColor: '#A3BE8C',
    borderRadius: 6,
  },
  sliderTrackVertical: {
    flex: 1,
    width: 36,
    borderRadius: 18,
    backgroundColor: 'rgba(67, 76, 94, 0.4)',
    borderWidth: 1,
    borderColor: 'rgba(236, 239, 244, 0.1)',
    overflow: 'hidden',
    justifyContent: 'flex-end',
    alignItems: 'center',
  },
  sliderTrackFillVertical: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    borderRadius: 18,
  },
  sliderTrackIcon: {
    position: 'absolute',
    top: 10,
    alignSelf: 'center',
  },
  sliderThumb: {
    position: 'absolute',
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: '#ECEFF4',
    borderWidth: 3,
    marginBottom: -14,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 6,
  },
  controlValue: {
    color: '#ECEFF4',
    fontSize: 13,
    fontWeight: '600',
    textAlign: 'center',
  },
  buttonGroup: {
    gap: 10,
    marginBottom: 16,
  },
  groupLabel: {
    color: '#D8DEE9',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
    paddingLeft: 4,
  },
  actionButtonsRow: {
    flexDirection: 'row',
    gap: 10,
  },
  volumeRow: {
    flexDirection: 'row',
    gap: 12,
    alignItems: 'center',
  },
  volumeSliderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  sliderContainer: {
    flex: 1,
    gap: 8,
  },
  sliderTrack: {
    height: 8,
    borderRadius: 4,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    overflow: 'hidden',
  },
  sliderFill: {
    height: '100%',
    backgroundColor: '#A3BE8C',
    borderRadius: 3,
  },
  sliderButtons: {
    flexDirection: 'row',
    gap: 8,
  },
  sliderButton: {
    flex: 1,
    height: 40,
    borderRadius: 10,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  volumeButton: {
    flex: 1,
    minHeight: 64,
    borderRadius: 16,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  muteButton: {
    backgroundColor: '#88C0D0',
    borderColor: '#88C0D0',
    flex: 1.2,
  },
  muteButtonFull: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    height: 48,
    borderRadius: 12,
    backgroundColor: '#88C0D0',
    borderWidth: 1,
    borderColor: '#88C0D0',
  },
  muteButtonText: {
    color: '#ECEFF4',
    fontSize: 15,
    fontWeight: '700',
  },
  brightnessSliderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  brightnessPresetsRow: {
    flexDirection: 'row',
    gap: 8,
  },
  brightnessPresetButton: {
    flex: 1,
    height: 36,
    borderRadius: 8,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  brightnessPresetButtonActive: {
    backgroundColor: '#EBCB8B',
    borderColor: '#EBCB8B',
  },
  brightnessPresetText: {
    color: '#D8DEE9',
    fontSize: 13,
    fontWeight: '600',
  },
  brightnessPresetTextActive: {
    color: '#ECEFF4',
    fontWeight: '700',
  },
  playButtonSmall: {
    backgroundColor: '#A3BE8C',
    borderColor: '#A3BE8C',
  },
  playButtonTextSmall: {
    color: '#ECEFF4',
    fontWeight: '700',
  },
  actionButtonSmall: {
    flex: 1,
    minHeight: 56,
    borderRadius: 14,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  actionButtonText: {
    color: '#D8DEE9',
    fontSize: 13,
    fontWeight: '600',
  },
  closeButtonSmall: {
    backgroundColor: '#BF616A',
    borderColor: '#BF616A',
  },
  closeButtonTextSmall: {
    color: '#ECEFF4',
    fontWeight: '700',
  },
  recordingButton: {
    backgroundColor: '#BF616A',
    borderColor: '#BF616A',
  },
  fullscreenButtonSmall: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  fullscreenButtonTextSmall: {
    color: '#ECEFF4',
    fontWeight: '600',
  },
  buttonIcon: {
    marginBottom: 2,
  },
  keyboardContainer: {
    gap: 16,
  },
  keyboardInputWrapper: {
    gap: 12,
  },
  keyboardSection: {
    gap: 12,
  },
  keyboardInput: {
    minHeight: 140,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    color: '#D8DEE9',
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 15,
    textAlignVertical: 'top',
  },
  sendButton: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 20,
  },
  specialKeysRow: {
    flexDirection: 'row',
    gap: 10,
  },
  keyButton: {
    flex: 1,
    minHeight: 54,
    borderRadius: 12,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  keyButtonText: {
    color: '#D8DEE9',
    fontSize: 13,
    fontWeight: '600',
  },
  modifierButtonActive: {
    backgroundColor: 'rgba(136, 192, 208, 0.22)',
    borderColor: '#88C0D0',
  },
  modifierButtonTextActive: {
    color: '#88C0D0',
  },
  touchpadContainer: {
    flex: 1,
    gap: 12,
    marginBottom: 12,
  },
  touchpadWrapper: {
    flex: 1,
    flexDirection: 'row',
    gap: 8,
  },
  touchpadSurface: {
    flex: 1,
    borderRadius: 20,
    borderWidth: 2,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
  },
  touchpadInner: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  touchpadText: {
    color: '#ECEFF4',
    fontSize: 24,
    fontWeight: '700',
  },
  touchpadHint: {
    color: '#D8DEE9',
    fontSize: 13,
  },
  scrollBar: {
    width: 50,
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 4,
  },
  scrollButton: {
    width: 50,
    height: 50,
    borderRadius: 10,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  scrollTrack: {
    flex: 1,
    width: 50,
    borderRadius: 10,
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  touchpadButtons: {
    flexDirection: 'row',
    gap: 12,
  },
  touchpadButton: {
    flex: 1,
    minHeight: 56,
    borderRadius: 14,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  touchpadButtonText: {
    color: '#D8DEE9',
    fontSize: 14,
    fontWeight: '600',
  },
  tabBarContainer: {
    width: '100%',
    paddingHorizontal: 4,
    paddingBottom: 4,
  },
  tabBar: {
    flexDirection: 'row',
    borderRadius: 22,
    paddingVertical: 4,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -4 },
    shadowOpacity: 0.35,
    shadowRadius: 12,
  },
  tabItem: {
    flex: 1,
    paddingVertical: 10,
    marginVertical: 2,
    marginHorizontal: 2,
    borderRadius: 16,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  tabItemActive: {
    backgroundColor: 'rgba(136, 192, 208, 0.16)',
  },
  tabItemText: {
    color: '#D8DEE9',
    fontSize: 12,
    fontWeight: '600',
  },
  tabItemTextActive: {
    color: '#81A1C1',
    fontWeight: '700',
  },
  addressRow: {
    flexDirection: 'row',
    gap: 10,
  },
  ipInput: {
    flex: 1,
  },
  portInput: {
    width: 100,
  },
  input: {
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    color: '#D8DEE9',
    paddingHorizontal: 16,
    paddingVertical: 13,
    fontSize: 15,
  },
  formSectionLabel: {
    color: '#D8DEE9',
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 0.4,
    marginTop: 4,
    textTransform: 'uppercase',
  },
  actionButton: {
    minHeight: 50,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 12,
  },
  primaryButton: {
    backgroundColor: '#A3BE8C',
  },
  primaryButtonText: {
    color: '#ECEFF4',
    fontSize: 16,
    fontWeight: '700',
  },
  secondaryButton: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: '#88C0D0',
  },
  secondaryButtonText: {
    color: '#88C0D0',
    fontSize: 16,
    fontWeight: '700',
  },
  ghostButton: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  ghostButtonText: {
    color: '#D8DEE9',
    fontSize: 16,
    fontWeight: '700',
  },
  pressed: {
    opacity: 0.8,
    transform: [{ scale: 0.96 }],
  },
  menuOverlay: {
    flex: 1,
    backgroundColor: 'rgba(46, 52, 64, 0.55)',
    justifyContent: 'flex-start',
    paddingTop: 80,
    paddingHorizontal: 20,
    alignItems: 'flex-end',
  },
  menuSheetWrap: {
    width: '100%',
    maxWidth: 320,
  },
  menuSheet: {
    width: '100%',
    borderRadius: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.5,
    shadowRadius: 16,
  },
  menuHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 20,
    paddingVertical: 16,
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
  },
  menuHeaderTitle: {
    color: '#ECEFF4',
    fontSize: 20,
    fontWeight: '700',
  },
  menuDivider: {
    height: 1,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
  },
  menuSection: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    gap: 6,
  },
  menuSectionTitle: {
    color: '#D8DEE9',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 4,
  },
  systemRowLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    flex: 1,
  },
  activeBadge: {
    backgroundColor: '#A3BE8C',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  activeBadgeText: {
    color: '#ECEFF4',
    fontSize: 11,
    fontWeight: '700',
  },
  systemRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  systemRowActive: {
    borderColor: '#A3BE8C',
    backgroundColor: 'rgba(163, 190, 140, 0.14)',
  },
  systemRowText: {
    flex: 1,
    gap: 2,
  },
  systemName: {
    color: '#ECEFF4',
    fontSize: 15,
    fontWeight: '700',
  },
  systemMeta: {
    color: '#D8DEE9',
    fontSize: 12,
  },
  systemBadge: {
    color: '#A3BE8C',
    fontSize: 12,
    fontWeight: '700',
  },
  systemSwapLabel: {
    color: '#88C0D0',
    fontSize: 12,
    fontWeight: '700',
  },
  menuItem: {
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  menuItemContent: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  menuItemPressed: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
  },
  menuItemText: {
    color: '#88C0D0',
    fontSize: 15,
    fontWeight: '600',
  },
  menuItemDangerText: {
    color: '#BF616A',
    fontSize: 15,
    fontWeight: '600',
  },
  menuItemWarningText: {
    color: '#EBCB8B',
    fontSize: 15,
    fontWeight: '600',
  },
  editorOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(46, 52, 64, 0.55)',
  },
  editorSheet: {
    width: '100%',
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 32,
    gap: 12,
  },
  editorTitle: {
    color: '#ECEFF4',
    fontSize: 22,
    fontWeight: '800',
  },
  editorSubtitle: {
    color: '#D8DEE9',
    fontSize: 13,
    lineHeight: 18,
    marginBottom: 4,
  },
  channelsGrid: {
    gap: 12,
  },
  channelCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(59, 66, 82, 0.55)',
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
    gap: 14,
  },
  channelIconContainer: {
    width: 56,
    height: 56,
    borderRadius: 12,
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  channelInfo: {
    flex: 1,
    gap: 4,
  },
  channelNumber: {
    fontSize: 13,
    color: '#88C0D0',
    fontWeight: '600',
  },
  channelName: {
    fontSize: 17,
    color: '#ECEFF4',
    fontWeight: '600',
  },
  channelMeta: {
    fontSize: 13,
    color: '#D8DEE9',
  },
  // Settings Modals
  settingsOverlay: {
    flex: 1,
    backgroundColor: 'rgba(46, 52, 64, 0.55)',
    justifyContent: 'flex-end',
  },
  settingsSheetWrap: {
    flex: 1,
    width: '100%',
  },
  settingsSheet: {
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    maxHeight: '85%',
    paddingBottom: 20,
    flex: 1,
  },
  settingsHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(76, 86, 106, 0.18)',
    gap: 12,
  },
  settingsTitle: {
    flex: 1,
    fontSize: 20,
    color: '#ECEFF4',
    fontWeight: '600',
  },
  settingsCloseButton: {
    padding: 4,
  },
  settingsContent: {
    padding: 20,
    flex: 1,
  },
  settingsSectionTitle: {
    fontSize: 16,
    color: '#D8DEE9',
    fontWeight: '600',
    marginBottom: 12,
  },
  settingsSection: {
    marginBottom: 20,
  },
  settingsLoading: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 40,
    gap: 12,
  },
  settingsLoadingText: {
    fontSize: 16,
    color: '#D8DEE9',
  },
  settingsEmpty: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 40,
    gap: 12,
  },
  settingsEmptyText: {
    fontSize: 16,
    color: '#D8DEE9',
  },
  settingsInput: {
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    color: '#ECEFF4',
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  settingsMessage: {
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    padding: 12,
    borderRadius: 8,
    marginBottom: 16,
  },
  settingsMessageSuccess: {
    backgroundColor: 'rgba(163, 190, 140, 0.2)',
  },
  settingsMessageError: {
    backgroundColor: 'rgba(191, 97, 106, 0.2)',
  },
  settingsMessageText: {
    fontSize: 14,
    color: '#ECEFF4',
  },
  settingsActionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    padding: 14,
    borderRadius: 8,
    gap: 8,
    marginTop: 12,
  },
  settingsActionButtonText: {
    fontSize: 16,
    color: '#88C0D0',
    fontWeight: '600',
  },
  // WiFi
  networkItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    padding: 14,
    borderRadius: 8,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  networkItemActive: {
    borderColor: '#A3BE8C',
    backgroundColor: 'rgba(163, 190, 140, 0.1)',
  },
  networkItemLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    flex: 1,
  },
  networkItemText: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  networkName: {
    fontSize: 16,
    color: '#ECEFF4',
    fontWeight: '500',
  },
  networkSignal: {
    fontSize: 14,
    color: '#D8DEE9',
  },
  // Bluetooth
  bluetoothItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    padding: 14,
    borderRadius: 8,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  bluetoothItemLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    flex: 1,
  },
  bluetoothItemText: {
    gap: 4,
  },
  bluetoothName: {
    fontSize: 16,
    color: '#ECEFF4',
    fontWeight: '500',
  },
  bluetoothMac: {
    fontSize: 12,
    color: '#D8DEE9',
  },
  bluetoothActions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  bluetoothConnectButton: {
    backgroundColor: '#A3BE8C',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
  },
  bluetoothConnectText: {
    fontSize: 14,
    color: '#ECEFF4',
    fontWeight: '600',
  },
  bluetoothConnectedText: {
    fontSize: 14,
    color: '#A3BE8C',
    fontWeight: '600',
  },
  bluetoothRemoveButton: {
    padding: 6,
  },
  // Sound
  speakerItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: 'rgba(46, 52, 64, 0.6)',
    padding: 14,
    borderRadius: 8,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  speakerItemActive: {
    borderColor: '#A3BE8C',
    backgroundColor: 'rgba(163, 190, 140, 0.1)',
  },
  speakerItemLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    flex: 1,
  },
  speakerName: {
    fontSize: 16,
    color: '#ECEFF4',
    fontWeight: '500',
  },
  speakerActiveBadge: {
    backgroundColor: '#A3BE8C',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
  },
  speakerActiveText: {
    fontSize: 12,
    color: '#ECEFF4',
    fontWeight: '600',
  },
  settingsSubtitle: {
    fontSize: 14,
    color: '#D8DEE9',
    marginBottom: 16,
  },
  appItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: 16,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderRadius: 12,
    marginBottom: 8,
  },
  appItemLeft: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    gap: 12,
  },
  appItemIcon: {
    width: 24,
    height: 24,
  },
  appItemText: {
    flex: 1,
  },
  recommendedAppsHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  appItemAdded: {
    backgroundColor: 'rgba(163, 190, 140, 0.14)',
    borderWidth: 1,
    borderColor: '#A3BE8C',
  },
  appRemoveButton: {
    padding: 8,
    borderRadius: 8,
    backgroundColor: 'rgba(191, 97, 106, 0.1)',
  },
  addAppCard: {
    backgroundColor: 'rgba(59, 66, 82, 0.55)',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  addAppCardTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#ECEFF4',
    marginBottom: 12,
  },
  addAppChoiceButtons: {
    flexDirection: 'row',
    gap: 12,
    marginBottom: 16,
  },
  addAppChoiceButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 12,
    paddingHorizontal: 16,
    backgroundColor: 'rgba(67, 76, 94, 0.45)',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: 'rgba(76, 86, 106, 0.18)',
  },
  addAppChoiceButtonActive: {
    backgroundColor: 'rgba(136, 192, 208, 0.15)',
    borderColor: '#88C0D0',
  },
  addAppChoiceButtonText: {
    fontSize: 15,
    fontWeight: '600',
    color: '#D8DEE9',
  },
  addAppChoiceButtonTextActive: {
    color: '#88C0D0',
  },
});
