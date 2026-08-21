import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/tokens.css";
import "./styles/components.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("index.html is missing <div id=\"root\">");
}

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
