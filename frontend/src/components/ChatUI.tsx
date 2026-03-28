
import React, {
  useState,
  useContext,
  useRef,
  useEffect,
  useCallback,
  useMemo,
} from 'react';
import '../styles/Chat.css';
import { GlobalContext } from '../main';
import { startVoiceSession } from '../utils/apiClient';

const CAMERA_PLACEHOLDER = '📷';
const STATUS_CONNECTED = 'connected';
const STATUS_CONNECTING = 'connecting';
const STATUS_FAILED = 'failed';
const STATUS_IDLE = 'idle';

type VoiceSession = {
  provider: string;
  url?: string;
  token?: string;
  client_secret?: string;
  expires_at?: string | number | null;
  session?: Record<string, unknown>;
};

declare global {
  interface Window {
    pipecat?: any;
    livekit?: any;
  }
}

/**
 * ChatUI component with Live Mode support.
 *
 * Live Mode now requests connection details from `/api/voice/connect` and, when
 * the backend is configured for the OpenAI Realtime API, establishes a minimal
 * WebRTC session so we can stream microphone audio without relying on LiveKit.
 * The UI keeps the placeholder layout while we iterate on the richer controls.
 */
const ChatUI: React.FC = () => {
  const { state, setState } = useContext(GlobalContext);
  const [isLive, setIsLive] = useState(false);
  const [micEnabled, setMicEnabled] = useState(true);
  const [cameraEnabled, setCameraEnabled] = useState(true);
  const [connectionStatus, setConnectionStatus] = useState<string>(STATUS_IDLE);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [sessionInfo, setSessionInfo] = useState<VoiceSession | null>(null);
  const [consoleCollapsed, setConsoleCollapsed] = useState<boolean>(true);

  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const lastIdentityRef = useRef<string>('');

  const connectionBadge = useMemo(() => {
    switch (connectionStatus) {
      case STATUS_CONNECTED:
        return 'Live (OpenAI Realtime)';
      case STATUS_CONNECTING:
        return 'Starting live session…';
      case STATUS_FAILED:
        return 'Live session failed';
      default:
        return 'Live mode idle';
    }
  }, [connectionStatus]);

  const cleanupLiveSession = useCallback(() => {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    peerRef.current?.close();
    peerRef.current = null;

    if (localStreamRef.current) {
      localStreamRef.current.getTracks().forEach((track) => track.stop());
      localStreamRef.current = null;
    }

    if (remoteAudioRef.current) {
      remoteAudioRef.current.srcObject = null;
    }

    setSessionInfo(null);
    setMicEnabled(true);
    setCameraEnabled(true);
  }, []);

  useEffect(() => () => {
    cleanupLiveSession();
  }, [cleanupLiveSession]);

  const initializeOpenAiRealtime = useCallback(
    async (session: VoiceSession, reconnectAttempt = false) => {
      if (!session.client_secret) {
        throw new Error('Realtime session missing client secret');
      }

      const pc = new RTCPeerConnection();
      peerRef.current = pc;

      pc.ontrack = (event) => {
        if (remoteAudioRef.current) {
          remoteAudioRef.current.srcObject = event.streams[0];
        }
      };

      pc.onconnectionstatechange = () => {
        if (!peerRef.current) {
          return;
        }
        const state = peerRef.current.connectionState;
        if (state === 'connected') {
          setConnectionStatus(STATUS_CONNECTED);
        }
        if (['failed', 'disconnected', 'closed'].includes(state)) {
          setConnectionStatus(STATUS_FAILED);
          setLiveError('Realtime session disconnected');
          setIsLive(false);
          cleanupLiveSession();
        }
      };

      const localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      localStreamRef.current = localStream;
      localStream.getAudioTracks().forEach((track) => pc.addTrack(track, localStream));

      const offer = await pc.createOffer({ offerToReceiveAudio: true, offerToReceiveVideo: false });
      await pc.setLocalDescription(offer);

      const offerSdp = offer.sdp;
      if (!offerSdp) {
        throw new Error('Failed to create SDP offer');
      }

      if (!session.url) {
        throw new Error('Realtime session missing URL');
      }

      const targetUrl = session.url.replace(/^ws(s)?:\/\//, 'http$1://');
      const response = await fetch(targetUrl, {
        method: 'POST',
        body: offerSdp,
        headers: {
          Authorization: `Bearer ${session.client_secret}`,
          'Content-Type': 'application/sdp',
        },
      });

      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || `Realtime handshake failed (${response.status})`);
      }

      const answerSdp = await response.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

      if (!reconnectAttempt) {
        setIsLive(true);
      }
    },
    [cleanupLiveSession],
  );

  const startSession = useCallback(
    async (identity: string, isReconnect = false) => {
      setConnectionStatus(STATUS_CONNECTING);

      try {
        const session = await startVoiceSession(identity, 'float-light');
        setSessionInfo(session);

        if (session.provider === 'openai-realtime') {
          await initializeOpenAiRealtime(session, isReconnect);
        } else if (session.provider === 'livekit') {
          window?.livekit?.connect?.(session);
          setIsLive(true);
        } else {
          throw new Error(`Unsupported streaming provider: ${session.provider}`);
        }

        setLiveError(null);
        setConnectionStatus(STATUS_CONNECTED);
      } catch (error) {
        console.error('Failed to start live session', error);
        const message = error instanceof Error ? error.message : 'Unknown streaming error';
        setLiveError(message);
        setConnectionStatus(STATUS_FAILED);
        cleanupLiveSession();
        throw error;
      }
    },
    [cleanupLiveSession, initializeOpenAiRealtime],
  );

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      return;
    }
    reconnectTimerRef.current = window.setTimeout(async () => {
      reconnectTimerRef.current = null;
      const identity = lastIdentityRef.current || `client-${Date.now()}`;
      try {
        await startSession(identity, true);
        setIsLive(true);
      } catch (error) {
        // Keep the failure state visible; offer manual retry via button.
      }
    }, 3000);
  }, [startSession]);

  const enterLiveMode = useCallback(async () => {
    if (connectionStatus === STATUS_CONNECTING) {
      return;
    }

    const identity = typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `client-${Date.now()}`;

    lastIdentityRef.current = identity;
    try {
      await startSession(identity);
    } catch (error) {
      // Already handled in startSession.
    }
  }, [connectionStatus, startSession]);

  const exitLive = useCallback(() => {
    cleanupLiveSession();
    setIsLive(false);
    setConnectionStatus(STATUS_IDLE);
    setLiveError(null);
  }, [cleanupLiveSession]);

  const toggleMic = useCallback(() => {
    const stream = localStreamRef.current;
    setMicEnabled((prev) => {
      const next = !prev;
      if (stream) {
        stream.getAudioTracks().forEach((track) => {
          track.enabled = next;
        });
      } else {
        // Fallback for environments where we still rely on global helpers.
        window?.pipecat?.session?.toggleMic?.();
        window?.livekit?.toggleMic?.();
      }
      return next;
    });
  }, []);

  const toggleCamera = useCallback(() => {
    setCameraEnabled((prev) => !prev);
    window?.livekit?.toggleCamera?.();
  }, []);

  const retryLiveSession = useCallback(async () => {
    const identity = lastIdentityRef.current || `client-${Date.now()}`;
    try {
      await startSession(identity, true);
    } catch (error) {
      // keep error state to surface to user
    }
  }, [startSession]);

  useEffect(() => {
    if (connectionStatus === STATUS_FAILED && sessionInfo?.provider === 'openai-realtime') {
      scheduleReconnect();
    }
  }, [connectionStatus, scheduleReconnect, sessionInfo]);

  return (
    <div className="chat-ui">
      {!isLive && (
        <div className="chat-history" data-testid="chat-history">
          <p>Chat history</p>
          {connectionStatus === STATUS_CONNECTING && (
            <p className="live-status">Connecting live session…</p>
          )}
          {liveError && <p className="live-error">{liveError}</p>}
        </div>
      )}

      {isLive && (
        <div className="live-mode" data-testid="live-mode">
          <div className="video-feed">Video Feed</div>
          <button className="send-picture" aria-label="Send picture">
            {CAMERA_PLACEHOLDER}
          </button>
          <button className="exit-live" aria-label="Exit live mode" onClick={exitLive}>
            X
          </button>
          <audio ref={remoteAudioRef} autoPlay playsInline muted={false} />
          <p className="live-status">{connectionBadge}</p>
        </div>
      )}

      <div className="entry-area">
        <input type="text" placeholder="Type..." />
        <button className="stt-mic" aria-label="Speech to text">
          🎤
        </button>
        <div className="live-controls">
          <button className="theme-toggle" onClick={() => setState((prev) => ({
            ...prev,
            theme: prev.theme === 'dark' ? 'light' : 'dark',
          }))} aria-label="Toggle theme">
            {state.theme === 'dark' ? 'Light Mode' : 'Dark Mode'}
          </button>
          <button
            className="live-mode-btn"
            onClick={enterLiveMode}
            aria-label="Activate live mode"
            disabled={connectionStatus === STATUS_CONNECTING}
          >
            {connectionStatus === STATUS_CONNECTING ? 'Starting…' : 'Live Mode'}
          </button>
          <div className="media-toggles">
            <button
              className="toggle-mic"
              aria-label={micEnabled ? 'Mute microphone' : 'Unmute microphone'}
              onClick={toggleMic}
              disabled={connectionStatus === STATUS_CONNECTING || (!isLive && !localStreamRef.current)}
            >
              {micEnabled ? 'Mic On' : 'Mic Off'}
            </button>
            <button
              className="toggle-camera"
              aria-label={cameraEnabled ? 'Disable camera' : 'Enable camera'}
              onClick={toggleCamera}
            >
              {cameraEnabled ? 'Cam On' : 'Cam Off'}
            </button>
          </div>
        </div>
      </div>

      {(connectionStatus === STATUS_FAILED || liveError) && (
        <div className="live-console">
          <div className="console-header" onClick={() => setConsoleCollapsed((prev) => !prev)}>
            <span className="console-title">Live diagnostics</span>
            <span className="console-toggle">{consoleCollapsed ? '+' : '-'}</span>
          </div>
          {!consoleCollapsed && (
            <div className="console-body">
              <p>Status: {connectionBadge}</p>
              {liveError && <p>Error: {liveError}</p>}
              <button onClick={retryLiveSession} className="retry-button">
                Retry now
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ChatUI;
