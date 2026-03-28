import React from "react";
import { Link } from "react-router-dom";

const NotFound = () => {
  return (
    <div className="center-rail" style={{ padding: 24 }}>
      <h2>Page not found</h2>
      <p>Try one of these:</p>
      <ul>
        <li><Link to="/">chat</Link></li>
        <li><Link to="/knowledge">knowledge</Link></li>
        <li><Link to="/settings">settings</Link></li>
      </ul>
    </div>
  );
};

export default NotFound;

