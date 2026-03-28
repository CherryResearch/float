import React, { useState, useContext } from "react";
import { GlobalContext } from "../main";

const ToolForm = () => {
  const { state, setState } = useContext(GlobalContext);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [parameters, setParameters] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    const newTool = {
      name,
      description,
      parameters: JSON.parse(parameters || "{}"),
      metadata: { timestamp: Date.now() },
    };
    const updatedContext = {
      ...state.context,
      tools: [...(state.context?.tools || []), newTool],
    };
    setState({ ...state, context: updatedContext });
    setName("");
    setDescription("");
    setParameters("");
  };

  return (
    <form onSubmit={handleSubmit}>
      <label>
        Tool Name:
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </label>
      <label>
        Description:
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          required
        />
      </label>
      <label>
        Parameters (JSON):
        <textarea
          value={parameters}
          onChange={(e) => setParameters(e.target.value)}
          placeholder='{"key": "value"}'
        />
      </label>
      <button type="submit">Add Tool</button>
    </form>
  );
};

export default ToolForm; 