import { Component, type ErrorInfo, type ReactNode } from "react";

/** If a page crashes, show what happened instead of a blank screen. Resets when you
 *  navigate away (the key on the router location is not needed: the buttons reload). */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("Page crashed:", error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="card narrow">
        <h2>Something went wrong on this page</h2>
        <p className="muted">Your booking and payments are not affected. Try again, or go back to the home page.</p>
        <p className="error-text small">{this.state.error.message}</p>
        <div className="actions">
          <button className="primary" onClick={() => location.reload()}>Reload</button>
          <a className="btn" href="/">Home</a>
        </div>
      </div>
    );
  }
}
