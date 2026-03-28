# Float Frontend

This is the frontend for the Float project, a latent-thought based learning agent designed to run on locally managed hardware with a focus on privacy.

## Overview

The frontend is built using **React**, providing a modern and responsive user interface for interacting with the backend services. Previous Vue components have been removed. It includes components for managing model context, adding messages and tools, displaying chat interactions, and a simple data visualization view powered by **D3**.

## Project Structure

```
frontend/
│
├── src/
│   ├── components/
│   │   ├── forms/
│   │   │   ├── MessageForm.jsx
│   │   │   ├── ToolForm.jsx
│   │   ├── ContextManager.jsx
│   │   ├── HistorySidebar.jsx
│   │   ├── AgentConsole.jsx
│   │   ├── Chat.jsx
│   │   ├── App.jsx
│   ├── utils/
│   │   ├── apiClient.js
│   ├── styles/
│   ├── main.jsx
│
├── package.json
├── README.md
```

## Setup Instructions

1. **Install Dependencies**:
   ```bash
   npm install
   ```
   This installs React, D3, and all other packages required by the UI.

2. **Run the Development Server**:
   ```bash
   npm start
   ```

3. **Build for Production**:
   ```bash
   npm run build
   ```

### Configuration

The frontend reads `VITE_API_BASE_URL` from your `.env` file to determine where
API requests should be sent. Set this variable to the backend URL (default
`http://localhost:8000`).

## Key Features

- **Model Context Management**: Manage and display the current model context.
- **Message and Tool Forms**: Add messages and tools to the context.
- **Responsive UI**: Modern design with React.
- **Data Visualization**: The `Visualization` component renders demo charts using D3.

For more information, refer to the backend documentation and the main project README.
