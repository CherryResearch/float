// config.js

const CONFIG = {
    API_BASE_URL: '/api', // Proxy URL for backend APIs (defined in vite.config.js)
    MODELS_BASE_PATH: 'data/models', // Base path for local models
  
    // LLM Configuration
    LLM: {
      MODES: ['api', 'local', 'server'], // Available LLM modes
      DEFAULT_MODE: 'api',               // Default LLM mode
      ENDPOINTS: {
        GENERATE: '/llm/generate',       // LLM generation endpoint
        // Dynamic server endpoints are deprecated and hidden from UI
      },
    },
  
    // Other Backend Endpoints
    ENDPOINTS: {
      HEALTH: '/health',   // Backend health check
      TOOLS: '/tools/',    // List available tools
      MEMORY_UPDATE: '/memory/update/', // Memory update endpoint
    },
  
    // Frontend Settings
    FRONTEND: {
      ENABLE_VISUALIZATIONS: true, // Toggle visualizations tab
      DEFAULT_THEME: 'light',      // Default theme: light or dark
    },
  };
  
  export default CONFIG;
  
