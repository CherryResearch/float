import axios from "axios";

// Base API URL resolves via Vite proxy during development
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

export const createContext = async (systemPrompt, messages = [], tools = [], metadata = {}) => {
  try {
    const response = await axios.post(`${API_BASE_URL}/context/`, {
      system_prompt: systemPrompt,
      messages,
      tools,
      metadata,
    });
    return response.data;
  } catch (error) {
    console.error("Error creating context:", error);
    throw error;
  }
};

export const addMessage = async (contextId, role, content, metadata = {}) => {
  try {
    const response = await axios.post(`${API_BASE_URL}/context/${contextId}/message`, {
      role,
      content,
      metadata,
    });
    return response.data;
  } catch (error) {
    console.error("Error adding message:", error);
    throw error;
  }
};

export const addTool = async (contextId, name, description, parameters, metadata = {}) => {
  try {
    const response = await axios.post(`${API_BASE_URL}/context/${contextId}/tool`, {
      name,
      description,
      parameters,
      metadata,
    });
    return response.data;
  } catch (error) {
    console.error("Error adding tool:", error);
    throw error;
  }
};

export const getContext = async (contextId) => {
  try {
    const response = await axios.get(`${API_BASE_URL}/context/${contextId}`);
    return response.data;
  } catch (error) {
    console.error("Error retrieving context:", error);
    throw error;
  }
};

export const clearContext = async (contextId) => {
  try {
    const response = await axios.delete(`${API_BASE_URL}/context/${contextId}`);
    return response.data;
  } catch (error) {
    console.error("Error clearing context:", error);
    throw error;
  }
}; 
export const startVoiceSession = async (identity, room = "float") => {
  try {
    const response = await axios.post(`${API_BASE_URL}/voice/connect`, { identity, room });
    return response.data;
  } catch (error) {
    console.error("Error starting voice session:", error);
    throw error;
  }
};
