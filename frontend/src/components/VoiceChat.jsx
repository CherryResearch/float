import React, { useEffect, useState } from "react";
import { Room, RoomEvent } from "livekit-client";
import axios from "axios";
import { ensureDeviceAndToken } from "../utils/sync";
import "../styles/Chat.css";

const VoiceChat = () => {
  const [connected, setConnected] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  useEffect(() => {
    let room;
    const join = async () => {
      try {
        // Ensure device token before establishing voice session
        await ensureDeviceAndToken();
        const res = await axios.post("/api/voice/connect", {
          identity: crypto.randomUUID(),
          room: "float",
        });
        room = new Room();
        await room.connect(res.data.url, res.data.token, {
          autoSubscribe: true,
        });
        setConnected(true);
        room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          const remoteSpeaking = speakers.some((p) => !p.isLocal);
          setSpeaking(remoteSpeaking);
        });
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        room.localParticipant.publishTrack(stream.getAudioTracks()[0]);
      } catch (err) {
        console.error("livekit connect failed", err);
      }
    };
    join();
    return () => {
      if (room) {
        room.disconnect();
      }
      setConnected(false);
      setSpeaking(false);
    };
  }, []);

  return (
    <div className="voice-chat">
      <div
        className={`mic-btn ${connected ? "recording" : ""} ${
          speaking ? "speaking" : ""
        }`}
        aria-label="Voice connection status"
      >
        🎤
      </div>
    </div>
  );
};

export default VoiceChat;
