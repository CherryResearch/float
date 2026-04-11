import React, { useState, useEffect, useContext, useRef } from 'react';
import axios from 'axios';
import { GlobalContext } from '../main';
import { List, ListItem, ListItemText, Button, Typography, Paper, Grid, CircularProgress, Chip } from '@mui/material';
import { resolveLocalCatalogModelId } from "../utils/modelUtils";

const ModelManager = () => {
  const { state } = useContext(GlobalContext);
  const [availableModels, setAvailableModels] = useState([]);
  const [supportedModels, setSupportedModels] = useState([]);
  const [modelInfo, setModelInfo] = useState({});
  const [modelStatus, setModelStatus] = useState({});
  const [loading, setLoading] = useState(true);
  const [includeCacheUnfiltered, setIncludeCacheUnfiltered] = useState(false);
  const [downloading, setDownloading] = useState({});
  const bcRef = useRef(null);

  useEffect(() => {
    bcRef.current = new BroadcastChannel('model-download');
    return () => bcRef.current && bcRef.current.close();
  }, []);

  const fetchAvailableModels = async (unfiltered = includeCacheUnfiltered) => {
    try {
      const params = unfiltered ? { params: { include_cache_unfiltered: true } } : undefined;
      const response = await axios.get('/api/transformers/models', params);
      setAvailableModels(response.data.models);
    } catch (error) {
      console.error('Error fetching available models:', error);
    }
  };

  const fetchSupportedModels = async () => {
    try {
      const response = await axios.get('/api/models/downloadable');
      setSupportedModels(response.data.models);
      return response.data.models;
    } catch (error) {
      console.error('Error fetching supported models:', error);
      return [];
    }
  };

  const fetchModelInfo = async (modelName) => {
    try {
      const response = await axios.get(
        `/api/models/info/${encodeURIComponent(resolveLocalCatalogModelId(modelName))}`,
      );
      setModelInfo(prev => ({ ...prev, [modelName]: response.data }));
    } catch (error) {
      console.error(`Error fetching info for model ${modelName}:`, error);
    }
  };

  const verifyModel = async (modelName) => {
    try {
      const response = await axios.get(
        `/api/models/verify/${encodeURIComponent(resolveLocalCatalogModelId(modelName))}`,
      );
      setModelStatus(prev => ({ ...prev, [modelName]: response.data }));
    } catch (error) {
      console.error(`Error verifying model ${modelName}:`, error);
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchSupportedModels().then(models => {
      Promise.all([
        fetchAvailableModels(),
        ...models.map(fetchModelInfo),
        ...models.map(verifyModel)
      ]).finally(() => setLoading(false));
    });
  }, []);

  const handleDownloadOrResume = async (modelName) => {
    setDownloading(prev => ({ ...prev, [modelName]: true }));
    try {
      // Schedule a background job so the global DownloadTray can show progress
      const r = await axios.post('/api/models/jobs', { model: modelName });
      const job = r.data?.job;
      if (job?.id) {
        const key = 'modelDownloadJobs';
        const list = JSON.parse(localStorage.getItem(key) || '[]');
        const entry = {
          id: job.id,
          model: modelName,
          path: job.path,
          status: job.status,
          total: job.total || 0,
          downloaded: r.data?.downloaded || 0,
          percent: r.data?.percent || 0,
        };
        const next = [entry, ...list.filter((j) => j.id !== job.id)];
        localStorage.setItem(key, JSON.stringify(next));
        if (bcRef.current)
          bcRef.current.postMessage({ type: 'jobs:update', payload: next });
      }
      // Refresh availability list (may still be downloading, but some files could exist)
      await fetchAvailableModels();
      await verifyModel(modelName);
    } catch (error) {
      console.error(`Error downloading model ${modelName}:`, error);
    }
    setDownloading(prev => ({ ...prev, [modelName]: false }));
  };

  const handleDelete = async (modelName) => {
    try {
      await axios.delete(
        `/api/models/${encodeURIComponent(resolveLocalCatalogModelId(modelName))}`,
      );
      await fetchAvailableModels();
      await verifyModel(modelName);
    } catch (error) {
      console.error(`Error deleting model ${modelName}:`, error);
    }
  };

  const formatBytes = (bytes, decimals = 2) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
  }

  const getButtonState = (model) => {
    const status = modelStatus[model];
    if (downloading[model]) {
      return { text: <CircularProgress size={24} />, disabled: true, style: { backgroundColor: 'grey' } };
    }
    if (status?.verified) {
      return { text: 'Downloaded', disabled: true, style: { backgroundColor: 'darkgreen', color: 'white' } };
    }
    if (status?.exists && status?.installed_bytes > 0) {
      return { text: 'Resume', disabled: false, style: { backgroundColor: 'orange', color: 'white' } };
    }
    return { text: 'Download', disabled: false, style: { backgroundColor: 'lightgreen', color: 'white' } };
  };

  if (loading) {
    return <CircularProgress />;
  }

  return (
    <Paper style={{ padding: '20px', margin: '20px' }}>
      <Typography variant="h4" gutterBottom>Model Manager</Typography>
      <Grid container spacing={4}>
        <Grid item xs={12} md={6}>
          <Typography variant="h6">Available Models</Typography>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <input
              type="checkbox"
              checked={includeCacheUnfiltered}
              id="includeCacheUnfiltered"
              onChange={(e) => {
                const next = e.target.checked;
                setIncludeCacheUnfiltered(next);
                fetchAvailableModels(next);
              }}
            />
            <label htmlFor="includeCacheUnfiltered">
              Show all Hugging Face cache entries (includes utility/noisy models)
            </label>
          </div>
          <List>
            {availableModels.map((model) => (
              <ListItem key={model}>
                <ListItemText primary={model} />
                <Button variant="contained" style={{backgroundColor: 'darkred', color: 'white'}} onClick={() => handleDelete(model)}>Delete</Button>
              </ListItem>
            ))}
          </List>
        </Grid>
        <Grid item xs={12} md={6}>
          <Typography variant="h6">Downloadable Models</Typography>
          <List>
            {supportedModels.map((model) => {
              const buttonState = getButtonState(model);
              return (
                <ListItem key={model}>
                  <ListItemText primary={model} secondary={modelInfo[model] ? formatBytes(modelInfo[model].size) : '...'} />
                  <Button
                    variant="contained"
                    style={buttonState.style}
                    onClick={() => handleDownloadOrResume(model)}
                    disabled={buttonState.disabled}
                  >
                    {buttonState.text}
                  </Button>
                </ListItem>
              );
            })}
          </List>
        </Grid>
      </Grid>
    </Paper>
  );
};

export default ModelManager;
