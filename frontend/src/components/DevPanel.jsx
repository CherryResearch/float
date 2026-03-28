import React, { useEffect, useRef, useState } from "react";
import axios from "axios";
import Box from "@mui/material/Box";
import Tabs from "@mui/material/Tabs";
import Tab from "@mui/material/Tab";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";

const DevPanel = () => {
  const [prompts, setPrompts] = useState([]);
  const [results, setResults] = useState({});
  const [logs, setLogs] = useState([]);
  const [tab, setTab] = useState(0);
  const logsContainerRef = useRef(null);
  const [logsAtBottom, setLogsAtBottom] = useState(true);

  useEffect(() => {
    axios
      .get("/api/test-prompts")
      .then((res) => setPrompts(res.data.prompts || []))
      .catch((err) => console.error("Failed to load test prompts", err));
  }, []);

  const runPrompt = (name) => {
    axios
      .post(`/api/test-prompts/${name}`)
      .then((res) => {
        setResults((prev) => ({ ...prev, [name]: res.data.response }));
      })
      .catch((err) => {
        const detail = err.response?.data?.detail || "Failed to run";
        setResults((prev) => ({ ...prev, [name]: `Error: ${detail}` }));
      });
  };

  useEffect(() => {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${window.location.host}/api/ws/thoughts`);
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data?.type === "keepalive") return;
        setLogs((prev) => {
          const next = [...prev, data];
          return next.length > 500 ? next.slice(-500) : next;
        });
      } catch (err) {
        console.error("Bad WS data", err);
      }
    };
    ws.onerror = () => ws.close();
    return () => ws.close();
  }, []);

  useEffect(() => {
    if (tab !== 1) return undefined;
    const node = logsContainerRef.current;
    if (!node) return undefined;

    const thresholdPx = 48;
    const update = () => {
      const target = logsContainerRef.current;
      if (!target) return;
      const distanceFromBottom =
        target.scrollHeight - target.scrollTop - target.clientHeight;
      setLogsAtBottom(distanceFromBottom <= thresholdPx);
    };

    update();
    node.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      node.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [tab]);

  useEffect(() => {
    if (tab !== 1) return;
    if (!logsAtBottom) return;
    const node = logsContainerRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [logs, tab, logsAtBottom]);

  return (
    <Box sx={{ p: 2 }}>
      <Tabs value={tab} onChange={(e, v) => setTab(v)} aria-label="dev tabs">
        <Tab label="Prompts" />
        <Tab label="Logs" />
      </Tabs>
      {tab === 0 && (
        <List>
          {prompts.map((name) => (
            <ListItem key={name} alignItems="flex-start">
              <ListItemText
                primary={<button onClick={() => runPrompt(name)}>{name}</button>}
                secondary={results[name] && <pre>{results[name]}</pre>}
              />
            </ListItem>
          ))}
        </List>
      )}
      {tab === 1 && (
        <List
          ref={logsContainerRef}
          sx={{ maxHeight: "70vh", overflow: "auto", pr: 1 }}
        >
          {logs.map((log, idx) => (
            <ListItem key={idx} alignItems="flex-start">
              <ListItemText
                primary={log.type === "tool" ? log.name : log.content}
                secondary={
                  log.type === "tool"
                    ? `args: ${JSON.stringify(log.args || {})} result: ${JSON.stringify(log.result)}`
                    : log.type
                }
              />
            </ListItem>
          ))}
        </List>
      )}
    </Box>
  );
};

export default DevPanel;
