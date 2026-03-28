import React, { useState, useContext } from "react";
import { GlobalContext } from "../main";

const MessageForm = () => {
  const { state, setState } = useContext(GlobalContext);
  const [role, setRole] = useState("user");
  const [content, setContent] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    const newMessage = { role, content, metadata: { timestamp: Date.now() } };
    const updatedContext = {
      ...state.context,
      messages: [...(state.context?.messages || []), newMessage],
    };
    setState({ ...state, context: updatedContext });
    setContent(""); // Clear the input field
  };

  return (
    <form onSubmit={handleSubmit}>
      <label>
        Role:
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="user">User</option>
          <option value="assistant">Assistant</option>
        </select>
      </label>
      <label>
        Message:
        <input
          type="text"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          required
        />
      </label>
      <button type="submit">Add Message</button>
    </form>
  );
};

export default MessageForm; 