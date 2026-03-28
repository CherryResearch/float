import React, { useContext } from "react";
import { GlobalContext } from "../main";

const ContextManager = () => {
  const { state, setState } = useContext(GlobalContext);

  const handleClearContext = () => {
    setState({ ...state, context: null });
  };

  return (
    <div>
      <h2>Model Context</h2>
      {state.context ? (
        <pre>{JSON.stringify(state.context, null, 2)}</pre>
      ) : (
        <p>No context available.</p>
      )}
      <button onClick={handleClearContext}>Clear Context</button>
    </div>
  );
};

export default ContextManager; 