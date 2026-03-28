import React from "react";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    // Placeholder: could log to backend later
    // console.error("ErrorBoundary", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="center-rail" style={{ padding: 16 }}>
          <h2>Something went wrong.</h2>
          <pre style={{ whiteSpace: "pre-wrap" }}>{String(this.state.error)}</pre>
          {this.props.fallback}
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;

