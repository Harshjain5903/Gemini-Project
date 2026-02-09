import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getDistance } from './navUtils';
import './ChatPanel.css';

const WELCOME_MESSAGE = {
  role: 'assistant',
  content: "Hey! Tell me where you want to go in Chicago and I'll find you a safe route."
};

const EXAMPLE_PROMPTS = [
  "Walk from Millennium Park to Navy Pier at 11 PM",
  "Willis Tower to Wrigley Field, morning rush hour",
  "I'm alone walking from Wicker Park to the Loop at night"
];

function TypingIndicator() {
  return (
    <div className="typing-indicator">
      <span></span>
      <span></span>
      <span></span>
    </div>
  );
}

function ChatMessage({ message }) {
  if (message.role === 'user') {
    return <div className="chat-msg user">{message.content}</div>;
  }

  if (message.role === 'briefing') {
    const renderLine = (line, lineKey) => {
      const parts = [];
      let lastIndex = 0;
      const boldRegex = /\*\*(.+?)\*\*/g;
      let match;
      while ((match = boldRegex.exec(line)) !== null) {
        if (match.index > lastIndex) {
          parts.push(line.slice(lastIndex, match.index));
        }
        parts.push(<strong key={`${lineKey}-b-${match.index}`}>{match[1]}</strong>);
        lastIndex = boldRegex.lastIndex;
      }
      if (lastIndex < line.length) {
        parts.push(line.slice(lastIndex));
      }
      return parts.length > 0 ? parts : [line];
    };

    const renderBriefing = (text) => {
      return text.split('\n\n').map((block, i) => {
        const lines = block.split('\n');
        return (
          <p key={i} className="briefing-text">
            {lines.map((line, j) => (
              <React.Fragment key={j}>
                {j > 0 && <br />}
                {renderLine(line, `${i}-${j}`)}
              </React.Fragment>
            ))}
          </p>
        );
      });
    };

    return (
      <div className="chat-msg briefing">
        <div className="briefing-header">
          Safety Briefing
        </div>
        <div className="briefing-body">
          {renderBriefing(message.content)}
        </div>
        {message.metrics && (
          <div className="briefing-stats">
            <span className="briefing-stat green">
              -{message.metrics.reduction_in_risk_pct}% risk
            </span>
            <span className="briefing-stat orange">
              +{Math.round(message.metrics.extra_time_seconds)}s time
            </span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={`chat-msg assistant ${message.isError ? 'error' : ''}`}>
      {message.content}
    </div>
  );
}

// Browser Speech APIs
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

// Pick a random item from an array (keeps responses from sounding scripted)
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

// Default speeds (m/s) per travel mode for time-based turn alerts
const MODE_SPEEDS = { walking: 1.4, cycling: 4.5, driving: 8.0 };

export default function ChatPanel({ onRouteReceived, onStartNavigation, navContext, userCoords, weather }) {
  const [messages, setMessages] = useState([WELCOME_MESSAGE]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [showExamples, setShowExamples] = useState(true);
  const [hasRoute, setHasRoute] = useState(false);
  const [pendingRoute, setPendingRoute] = useState(null);

  // Voice call state
  const [callActive, setCallActive] = useState(false);
  // 'idle' | 'listening' | 'processing' | 'speaking'
  const [callState, setCallState] = useState('idle');
  const [transcript, setTranscript] = useState('');
  const [callDuration, setCallDuration] = useState(0);

  const messagesEndRef = useRef(null);
  const recognitionRef = useRef(null);
  const callActiveRef = useRef(false);
  const durationRef = useRef(null);
  const callGenRef = useRef(0); // generation counter to prevent orphaned call loops

  // Travel mode pending route ref (for voice mode async access)
  const pendingRouteRef = useRef(null);

  // Navigation-aware voice refs
  const userCoordsRef = useRef(null);
  const navContextRef = useRef(null);
  const lastAlertedStepRef = useRef(-1);
  const turnAlertPendingRef = useRef(null);
  const speakingUtteranceRef = useRef(null);
  const speakResolveRef = useRef(null);
  const farAlertedStepRef = useRef(-1);
  const offRouteAlertedRef = useRef(false);

  // Persistent audio element for mobile autoplay unlock
  const sharedAudioRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      endCall();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep refs in sync for async access (avoids stale closures in callLoop)
  useEffect(() => {
    navContextRef.current = navContext || null;
  }, [navContext]);

  useEffect(() => {
    userCoordsRef.current = userCoords || null;
  }, [userCoords]);

  // Keep pendingRoute ref in sync for voice mode async access
  useEffect(() => {
    pendingRouteRef.current = pendingRoute;
  }, [pendingRoute]);

  // Keep hasRoute ref in sync for voice mode async access (avoids stale closure in callLoop)
  const hasRouteRef = useRef(false);
  useEffect(() => {
    hasRouteRef.current = hasRoute;
  }, [hasRoute]);

  // Call duration timer
  useEffect(() => {
    if (callActive) {
      setCallDuration(0);
      durationRef.current = setInterval(() => {
        setCallDuration(d => d + 1);
      }, 1000);
    } else {
      if (durationRef.current) clearInterval(durationRef.current);
      setCallDuration(0);
    }
    return () => {
      if (durationRef.current) clearInterval(durationRef.current);
    };
  }, [callActive]);

  const formatCallTime = (s) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  // --- TTS using Gemini's natural voice via backend ---
  const stopCurrentAudio = useCallback(() => {
    const audio = sharedAudioRef.current;
    if (audio) {
      audio.pause();
      if (audio._objectUrl) { URL.revokeObjectURL(audio._objectUrl); audio._objectUrl = null; }
      audio.removeAttribute('src');
      audio.onended = null;
      audio.onerror = null;
    }
    // Also stop browser SpeechSynthesis
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    speakingUtteranceRef.current = null;
    // Resolve any pending speak promise so the call loop continues immediately
    const pending = speakResolveRef.current;
    speakResolveRef.current = null;
    if (pending) pending();
  }, []);

  const speak = useCallback((text) => {
    return new Promise(async (resolve) => {
      stopCurrentAudio();
      speakResolveRef.current = resolve;
      const clean = text.replace(/\*\*(.+?)\*\*/g, '$1');

      try {
        const res = await fetch('/api/tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: clean })
        });

        if (!res.ok) throw new Error('TTS request failed');
        if (!callActiveRef.current) { speakResolveRef.current = null; resolve(); return; }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = sharedAudioRef.current;

        if (!audio) { URL.revokeObjectURL(url); resolve(); return; }

        if (audio._objectUrl) URL.revokeObjectURL(audio._objectUrl);
        audio._objectUrl = url;
        speakingUtteranceRef.current = audio;

        const safetyTimeout = setTimeout(() => {
          stopCurrentAudio();
        }, 60000);

        audio.onended = () => {
          clearTimeout(safetyTimeout);
          URL.revokeObjectURL(url);
          audio._objectUrl = null;
          speakingUtteranceRef.current = null;
          speakResolveRef.current = null;
          resolve();
        };
        audio.onerror = () => {
          clearTimeout(safetyTimeout);
          URL.revokeObjectURL(url);
          audio._objectUrl = null;
          speakingUtteranceRef.current = null;
          speakResolveRef.current = null;
          resolve();
        };

        audio.src = url;
        audio.play().catch(() => {
          clearTimeout(safetyTimeout);
          URL.revokeObjectURL(url);
          audio._objectUrl = null;
          speakingUtteranceRef.current = null;
          speakResolveRef.current = null;
          resolve();
        });
      } catch {
        speakingUtteranceRef.current = null;
        speakResolveRef.current = null;
        resolve();
      }
    });
  }, [stopCurrentAudio]);

  // For long texts, just call speak directly — Gemini handles full text well
  const speakLong = useCallback(async (text) => {
    await speak(text);
  }, [speak]);

  // Instant browser TTS for urgent turn alerts (near-zero latency vs Gemini's 3-4s)
  const speakBrowserTTS = useCallback((text) => {
    return new Promise((resolve) => {
      stopCurrentAudio();
      if (!window.speechSynthesis) { resolve(); return; }
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.rate = 1.1;
      utter.pitch = 1.0;
      let resolved = false;
      const done = () => { if (!resolved) { resolved = true; resolve(); } };
      utter.onend = done;
      utter.onerror = done;
      window.speechSynthesis.speak(utter);
      setTimeout(done, 8000);
    });
  }, [stopCurrentAudio]);

  // Play pre-generated audio from base64 (skips the /tts round trip entirely)
  const speakFromAudio = useCallback((base64Audio, mimeType) => {
    return new Promise((resolve) => {
      stopCurrentAudio();
      speakResolveRef.current = resolve;

      try {
        const raw = atob(base64Audio);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        const blob = new Blob([bytes], { type: mimeType || 'audio/wav' });
        const url = URL.createObjectURL(blob);
        const audio = sharedAudioRef.current;

        if (!audio) { URL.revokeObjectURL(url); resolve(); return; }

        if (audio._objectUrl) URL.revokeObjectURL(audio._objectUrl);
        audio._objectUrl = url;
        speakingUtteranceRef.current = audio;

        const safetyTimeout = setTimeout(() => { stopCurrentAudio(); }, 60000);

        audio.onended = () => {
          clearTimeout(safetyTimeout);
          URL.revokeObjectURL(url);
          audio._objectUrl = null;
          speakingUtteranceRef.current = null;
          speakResolveRef.current = null;
          resolve();
        };
        audio.onerror = () => {
          clearTimeout(safetyTimeout);
          URL.revokeObjectURL(url);
          audio._objectUrl = null;
          speakingUtteranceRef.current = null;
          speakResolveRef.current = null;
          resolve();
        };

        audio.src = url;
        audio.play().catch(() => {
          clearTimeout(safetyTimeout);
          URL.revokeObjectURL(url);
          audio._objectUrl = null;
          speakingUtteranceRef.current = null;
          speakResolveRef.current = null;
          resolve();
        });
      } catch {
        speakingUtteranceRef.current = null;
        speakResolveRef.current = null;
        resolve();
      }
    });
  }, [stopCurrentAudio]);

  // --- STT ---
  const listen = useCallback(() => {
    return new Promise((resolve, reject) => {
      if (!SpeechRecognition) {
        reject(new Error('Speech recognition not supported'));
        return;
      }

      // Prevent double-resolve (onresult + onend can both fire)
      let resolved = false;
      const safeResolve = (val) => {
        if (!resolved) { resolved = true; resolve(val); }
      };
      const safeReject = (val) => {
        if (!resolved) { resolved = true; reject(val); }
      };

      const recognition = new SpeechRecognition();
      recognition.lang = 'en-US';
      recognition.interimResults = true;
      recognition.continuous = false;
      recognition.maxAlternatives = 1;

      // Safety timeout: resolve empty after 15s if nothing happens
      const timeout = setTimeout(() => {
        try { recognition.stop(); } catch { /* ignore */ }
        safeResolve('');
      }, 15000);

      recognition.onresult = (event) => {
        const current = event.results[event.results.length - 1];
        setTranscript(current[0].transcript);
        if (current.isFinal) {
          clearTimeout(timeout);
          safeResolve(current[0].transcript);
        }
      };

      recognition.onerror = (e) => {
        clearTimeout(timeout);
        if (e.error === 'no-speech' || e.error === 'aborted') {
          safeResolve('');
        } else {
          safeReject(e);
        }
      };

      recognition.onend = () => {
        clearTimeout(timeout);
        safeResolve('');
      };

      recognitionRef.current = recognition;
      try {
        recognition.start();
      } catch {
        clearTimeout(timeout);
        safeResolve('');
      }
    });
  }, []);

  // --- Turn alert phrases (lots of variety so it never sounds robotic) ---
  const turnAlertPhrases = [
    (label, dist) => `Oh sorry to interrupt, but there's a ${label} coming up in like ${dist} meters.`,
    (label, dist) => `Hey, quick thing, you've got a ${label} in about ${dist} meters.`,
    (label, dist) => `Oh wait, ${label} coming up, about ${dist} meters ahead.`,
    (label, dist) => `So, heads up, ${label} in like ${dist} meters, okay?`,
    (label, dist) => `Just wanted to let you know, there's a ${label} about ${dist} meters from here.`,
    (label, dist) => `Real quick, ${label} ahead, maybe ${dist} meters or so.`,
    (label, dist) => `By the way, you're gonna want to ${label} in about ${dist} meters.`,
    (label, dist) => `Oh and, ${label} coming up pretty soon, like ${dist} meters.`,
  ];

  // --- Drain any pending turn alert ---
  // Supports { text, urgent } objects: urgent alerts use instant browser TTS
  const drainTurnAlert = useCallback(async () => {
    const alert = turnAlertPendingRef.current;
    if (!alert) return;
    turnAlertPendingRef.current = null;
    setCallState('speaking');
    const isUrgent = typeof alert === 'object' && alert.urgent;
    const text = typeof alert === 'string' ? alert : alert.text;
    if (isUrgent) {
      await speakBrowserTTS(text);
    } else {
      await speak(text);
    }
  }, [speak, speakBrowserTTS]);

  // --- Turn alert monitoring interval (time-based: alerts 10s before turn) ---
  useEffect(() => {
    if (!callActive || !navContext?.isNavigating) {
      lastAlertedStepRef.current = -1;
      farAlertedStepRef.current = -1;
      offRouteAlertedRef.current = false;
      return;
    }

    const monitor = setInterval(() => {
      const ctx = navContextRef.current;
      if (!ctx || !ctx.currentPosition || !ctx.instructions?.length) return;

      // Speed-aware thresholds: alerts trigger based on time-to-turn, not fixed distance
      const speed = MODE_SPEEDS[ctx.travelMode] || MODE_SPEEDS.walking;
      const farThreshold = 20 * speed;    // ~20 seconds before turn (Gemini TTS, natural voice)
      const closeThreshold = 10 * speed;  // ~10 seconds before turn (browser TTS, instant)

      // Off-route detection: warn if > 100m from any route point
      if (ctx.route && ctx.route.length > 1) {
        let minRouteDist = Infinity;
        for (let i = 0; i < ctx.route.length; i++) {
          const d = getDistance(ctx.currentPosition, ctx.route[i]);
          if (d < minRouteDist) minRouteDist = d;
        }
        if (minRouteDist > 100 && !offRouteAlertedRef.current) {
          offRouteAlertedRef.current = true;
          turnAlertPendingRef.current = { text: pick([
            "Hey, I think you might be going off route. Try to head back toward the path I showed you.",
            "Hmm, you're getting kinda far from the route. You might want to turn around.",
            "Wait, I don't think this is right. You're off the route, let's get you back on track.",
          ]), urgent: true };
          stopCurrentAudio();
        } else if (minRouteDist < 40) {
          offRouteAlertedRef.current = false;
        }
      }

      const nextStepIdx = ctx.currentStep + 1;

      // Check for arrival
      if (ctx.currentStep === ctx.instructions.length - 1) {
        const destDist = getDistance(ctx.currentPosition, ctx.instructions[ctx.instructions.length - 1].coord);
        if (destDist < 25 && lastAlertedStepRef.current !== ctx.instructions.length) {
          lastAlertedStepRef.current = ctx.instructions.length;
          turnAlertPendingRef.current = { text: pick([
            "Hey, you made it! You're right at your destination. Nice one!",
            "And, we're here! That's your stop. You did great!",
            "Okay, that's it, you've arrived! Look around, this is the place.",
            "We made it! You're at your destination. That wasn't too bad, right?",
          ]), urgent: false };
          stopCurrentAudio();
        }
        return;
      }

      if (nextStepIdx >= ctx.instructions.length) return;

      const nextInstr = ctx.instructions[nextStepIdx];
      const dist = getDistance(ctx.currentPosition, nextInstr.coord);
      const label = nextInstr.label.toLowerCase();
      const eta = Math.round(dist / speed);

      // Stage 1: Far heads-up ~20s before (Gemini TTS — natural buddy voice)
      if (dist < farThreshold && dist >= closeThreshold && nextStepIdx > farAlertedStepRef.current) {
        farAlertedStepRef.current = nextStepIdx;
        if (!label.includes('arrive')) {
          turnAlertPendingRef.current = { text: pick([
            `Oh by the way, there's a ${label} coming up in about ${eta} seconds.`,
            `Just a heads up, you'll need to ${label} in about ${eta} seconds.`,
            `So there's a ${label} ahead, like ${eta} seconds from here.`,
            `Oh and, ${label} coming up, about ${eta} seconds ahead.`,
          ]), urgent: false };
        } else {
          turnAlertPendingRef.current = { text: pick([
            `You're getting close! Your destination is about ${eta} seconds ahead.`,
            `Almost there! Maybe ${eta} more seconds and you're done.`,
          ]), urgent: false };
        }
      }

      // Stage 2: Close turn-now ~10s before (browser TTS — instant, no latency)
      if (dist < closeThreshold && nextStepIdx > lastAlertedStepRef.current) {
        lastAlertedStepRef.current = nextStepIdx;
        if (label.includes('arrive')) {
          turnAlertPendingRef.current = { text: pick([
            "You're here! That's your destination right there!",
            "And, we made it! Look around, this is the place!",
            "Okay, this is it! You've arrived!",
          ]), urgent: true };
        } else {
          turnAlertPendingRef.current = { text: pick([
            `Okay, ${label} right here!`,
            `Here's your turn, ${label} now!`,
            `Right now, ${label}!`,
            `This is it, ${label}!`,
            `Okay ${label}, right here, you see it?`,
          ]), urgent: true };
        }
        // Interrupt current speech for urgent turn alert
        stopCurrentAudio();
      }
    }, 400);

    return () => clearInterval(monitor);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [callActive, navContext?.isNavigating]);

  // --- Answer navigation questions locally ---
  const handleNavQuestion = useCallback((text) => {
    const ctx = navContextRef.current;
    if (!ctx || !ctx.isNavigating || !ctx.instructions?.length || !ctx.currentPosition) return null;

    const lower = text.toLowerCase();
    const navPatterns = [
      /\b(left|right|straight)\s*(or)\b/,
      /\bwhich\s*(way|direction|side)\b/,
      /\bwhere\s*(do i|should i|to)\s*(go|turn)\b/,
      /\bwhat('s| is)\s*(the\s*)?(next\s*)?turn\b/,
      /\bam i\s*(going|headed)\b/,
      /\bhow\s*far\b/,
      /\bwhen\s*(do i|should i)\s*turn\b/,
      /\bare we\s*there\b/,
      /\balmost\s*there\b/,
      /\bnext\s*(turn|step|move)\b/,
      /\bdo i\s*turn\b/,
      /\bshould i\s*(keep|go)\s*straight\b/,
    ];

    if (!navPatterns.some(p => p.test(lower))) return null;

    const nextInstr = ctx.instructions[ctx.currentStep + 1];
    const pos = ctx.currentPosition;

    // "how far" queries
    if (/\bhow\s*far\b/.test(lower)) {
      if (lower.includes('destination') || lower.includes('there')) {
        const destCoord = ctx.instructions[ctx.instructions.length - 1].coord;
        const dist = getDistance(pos, destCoord);
        if (dist < 100) return pick([
          "Oh you're super close, less than a hundred meters!",
          "Almost there, like less than a hundred meters, you can probably see it!",
        ]);
        return pick([
          `So you've got about ${Math.round(dist)} meters left. Not too bad, keep it up!`,
          `About ${Math.round(dist)} meters to go. You're making good time though!`,
          `Hmm, looks like roughly ${Math.round(dist)} meters still. We'll get there!`,
        ]);
      }
      if (nextInstr) {
        const dist = getDistance(pos, nextInstr.coord);
        return pick([
          `Your next turn is about ${Math.round(dist)} meters ahead, it's a ${nextInstr.label.toLowerCase()}.`,
          `About ${Math.round(dist)} meters until you need to ${nextInstr.label.toLowerCase()}.`,
        ]);
      }
    }

    // "are we there yet"
    if (/\bare we\s*there\b/.test(lower) || /\balmost\s*there\b/.test(lower)) {
      const destCoord = ctx.instructions[ctx.instructions.length - 1].coord;
      const dist = getDistance(pos, destCoord);
      if (dist < 50) return pick([
        "Yeah! You're basically there, just a few more steps!",
        "Pretty much! Look around, you should be right at it.",
      ]);
      if (dist < 200) return pick([
        "Almost! You're really close now, less than 200 meters.",
        "So close! Just a little bit more, under 200 meters.",
      ]);
      return pick([
        `Not quite yet, about ${Math.round(dist)} meters still. But we're getting there!`,
        `Still got about ${Math.round(dist)} meters. Hang tight, I'll let you know!`,
        `Hmm, about ${Math.round(dist)} meters to go. We'll be there before you know it!`,
      ]);
    }

    // Direction / turn queries ("left or right?", "which way?", "do I turn?")
    if (nextInstr) {
      const dist = getDistance(pos, nextInstr.coord);
      const label = nextInstr.label.toLowerCase();
      return pick([
        `So your next move is to ${label}, it's about ${Math.round(dist)} meters ahead. For now just keep going straight.`,
        `You're gonna ${label} in about ${Math.round(dist)} meters. Just stay on this path for now.`,
        `Coming up you'll need to ${label}, like ${Math.round(dist)} meters from here. I'll remind you when it's time.`,
      ]);
    }

    // On last step
    const currentInstr = ctx.instructions[ctx.currentStep];
    if (currentInstr && currentInstr.label.includes('Arrive')) {
      return pick([
        "You're right at your destination! Look around, this should be it.",
        "This is it! You've arrived. Nice work getting here safely!",
      ]);
    }

    return pick([
      "Just keep going straight for now, I'll tell you when there's a turn coming up.",
      "You're good, just keep heading the same way. I've got my eye on the route.",
      "Straight ahead for now! I'll give you a heads up before any turns.",
    ]);
  }, []);

  // --- Core call loop ---
  const callLoop = useCallback(async (isFirst, gen) => {
    if (!callActiveRef.current || gen !== callGenRef.current) return;

    // Speak greeting on first iteration
    if (isFirst) {
      setCallState('speaking');
      const ctx = navContextRef.current;
      if (ctx?.isNavigating) {
        await speak(pick([
          "Hey! I can see you're already moving. I'll keep an eye on your route and let you know about turns. What's going on?",
          "Oh hey, looks like you're already on your way! I'm here, I'll watch the turns for you. Anything you wanna talk about?",
          "Hey there! You're already navigating, nice. I'll jump in when there's a turn coming up. What's up?",
        ]));
      } else {
        await speak(pick([
          "Hey! So, where are you headed? Just tell me your start and end and I'll figure out the safest way to get you there.",
          "Hey there! Tell me where you wanna go and I'll find you a safe route. Like, just say something like walk from Millennium Park to Navy Pier.",
          "Hi! I'm here to help you get around Chicago safely. Where are you going tonight?",
        ]));
      }
      if (!callActiveRef.current) return;
      await drainTurnAlert();
      if (!callActiveRef.current) return;
    }

    // Drain any pending turn alert before listening
    await drainTurnAlert();
    if (!callActiveRef.current) return;

    // Listen
    setCallState('listening');
    setTranscript('');
    let userText = '';
    try {
      userText = await listen();
    } catch {
      if (callActiveRef.current) {
        setCallState('speaking');
        await speak(pick([
          "Sorry, I didn't catch that. Could you say it again?",
          "Hmm, I couldn't hear you. Mind repeating that?",
          "I missed that, can you try again?",
        ]));
        if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
      }
      return;
    }

    if (!callActiveRef.current) return;

    // Drain alert after listening
    await drainTurnAlert();
    if (!callActiveRef.current) return;

    userText = userText.trim();
    if (!userText) {
      await new Promise(r => setTimeout(r, 500));
      if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
      return;
    }

    // Check for pending travel mode selection (voice)
    if (pendingRouteRef.current) {
      const lowerCheck = userText.toLowerCase();
      const modeKeywords = {
        'walking': ['walk', 'walking', 'foot', 'on foot'],
        'driving': ['drive', 'driving', 'car'],
        'cycling': ['bike', 'biking', 'cycle', 'cycling', 'bicycle'],
      };
      let selectedMode = null;
      for (const [m, keywords] of Object.entries(modeKeywords)) {
        if (keywords.some(kw => lowerCheck.includes(kw))) {
          selectedMode = m;
          break;
        }
      }
      if (selectedMode) {
        const pending = pendingRouteRef.current;
        pendingRouteRef.current = null;
        setPendingRoute(null);
        setMessages(prev => [...prev, { role: 'user', content: userText }]);
        setCallState('processing');
        try {
          const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: userText,
              pending_parsed: pending,
              selected_travel_mode: selectedMode,
              user_hour: new Date().getHours(),
              user_coords: userCoordsRef.current,
              voice: true
            })
          });
          const data = await res.json();
          if (!callActiveRef.current) return;
          if (data.status === 'success') {
            setMessages(prev => [
              ...prev,
              { role: 'assistant', content: data.ai_summary },
              { role: 'briefing', content: data.safety_briefing, metrics: data.route_data.metrics }
            ]);
            onRouteReceived(data.route_data, data.parsed.start_coords, data.parsed.end_coords, data.parsed.hour, data.parsed.beta, data.parsed.travel_mode);
            setHasRoute(true);
            setCallState('speaking');
            if (data.audio) await speakFromAudio(data.audio, data.audio_mime);
            else await speakLong(data.ai_summary);
          } else {
            setMessages(prev => [...prev, { role: 'assistant', content: data.message || 'Something went wrong.' }]);
            setCallState('speaking');
            await speak(data.message || "Hmm, something went wrong. Let me try that again.");
          }
        } catch {
          setCallState('speaking');
          await speak("Sorry, something went wrong. Can you try again?");
        }
        await drainTurnAlert();
        if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
        return;
      }
      // No travel mode keyword detected — clear pending, handle as normal message
      pendingRouteRef.current = null;
      setPendingRoute(null);
    }

    // Check for end-call voice commands
    const lower = userText.toLowerCase();
    if (lower.includes('end call') || lower.includes('hang up') || lower.includes('stop call') || lower === 'bye' || lower === 'goodbye') {
      setCallState('speaking');
      await speak(pick([
        "Alright, stay safe out there! Talk to you later.",
        "Okay, take care! Let me know if you need anything. Bye!",
        "See ya! Be safe out there, okay?",
      ]));
      endCall();
      return;
    }

    // Check for navigation voice commands (auto-start simulation)
    if (lower.includes('navigate safest') || lower.includes('start safest') || lower.includes('use safest') || lower.includes('safest route') || lower === 'safest') {
      const useSim = lower.includes('sim') || lower.includes('demo');
      setCallState('speaking');
      await speak(pick([
        "Alright, let's do the safe route! I'll guide you through it and give you a heads up before every turn.",
        "Going with the safest option, good call! I'll tell you when to turn, just follow my lead.",
        "On it! Starting the safest route now. Just keep walking and I'll handle the directions.",
      ]));
      if (onStartNavigation) onStartNavigation('safest', useSim ? 'sim' : 'gps');
      if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
      return;
    }
    if (lower.includes('navigate fastest') || lower.includes('start fastest') || lower.includes('use fastest') || lower.includes('fastest route') || lower === 'fastest') {
      const useSim = lower.includes('sim') || lower.includes('demo');
      setCallState('speaking');
      await speak(pick([
        "Speed it is! Taking the fastest route. I'll let you know about every turn.",
        "Alright, fastest route coming up! I'll keep you posted as we go.",
        "Going fast, I like it! Starting navigation now, I'll guide you through.",
      ]));
      if (onStartNavigation) onStartNavigation('fastest', useSim ? 'sim' : 'gps');
      if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
      return;
    }
    // Generic navigation commands (no safest/fastest specified) — default to safest
    if (hasRouteRef.current && (/\b(start|begin|let'?s?\s*(start|go|begin)|navigate|navigation)\b/.test(lower)) &&
        !lower.includes('safest') && !lower.includes('fastest')) {
      const useSim = lower.includes('sim') || lower.includes('demo');
      setCallState('speaking');
      await speak(pick([
        "Let's go! I'll start you on the safest route. I'll tell you about every turn along the way.",
        "Starting navigation on the safest route! Follow my lead, I've got you.",
        "Alright, let's do this! Taking the safe route. I'll guide you through it.",
      ]));
      if (onStartNavigation) onStartNavigation('safest', useSim ? 'sim' : 'gps');
      if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
      return;
    }

    // Check for local navigation question (instant, no backend needed)
    const navAnswer = handleNavQuestion(userText);
    if (navAnswer) {
      setMessages(prev => [...prev, { role: 'user', content: userText }]);
      setMessages(prev => [...prev, { role: 'assistant', content: navAnswer }]);
      setCallState('speaking');
      await speak(navAnswer);
      await drainTurnAlert();
      if (callActiveRef.current && gen === callGenRef.current) callLoop(false, gen);
      return;
    }

    // Process with backend (skip ack TTS — go straight to processing for speed)
    setCallState('processing');
    setMessages(prev => [...prev, { role: 'user', content: userText }]);

    // Build navigation context so the buddy knows where we are
    const ctx = navContextRef.current;
    const navState = (ctx && ctx.isNavigating && ctx.instructions?.length) ? {
      is_navigating: true,
      current_step: ctx.currentStep,
      total_steps: ctx.instructions.length,
      next_turn: ctx.instructions[ctx.currentStep + 1]?.label || null,
      next_turn_dist: ctx.currentPosition && ctx.instructions[ctx.currentStep + 1]?.coord
        ? Math.round(getDistance(ctx.currentPosition, ctx.instructions[ctx.currentStep + 1].coord))
        : null,
      dest_dist: ctx.currentPosition && ctx.instructions[ctx.instructions.length - 1]?.coord
        ? Math.round(getDistance(ctx.currentPosition, ctx.instructions[ctx.instructions.length - 1].coord))
        : null,
    } : null;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: userText,
          user_hour: new Date().getHours(),
          nav_state: navState,
          user_coords: userCoordsRef.current,
          voice: true  // Request inline TTS audio (eliminates separate /tts round trip)
        })
      });
      const data = await res.json();

      if (!callActiveRef.current) return;

      if (data.status === 'success') {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: data.ai_summary },
          { role: 'briefing', content: data.safety_briefing, metrics: data.route_data.metrics }
        ]);
        onRouteReceived(
          data.route_data,
          data.parsed.start_coords,
          data.parsed.end_coords,
          data.parsed.hour,
          data.parsed.beta,
          data.parsed.travel_mode
        );
        setHasRoute(true);

        // Play inline audio if available, otherwise fall back to separate TTS call
        setCallState('speaking');
        if (data.audio) {
          await speakFromAudio(data.audio, data.audio_mime);
        } else {
          await speakLong(data.ai_summary);
        }
        if (!callActiveRef.current) return;
        await drainTurnAlert();
      } else if (data.status === 'need_travel_mode') {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
        pendingRouteRef.current = data.pending_parsed;
        setPendingRoute(data.pending_parsed);
        setCallState('speaking');
        if (data.audio) {
          await speakFromAudio(data.audio, data.audio_mime);
        } else {
          await speak(data.message);
        }
      } else if (data.status === 'chat') {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
        setCallState('speaking');
        if (data.audio) {
          await speakFromAudio(data.audio, data.audio_mime);
        } else {
          await speak(data.message);
        }
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message, isError: true }]);
        setCallState('speaking');
        await speak(data.message || pick([
          "Hmm, I'm not sure I understood that. Could you tell me like, where you're starting from and where you wanna go?",
          "I didn't quite get that. Try something like, walk from Millennium Park to Navy Pier.",
          "Sorry, I'm a bit confused. Can you give me your starting point and destination?",
        ]));
      }
    } catch {
      setCallState('speaking');
      await speak(pick([
        "Ugh, I can't connect to the server right now. Can you try again in a sec?",
        "Hmm, something went wrong on my end. Let's try that again.",
        "Sorry about that, the connection dropped. Mind saying that again?",
      ]));
    }

    await drainTurnAlert();
    if (callActiveRef.current && gen === callGenRef.current) {
      callLoop(false, gen);
    }
  }, [speak, speakLong, speakFromAudio, listen, onRouteReceived, onStartNavigation, drainTurnAlert, handleNavQuestion]);

  // --- Interrupt: tap orb to stop speaking and jump to listening ---
  const handleInterrupt = useCallback(() => {
    if (callState === 'speaking') {
      stopCurrentAudio();
    }
  }, [callState, stopCurrentAudio]);

  // --- Start / End call ---
  const startCall = () => {
    if (!SpeechRecognition) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Voice is not supported in this browser. Please use Chrome.',
        isError: true
      }]);
      return;
    }
    // Create & unlock a persistent Audio element on this user gesture (fixes mobile autoplay)
    if (!sharedAudioRef.current) {
      sharedAudioRef.current = new Audio();
    }
    const audio = sharedAudioRef.current;
    // Play a tiny silent WAV to unlock audio playback on iOS/Android
    audio.src = 'data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA';
    audio.play().then(() => { audio.pause(); audio.currentTime = 0; }).catch(() => {});
    // Also unlock browser SpeechSynthesis on mobile
    if (window.speechSynthesis) {
      const utter = new SpeechSynthesisUtterance('');
      utter.volume = 0;
      window.speechSynthesis.speak(utter);
    }
    setCallActive(true);
    callActiveRef.current = true;
    const gen = ++callGenRef.current;
    callLoop(true, gen);
  };

  const endCall = () => {
    callActiveRef.current = false;
    setCallActive(false);
    setCallState('idle');
    setTranscript('');
    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch { /* ignore */ }
    }
    stopCurrentAudio();
    // Clean up shared audio element
    if (sharedAudioRef.current) {
      sharedAudioRef.current.removeAttribute('src');
      sharedAudioRef.current.load();
    }
  };

  // --- Travel mode selection (text chat) ---
  const handleTravelModeSelect = async (mode) => {
    if (!pendingRoute || isLoading) return;
    const modeLabel = mode === 'walking' ? '🚶 Walking' : mode === 'driving' ? '🚗 Driving' : '🚴 Cycling';
    const pending = pendingRoute;
    setPendingRoute(null);
    setMessages(prev => [...prev, { role: 'user', content: modeLabel }]);
    setIsLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: `I'll be ${mode}`,
          pending_parsed: pending,
          selected_travel_mode: mode,
          user_hour: new Date().getHours(),
          user_coords: userCoords || null
        })
      });
      const data = await res.json();

      if (data.status === 'success') {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: data.ai_summary },
          { role: 'briefing', content: data.safety_briefing, metrics: data.route_data.metrics }
        ]);
        onRouteReceived(data.route_data, data.parsed.start_coords, data.parsed.end_coords, data.parsed.hour, data.parsed.beta, data.parsed.travel_mode);
        setHasRoute(true);
      } else if (data.status === 'chat') {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message, isError: true }]);
      }
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Could not connect to the backend.', isError: true }]);
    }
    setIsLoading(false);
  };

  // --- Text chat (non-voice) ---
  const sendMessage = async (text) => {
    const userMsg = (text || input).trim();
    if (!userMsg || isLoading) return;

    setInput('');
    setShowExamples(false);
    setPendingRoute(null);
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setIsLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg, user_hour: new Date().getHours(), nav_state: null, user_coords: userCoords || null })
      });
      const data = await res.json();

      if (data.status === 'success') {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: data.ai_summary },
          { role: 'briefing', content: data.safety_briefing, metrics: data.route_data.metrics }
        ]);
        onRouteReceived(
          data.route_data,
          data.parsed.start_coords,
          data.parsed.end_coords,
          data.parsed.hour,
          data.parsed.beta,
          data.parsed.travel_mode
        );
        setHasRoute(true);
      } else if (data.status === 'need_travel_mode') {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
        setPendingRoute(data.pending_parsed);
      } else if (data.status === 'chat') {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message }]);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', content: data.message, isError: true }]);
      }
    } catch {
      setMessages(prev => [
        ...prev,
        { role: 'assistant', content: 'Could not connect to the backend. Make sure the Flask server is running on port 5001.', isError: true }
      ]);
    }
    setIsLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // --- Call overlay ---
  if (callActive) {
    const isMobileCall = window.innerWidth <= 768;

    // Mobile: compact call card instead of full-screen overlay
    if (isMobileCall) {
      return (
        <div className="chat-panel">
          <div className="mobile-call-card" onClick={callState === 'speaking' ? handleInterrupt : undefined}>
            <div className={`mobile-call-orb ${callState}`}>
              {callState === 'listening' && '🎙️'}
              {callState === 'processing' && '🧠'}
              {callState === 'speaking' && '🔊'}
              {callState === 'idle' && '📞'}
            </div>
            <div className="mobile-call-info">
              <span className="mobile-call-state">
                {callState === 'listening' && 'Listening...'}
                {callState === 'processing' && 'Thinking...'}
                {callState === 'speaking' && 'Speaking...'}
                {callState === 'idle' && 'Connecting...'}
              </span>
              <span className="mobile-call-timer">{formatCallTime(callDuration)}</span>
            </div>
            <button className="mobile-call-end" onClick={(e) => { e.stopPropagation(); endCall(); }}>
              End
            </button>
          </div>
          {transcript && callState === 'listening' && (
            <div className="mobile-call-transcript">"{transcript}"</div>
          )}
          {hasRoute && onStartNavigation && (
            <div className="call-nav-hint" style={{ marginTop: 8 }}>
              Say "navigate safest" or "navigate fastest"
            </div>
          )}
        </div>
      );
    }

    // Desktop: full-screen call overlay (unchanged)
    return (
      <div className="chat-panel">
        <div className="call-overlay">
          {/* Call header */}
          <div className="call-header">
            <span className="call-label">SafePath Buddy</span>
            <span className="call-sublabel">Powered by Gemini 3</span>
            <span className="call-timer">{formatCallTime(callDuration)}</span>
          </div>

          {/* Animated orb */}
          <div className={`call-orb ${callState}`} onClick={callState === 'speaking' ? handleInterrupt : undefined}>
            <div className="call-orb-inner">
              {callState === 'listening' && '🎙️'}
              {callState === 'processing' && '🧠'}
              {callState === 'speaking' && '🔊'}
              {callState === 'idle' && '📞'}
            </div>
            <div className="call-ring ring-1"></div>
            <div className="call-ring ring-2"></div>
            <div className="call-ring ring-3"></div>
          </div>

          {/* State label */}
          <div className="call-state-label">
            {callState === 'listening' && 'Listening...'}
            {callState === 'processing' && 'Thinking...'}
            {callState === 'speaking' && 'Speaking...'}
            {callState === 'idle' && 'Connecting...'}
          </div>

          {callState === 'speaking' && (
            <div className="call-interrupt-hint">Tap orb to interrupt</div>
          )}

          {/* Live transcript */}
          {transcript && callState === 'listening' && (
            <div className="call-transcript">"{transcript}"</div>
          )}

          {/* Recent message preview */}
          {messages.length > 1 && (
            <div className="call-last-msg">
              {messages[messages.length - 1].content?.slice(0, 120)}
              {messages[messages.length - 1].content?.length > 120 ? '...' : ''}
            </div>
          )}

          {/* Nav buttons in call mode */}
          {hasRoute && onStartNavigation && (
            <div className="call-nav-hint">
              Say "navigate safest" or "navigate fastest"
            </div>
          )}

          {/* End call button */}
          <button className="call-end-btn" onClick={endCall}>
            <span className="call-end-icon">📞</span>
            End Call
          </button>
        </div>
      </div>
    );
  }

  // --- Normal text chat UI ---
  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.map((msg, i) => (
          <ChatMessage key={i} message={msg} />
        ))}
        {isLoading && <TypingIndicator />}
        <div ref={messagesEndRef} />
      </div>

      {showExamples && messages.length <= 1 && (
        <div className="chat-examples">
          <div className="chat-examples-title">Try these</div>
          {EXAMPLE_PROMPTS.map((prompt, i) => (
            <button
              key={i}
              className="chat-example-btn"
              onClick={() => sendMessage(prompt)}
            >
              "{prompt}"
            </button>
          ))}
        </div>
      )}

      {pendingRoute && !isLoading && (
        <div className="travel-mode-selector">
          <div className="travel-mode-label">How are you traveling?</div>
          <div className="travel-mode-buttons">
            <button className="travel-mode-btn" onClick={() => handleTravelModeSelect('walking')}>
              🚶 Walking
            </button>
            <button className="travel-mode-btn" onClick={() => handleTravelModeSelect('driving')}>
              🚗 Driving
            </button>
            <button className="travel-mode-btn" onClick={() => handleTravelModeSelect('cycling')}>
              🚴 Cycling
            </button>
          </div>
        </div>
      )}

      {hasRoute && onStartNavigation && (
        <div className="chat-nav-buttons">
          <button className="chat-nav-btn safest" onClick={() => onStartNavigation('safest')}>
            🛡️ Navigate Safest Route
          </button>
          <button className="chat-nav-btn fastest" onClick={() => onStartNavigation('fastest')}>
            ⚡ Navigate Fastest Route
          </button>
        </div>
      )}

      <div className="chat-input-bar">
        <button
          className="voice-call-btn"
          onClick={startCall}
          disabled={isLoading}
          title="Start voice call"
        >
          📞
        </button>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message your walking buddy..."
          disabled={isLoading}
        />
        <button
          className="chat-send-btn"
          onClick={() => sendMessage()}
          disabled={isLoading || !input.trim()}
          title="Send message"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
        </button>
      </div>
    </div>
  );
}
